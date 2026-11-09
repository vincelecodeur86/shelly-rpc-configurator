# -*- coding: utf-8 -*-
"""Chiffrement des secrets (mots de passe, clé privée TLS) enregistrés sur disque.

Formats (chaîne préfixée, stockée à la place de la valeur en clair) :
  enc:dpapi:<b64>  Windows : DPAPI (lié au compte Windows de l'utilisateur)
  enc:lk1:<b64>    Linux (ou repli) : clé locale aléatoire (fichier en 0600) + chiffrement HMAC-SHA256
  enc:pp1:<b64>    Export : clé dérivée d'une phrase de passe (PBKDF2-HMAC-SHA256)

Le chiffrement « HMAC-SHA256 » est un chiffrement par flot (HMAC en mode compteur) suivi d'un
HMAC d'authentification (encrypt-then-MAC), réalisable avec la seule bibliothèque standard.
"""
import base64
import hashlib
import hmac
import os
import struct

from . import paths
from .log_setup import get_logger

log = get_logger("secrets")

PREFIX = "enc:"
_DPAPI_ENTROPY = b"ShellyConfigurator/v1"
PBKDF2_ITERATIONS = 100000


class SecretError(Exception):
    """Déchiffrement impossible (mauvaise clé, autre compte, données altérées)."""


def is_encrypted(value):
    return isinstance(value, str) and value.startswith(PREFIX)


# -- chiffrement HMAC-SHA256 (bibliothèque standard) ------------------------------------------
def _subkeys(key):
    return (hmac.new(key, b"enc", hashlib.sha256).digest(),
            hmac.new(key, b"mac", hashlib.sha256).digest())


def _keystream(enc_key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(enc_key, nonce + struct.pack(">Q", counter), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def seal(key, plaintext):
    enc_key, mac_key = _subkeys(key)
    nonce = os.urandom(16)
    stream = _keystream(enc_key, nonce, len(plaintext))
    cipher = bytes(a ^ b for a, b in zip(plaintext, stream))
    tag = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    return nonce + cipher + tag


def unseal(key, blob):
    if len(blob) < 48:
        raise SecretError("Donnée chiffrée tronquée")
    enc_key, mac_key = _subkeys(key)
    nonce, cipher, tag = blob[:16], blob[16:-32], blob[-32:]
    expected = hmac.new(mac_key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise SecretError("Clé incorrecte ou donnée altérée")
    stream = _keystream(enc_key, nonce, len(cipher))
    return bytes(a ^ b for a, b in zip(cipher, stream))


# -- DPAPI (Windows) --------------------------------------------------------------------------
def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf_in = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf_in, ctypes.POINTER(ctypes.c_char)))
    buf_ent = ctypes.create_string_buffer(_DPAPI_ENTROPY, len(_DPAPI_ENTROPY))
    blob_ent = DATA_BLOB(len(_DPAPI_ENTROPY), ctypes.cast(buf_ent, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x01
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    if protect:
        func = crypt32.CryptProtectData
        func.argtypes = [ctypes.POINTER(DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(DATA_BLOB),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        func.restype = wintypes.BOOL
        ok = func(ctypes.byref(blob_in), u"ShellyConfigurator", ctypes.byref(blob_ent),
                  None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    else:
        func = crypt32.CryptUnprotectData
        func.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.POINTER(DATA_BLOB),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
        func.restype = wintypes.BOOL
        ok = func(ctypes.byref(blob_in), None, ctypes.byref(blob_ent),
                  None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out))
    if not ok:
        raise SecretError("DPAPI a échoué (erreur Windows %d)" % ctypes.GetLastError())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


# -- clé locale (Linux) -----------------------------------------------------------------------
def _local_key():
    path = paths.key_file()
    if os.path.exists(path):
        with open(path, "rb") as fh:
            key = fh.read()
        if len(key) == 32:
            return key
        raise SecretError("Fichier de clé invalide : %s" % path)
    key = os.urandom(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key)
    return key


def _b64e(data):
    return base64.b64encode(data).decode("ascii")


def _b64d(text):
    try:
        return base64.b64decode(text.encode("ascii"))
    except (ValueError, TypeError):
        raise SecretError("Donnée chiffrée illisible")


def encrypt_local(text):
    """Chiffre pour le poste/compte courant. Une chaîne vide reste vide."""
    if not text:
        return ""
    data = text.encode("utf-8")
    if paths.is_windows():
        try:
            return PREFIX + "dpapi:" + _b64e(_dpapi(data, True))
        except Exception as exc:  # DPAPI indisponible : repli sur la clé locale
            log.warning("DPAPI indisponible (%s), utilisation de la clé locale", exc)
    return PREFIX + "lk1:" + _b64e(seal(_local_key(), data))


def decrypt_local(value):
    if not is_encrypted(value):
        return value or ""
    scheme, _, payload = value[len(PREFIX):].partition(":")
    blob = _b64d(payload)
    if scheme == "dpapi":
        if not paths.is_windows():
            raise SecretError("Secret chiffré par DPAPI (Windows) : illisible sur ce système")
        return _dpapi(blob, False).decode("utf-8")
    if scheme == "lk1":
        return unseal(_local_key(), blob).decode("utf-8")
    if scheme == "pp1":
        raise SecretError("Secret protégé par une phrase de passe (fichier d'export)")
    raise SecretError("Format de secret inconnu : %s" % scheme)


def _passphrase_key(passphrase, salt):
    return hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, PBKDF2_ITERATIONS, 32)


def encrypt_with_passphrase(text, passphrase):
    if not text:
        return ""
    salt = os.urandom(16)
    return PREFIX + "pp1:" + _b64e(salt + seal(_passphrase_key(passphrase, salt), text.encode("utf-8")))


def decrypt_with_passphrase(value, passphrase):
    if not is_encrypted(value):
        return value or ""
    scheme, _, payload = value[len(PREFIX):].partition(":")
    if scheme != "pp1":
        raise SecretError("Ce secret n'est pas protégé par phrase de passe")
    blob = _b64d(payload)
    salt, sealed = blob[:16], blob[16:]
    try:
        return unseal(_passphrase_key(passphrase, salt), sealed).decode("utf-8")
    except SecretError:
        raise SecretError("Phrase de passe incorrecte")
