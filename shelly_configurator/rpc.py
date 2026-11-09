# -*- coding: utf-8 -*-
"""Client JSON-RPC pour les Shelly Gen2+ (HTTP POST /rpc, authentification digest SHA-256).

Uniquement la bibliothèque standard : urllib de Python 3.8 ne sait pas faire du digest SHA-256,
l'en-tête Authorization est donc calculé ici.
"""
import hashlib
import http.client
import itertools
import json
import os
import re
import socket
import threading
import urllib.error
import urllib.request

from .log_setup import get_logger

log = get_logger("rpc")

USERNAME = "admin"


class ShellyError(Exception):
    """Erreur générique de communication avec le Shelly."""


class ShellyConnectionError(ShellyError):
    """Le Shelly est injoignable (réseau, délai dépassé, connexion coupée)."""


class ShellyAuthError(ShellyError):
    """Mot de passe absent ou refusé."""


class ShellyRpcError(ShellyError):
    """Le Shelly a répondu par une erreur JSON-RPC."""

    def __init__(self, method, code, message):
        self.method = method
        self.code = code
        self.message = message
        ShellyError.__init__(self, "%s a échoué (code %s) : %s" % (method, code, message))


SENSITIVE_KEYS = ("pass", "password", "ha1")


def redact(obj):
    """Copie de obj sans les secrets, pour les journaux."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            if key in SENSITIVE_KEYS and value not in (None, ""):
                out[key] = "***"
            elif key == "data" and isinstance(value, str):
                out[key] = "<%d caractères>" % len(value)
            else:
                out[key] = redact(value)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    return obj


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_PARAM_RE = re.compile(r'(\w+)\s*=\s*(?:"([^"]*)"|([^,\s]+))')


def parse_www_authenticate(header):
    """Analyse un en-tête 'WWW-Authenticate: Digest ...' et renvoie ses paramètres (dict) ou None."""
    if not header:
        return None
    scheme, _, rest = header.strip().partition(" ")
    if scheme.lower() != "digest":
        return None
    params = {}
    for match in _PARAM_RE.finditer(rest):
        value = match.group(2) if match.group(2) is not None else match.group(3)
        params[match.group(1).lower()] = value
    return params


def normalize_host(host):
    host = (host or "").strip()
    for prefix in ("http://", "https://"):
        if host.lower().startswith(prefix):
            host = host[len(prefix):]
    return host.split("/")[0]


def _try_json(raw):
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return None


def _safe_read(http_error):
    try:
        return http_error.read()
    except Exception:  # pragma: no cover - lecture du corps d'erreur impossible
        return b""
    finally:
        try:
            http_error.close()
        except Exception:  # pragma: no cover
            pass


class ShellyClient(object):
    """Appels RPC vers un Shelly. Thread-safe pour un usage séquentiel."""

    def __init__(self, host, password=None, timeout=10.0):
        self.host = normalize_host(host)
        if not self.host:
            raise ValueError("Adresse du Shelly vide")
        self.url = "http://%s/rpc" % self.host
        self.timeout = timeout
        self._password = password or None
        self._challenge = None
        self._nc = 0
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    @property
    def password(self):
        return self._password

    def set_password(self, password):
        with self._lock:
            self._password = password or None

    # -- authentification -------------------------------------------------------------------
    def _authorization_header(self):
        challenge = self._challenge
        algorithm = (challenge.get("algorithm") or "SHA-256").upper()
        if algorithm != "SHA-256":
            raise ShellyAuthError("Algorithme d'authentification non supporté : %s" % algorithm)
        realm = challenge.get("realm", "")
        nonce = challenge.get("nonce", "")
        self._nc += 1
        nc = "%08x" % self._nc
        cnonce = os.urandom(8).hex()
        qop = "auth"
        ha1 = sha256_hex("%s:%s:%s" % (USERNAME, realm, self._password))
        ha2 = sha256_hex("POST:/rpc")
        response = sha256_hex(":".join([ha1, nonce, nc, cnonce, qop, ha2]))
        return ('Digest username="%s", realm="%s", nonce="%s", uri="/rpc", algorithm=SHA-256, '
                'response="%s", qop=%s, nc=%s, cnonce="%s"'
                % (USERNAME, realm, nonce, response, qop, nc, cnonce))

    # -- appel RPC --------------------------------------------------------------------------
    def call(self, method, params=None, timeout=None):
        with self._lock:
            return self._call(method, params, timeout)

    def _call(self, method, params, timeout):
        payload = {"id": next(self._ids), "method": method}
        if params is not None:
            payload["params"] = params
        body = json.dumps(payload).encode("utf-8")
        log.debug("-> %s %s %s", self.host, method,
                  json.dumps(redact(params), ensure_ascii=False) if params is not None else "")
        renewed = False
        for _attempt in range(3):
            headers = {"Content-Type": "application/json"}
            if self._challenge is not None and self._password is not None:
                headers["Authorization"] = self._authorization_header()
            request = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                    raw = resp.read()
            except urllib.error.HTTPError as exc:
                raw = _safe_read(exc)
                if exc.code == 401:
                    challenge = parse_www_authenticate(exc.headers.get("WWW-Authenticate"))
                    if challenge is None:
                        raise ShellyAuthError("Authentification requise par le Shelly %s." % self.host)
                    self._challenge = challenge
                    self._nc = 0
                    if self._password is None:
                        raise ShellyAuthError("Le Shelly %s est protégé par un mot de passe." % self.host)
                    if renewed:
                        raise ShellyAuthError("Mot de passe refusé par le Shelly %s." % self.host)
                    renewed = True
                    continue
                data = _try_json(raw)
                if isinstance(data, dict) and data.get("error"):
                    raise self._rpc_error(method, data["error"])
                raise ShellyConnectionError("Erreur HTTP %s du Shelly %s pour %s." % (exc.code, self.host, method))
            except (urllib.error.URLError, socket.timeout, http.client.HTTPException, OSError) as exc:
                reason = getattr(exc, "reason", None) or exc
                raise ShellyConnectionError("Impossible de joindre le Shelly %s : %s" % (self.host, reason))
            data = _try_json(raw)
            if not isinstance(data, dict):
                raise ShellyError("Réponse illisible du Shelly %s pour %s." % (self.host, method))
            if data.get("error"):
                raise self._rpc_error(method, data["error"])
            log.debug("<- %s %s OK", self.host, method)
            return data.get("result")
        raise ShellyAuthError("Échec d'authentification auprès du Shelly %s." % self.host)

    def _rpc_error(self, method, error):
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else str(error)
        if code == 401:
            return ShellyAuthError("Mot de passe refusé par le Shelly %s." % self.host)
        log.warning("Erreur RPC %s sur %s : %s %s", method, self.host, code, message)
        return ShellyRpcError(method, code, message)

    # -- raccourcis -------------------------------------------------------------------------
    def get_device_info(self, timeout=None):
        return self.call("Shelly.GetDeviceInfo", timeout=timeout)


def http_get_json(host, path="/shelly", timeout=2.0):
    """GET HTTP sans authentification (utilisé pour la découverte : /shelly est toujours public)."""
    url = "http://%s%s" % (normalize_host(host), path)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return _try_json(resp.read())
    except (urllib.error.URLError, socket.timeout, http.client.HTTPException, OSError, ValueError):
        return None
