# -*- coding: utf-8 -*-
"""Configuration souhaitée : valeurs par défaut, chargement/enregistrement chiffré, validation,
import/export."""
import copy
import ipaddress
import json
import os
import string
import time

from . import paths, secrets_store
from .log_setup import get_logger

log = get_logger("config")

SCHEMA_VERSION = 1
EXPORT_FORMAT = "shelly-configurator-export"

TEMPLATE_FIELDS = ("id", "mac", "mac6", "n")

_STA_DEFAULT = {
    "enable": False, "ssid": "", "password": "", "ipv4mode": "dhcp",
    "ip": "", "netmask": "255.255.255.0", "gw": "", "nameserver": "",
}

DEFAULTS = {
    "schema_version": SCHEMA_VERSION,
    "device": {
        "apply": True, "name": "", "timezone": "Europe/Paris", "sntp_server": "time.google.com",
        "lat": "", "lon": "", "eco_mode": False, "discoverable": True,
        "cloud_enable": False, "ble_enable": False, "ble_rpc_enable": False,
    },
    "wifi": {
        "apply": True,
        "sta": dict(_STA_DEFAULT, enable=True),
        "sta1": dict(_STA_DEFAULT),
        "roam_rssi_thr": -80, "roam_interval": 60,
    },
    "ap": {"apply": True, "enable": False, "ssid": "", "password": "", "is_open": False,
           "range_extender": False},
    "mqtt": {
        "apply": True, "enable": False, "server": "", "client_id": "", "user": "", "password": "",
        "topic_prefix": "", "ssl_ca": "none", "use_client_cert": False, "rpc_ntf": True,
        "status_ntf": False, "enable_rpc": True, "enable_control": True,
    },
    "ws": {"apply": True, "enable": False, "server": "", "ssl_ca": "none"},
    "auth": {"apply": True, "enable": True, "password": ""},
    "tls": {"apply": False, "user_ca_pem": "", "client_cert_pem": "", "client_key_pem": ""},
    "switch": {
        "apply": False, "name": "", "in_mode": "follow", "initial_state": "restore_last",
        "auto_on": False, "auto_on_delay": 60.0, "auto_off": False, "auto_off_delay": 60.0,
        "power_limit": 4480, "voltage_limit": 280, "undervoltage_limit": 0, "current_limit": 16.0,
        "autorecover_voltage_errors": False,
    },
    "input": {"apply": False, "name": "", "type": "switch", "invert": False},
    "options": {
        "backup_before": True, "verify_after": True, "reboot": "required",
        "increment_static_ip": True, "default_host": "192.168.33.1",
        "confirm_wifi_risk": True, "relocate_timeout": 60,
    },
}

SECRET_PATHS = (
    ("auth", "password"),
    ("wifi", "sta", "password"),
    ("wifi", "sta1", "password"),
    ("ap", "password"),
    ("mqtt", "password"),
    ("tls", "client_key_pem"),
)


class ConfigError(Exception):
    pass


# -- accès par chemin -------------------------------------------------------------------------
def get_path(data, path):
    for key in path:
        data = data[key]
    return data


def set_path(data, path, value):
    for key in path[:-1]:
        data = data.setdefault(key, {})
    data[path[-1]] = value


def default_config():
    return copy.deepcopy(DEFAULTS)


def merge_defaults(cfg, defaults=None):
    """Complète cfg avec les clés absentes (compatibilité avec une config plus ancienne)."""
    defaults = DEFAULTS if defaults is None else defaults
    out = copy.deepcopy(defaults)
    for key, value in (cfg or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge_defaults(value, out[key])
        elif key in out:
            out[key] = value
    out_version = out.get("schema_version")
    if out_version is not None:
        out["schema_version"] = SCHEMA_VERSION
    return out


def _map_secrets(cfg, func):
    out = copy.deepcopy(cfg)
    for path in SECRET_PATHS:
        try:
            value = get_path(out, path)
        except (KeyError, TypeError):
            continue
        set_path(out, path, func(value))
    return out


# -- disque -----------------------------------------------------------------------------------
def config_exists(path=None):
    return os.path.isfile(path or paths.config_file())


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    if not paths.is_windows():
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def load_config(path=None):
    path = path or paths.config_file()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("Fichier de configuration illisible (%s) : %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ConfigError("Fichier de configuration invalide : %s" % path)
    try:
        cfg = _map_secrets(raw, secrets_store.decrypt_local)
    except secrets_store.SecretError as exc:
        raise ConfigError("Impossible de déchiffrer les mots de passe enregistrés (%s). "
                          "La configuration a peut-être été copiée depuis un autre poste ou compte : "
                          "utilisez Importer/Exporter pour la transférer." % exc)
    return merge_defaults(cfg)


def save_config(cfg, path=None):
    path = path or paths.config_file()
    data = _map_secrets(cfg, secrets_store.encrypt_local)
    data["schema_version"] = SCHEMA_VERSION
    data["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_json_atomic(path, data)
    log.info("Configuration enregistrée dans %s", path)


def export_config(cfg, dest, passphrase=None):
    """Exporte la config. Avec passphrase : secrets chiffrés ; sans : secrets retirés."""
    if passphrase:
        data = _map_secrets(cfg, lambda v: secrets_store.encrypt_with_passphrase(v, passphrase))
    else:
        data = _map_secrets(cfg, lambda v: "")
    payload = {"format": EXPORT_FORMAT, "schema_version": SCHEMA_VERSION,
               "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
               "secrets_included": bool(passphrase), "config": data}
    _write_json_atomic(dest, payload)
    log.info("Configuration exportée vers %s (secrets inclus : %s)", dest, bool(passphrase))


def export_needs_passphrase(src):
    payload = _read_export(src)
    return bool(payload.get("secrets_included"))


def _read_export(src):
    try:
        with open(src, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError) as exc:
        raise ConfigError("Fichier d'import illisible : %s" % exc)
    if not isinstance(payload, dict) or payload.get("format") != EXPORT_FORMAT:
        raise ConfigError("Ce fichier n'est pas un export de Shelly Configurator.")
    return payload


def import_config(src, passphrase=None):
    payload = _read_export(src)
    data = payload.get("config") or {}
    if payload.get("secrets_included"):
        if not passphrase:
            raise ConfigError("Une phrase de passe est nécessaire pour importer ce fichier.")
        try:
            data = _map_secrets(data, lambda v: secrets_store.decrypt_with_passphrase(v, passphrase))
        except secrets_store.SecretError as exc:
            raise ConfigError(str(exc))
    log.info("Configuration importée depuis %s", src)
    return merge_defaults(data)


# -- validation -------------------------------------------------------------------------------
def _is_ipv4(value):
    try:
        ipaddress.IPv4Address(value)
        return True
    except (ValueError, ipaddress.AddressValueError):
        return False


def check_template(value):
    """Renvoie None si le modèle est valide, sinon un message d'erreur."""
    if not value:
        return None
    try:
        names = [f[1] for f in string.Formatter().parse(value) if f[1] is not None]
    except ValueError as exc:
        return "accolade mal fermée (%s)" % exc
    unknown = [n for n in names if n not in TEMPLATE_FIELDS]
    if unknown:
        return "variable inconnue {%s} (autorisées : %s)" % (
            unknown[0], ", ".join("{%s}" % f for f in TEMPLATE_FIELDS))
    return None


def _check_pem(label, pem, marker, errors):
    if pem and pem.strip():
        if "-----BEGIN" not in pem or marker not in pem:
            errors.append("%s : le contenu ne ressemble pas à un fichier PEM valide." % label)


def validate_config(cfg):
    """Renvoie (erreurs, avertissements) : listes de messages en français."""
    errors, warnings = [], []
    dev = cfg["device"]
    if dev["apply"]:
        msg = check_template(dev["name"])
        if msg:
            errors.append("Nom de l'appareil : " + msg)
        for key, label, lim in (("lat", "Latitude", 90), ("lon", "Longitude", 180)):
            val = str(dev[key]).strip()
            if val:
                try:
                    if abs(float(val)) > lim:
                        raise ValueError
                except ValueError:
                    errors.append("%s invalide (nombre entre -%d et %d)." % (label, lim, lim))

    wifi = cfg["wifi"]
    if wifi["apply"]:
        for key, label in (("sta", "Wi-Fi principal"), ("sta1", "Wi-Fi de secours")):
            sta = wifi[key]
            if not sta["enable"]:
                continue
            if not sta["ssid"] or len(sta["ssid"].encode("utf-8")) > 32:
                errors.append("%s : SSID obligatoire (32 octets max)." % label)
            if sta["password"] and not (8 <= len(sta["password"]) <= 63):
                errors.append("%s : le mot de passe WPA doit faire de 8 à 63 caractères." % label)
            if not sta["password"]:
                warnings.append("%s : aucun mot de passe, le réseau est considéré comme ouvert." % label)
            if sta["ipv4mode"] == "static":
                for field, flabel in (("ip", "adresse IP"), ("netmask", "masque"), ("gw", "passerelle")):
                    if not _is_ipv4(sta[field]):
                        errors.append("%s : %s invalide." % (label, flabel))
                if sta["nameserver"] and not _is_ipv4(sta["nameserver"]):
                    errors.append("%s : serveur DNS invalide." % label)
        if not wifi["sta"]["enable"] and not wifi["sta1"]["enable"]:
            ap = cfg["ap"]
            if ap["apply"] and not ap["enable"]:
                errors.append("Wi-Fi client et point d'accès tous désactivés : le Shelly deviendrait injoignable.")
        for key, label in (("roam_rssi_thr", "Seuil d'itinérance"), ("roam_interval", "Intervalle d'itinérance")):
            if not isinstance(wifi[key], int):
                errors.append("%s : nombre entier attendu." % label)

    ap = cfg["ap"]
    if ap["apply"] and ap["enable"]:
        if ap["ssid"] and len(ap["ssid"].encode("utf-8")) > 32:
            errors.append("Point d'accès : SSID trop long (32 octets max).")
        if not ap["is_open"] and not (8 <= len(ap["password"]) <= 63):
            errors.append("Point d'accès : mot de passe de 8 à 63 caractères obligatoire (ou cochez « ouvert »).")
        if ap["is_open"]:
            warnings.append("Point d'accès ouvert : n'importe qui à proximité pourra s'y connecter.")

    mqtt = cfg["mqtt"]
    if mqtt["apply"] and mqtt["enable"]:
        server = mqtt["server"].strip()
        if not server:
            errors.append("MQTT : adresse du serveur obligatoire (hôte:port).")
        elif ":" in server:
            port = server.rsplit(":", 1)[1]
            if not port.isdigit() or not 0 < int(port) < 65536:
                errors.append("MQTT : port invalide dans « %s »." % server)
        for key, label in (("client_id", "identifiant client"), ("topic_prefix", "préfixe de topic")):
            msg = check_template(mqtt[key])
            if msg:
                errors.append("MQTT %s : %s" % (label, msg))
        if mqtt["ssl_ca"] == "user" and not (cfg["tls"]["apply"] and cfg["tls"]["user_ca_pem"].strip()):
            warnings.append("MQTT utilise la CA utilisateur mais aucune n'est fournie : "
                            "elle doit déjà être présente sur le Shelly.")
        if mqtt["use_client_cert"] and not (cfg["tls"]["apply"] and cfg["tls"]["client_cert_pem"].strip()):
            warnings.append("MQTT utilise un certificat client mais aucun n'est fourni : "
                            "il doit déjà être présent sur le Shelly.")
        if mqtt["ssl_ca"] == "none" and mqtt["password"]:
            warnings.append("MQTT sans TLS : le mot de passe MQTT circulera en clair sur le réseau.")

    ws = cfg["ws"]
    if ws["apply"] and ws["enable"]:
        server = ws["server"].strip().lower()
        if not (server.startswith("ws://") or server.startswith("wss://")):
            errors.append("WebSocket : l'adresse doit commencer par ws:// ou wss://.")
        if server.startswith("wss://") and ws["ssl_ca"] == "none":
            warnings.append("WebSocket en wss:// sans CA choisie : sélectionnez une vérification TLS.")
        if ws["ssl_ca"] == "user" and not (cfg["tls"]["apply"] and cfg["tls"]["user_ca_pem"].strip()):
            warnings.append("WebSocket utilise la CA utilisateur mais aucune n'est fournie.")

    auth = cfg["auth"]
    if auth["apply"]:
        if auth["enable"]:
            if not auth["password"]:
                errors.append("Mot de passe administrateur obligatoire (ou décochez la protection).")
            elif len(auth["password"]) < 8:
                warnings.append("Mot de passe administrateur court (moins de 8 caractères).")
        else:
            warnings.append("La protection par mot de passe du Shelly sera désactivée.")

    tls = cfg["tls"]
    if tls["apply"]:
        _check_pem("CA utilisateur", tls["user_ca_pem"], "CERTIFICATE", errors)
        _check_pem("Certificat client", tls["client_cert_pem"], "CERTIFICATE", errors)
        _check_pem("Clé privée client", tls["client_key_pem"], "PRIVATE KEY", errors)
        if bool(tls["client_cert_pem"].strip()) != bool(tls["client_key_pem"].strip()):
            warnings.append("Certificat client et clé privée vont normalement ensemble.")

    sw = cfg["switch"]
    if sw["apply"]:
        for key, label in (("auto_on_delay", "Délai auto-ON"), ("auto_off_delay", "Délai auto-OFF"),
                           ("power_limit", "Limite de puissance"), ("voltage_limit", "Limite de tension"),
                           ("undervoltage_limit", "Seuil de sous-tension"), ("current_limit", "Limite de courant")):
            value = sw[key]
            if not isinstance(value, (int, float)) or value < 0:
                errors.append("Relais : %s doit être un nombre positif." % label)
        if sw["auto_on"] and sw["auto_off"]:
            warnings.append("Relais : auto-ON et auto-OFF activés ensemble (le relais clignotera).")

    timeout = cfg["options"].get("relocate_timeout")
    if not isinstance(timeout, int) or not 0 <= timeout <= 600:
        errors.append("Options : délai de recherche du Shelly entre 0 et 600 secondes.")

    return errors, warnings


def summary_lines(cfg):
    """Résumé lisible de la configuration (pour la question « garder ou modifier »)."""
    lines = []

    def onoff(flag):
        return "activé" if flag else "désactivé"

    if cfg["device"]["apply"]:
        d = cfg["device"]
        lines.append("Appareil : nom « %s », fuseau %s, Cloud %s, Bluetooth %s"
                     % (d["name"] or "(inchangé)", d["timezone"] or "(inchangé)",
                        onoff(d["cloud_enable"]), onoff(d["ble_enable"])))
    if cfg["wifi"]["apply"]:
        sta = cfg["wifi"]["sta"]
        if sta["enable"]:
            mode = "DHCP" if sta["ipv4mode"] == "dhcp" else "IP fixe %s" % sta["ip"]
            lines.append("Wi-Fi : « %s » (%s)" % (sta["ssid"], mode))
        else:
            lines.append("Wi-Fi client : désactivé")
        if cfg["wifi"]["sta1"]["enable"]:
            lines.append("Wi-Fi de secours : « %s »" % cfg["wifi"]["sta1"]["ssid"])
    if cfg["ap"]["apply"]:
        lines.append("Point d'accès : %s" % onoff(cfg["ap"]["enable"]))
    if cfg["mqtt"]["apply"]:
        m = cfg["mqtt"]
        lines.append("MQTT : %s" % (m["server"] if m["enable"] else "désactivé"))
    if cfg["ws"]["apply"]:
        w = cfg["ws"]
        lines.append("WebSocket sortant : %s" % (w["server"] if w["enable"] else "désactivé"))
    if cfg["auth"]["apply"]:
        lines.append("Mot de passe admin : %s" % ("défini" if cfg["auth"]["enable"] else "protection désactivée"))
    if cfg["tls"]["apply"]:
        t = cfg["tls"]
        parts = [label for key, label in (("user_ca_pem", "CA"), ("client_cert_pem", "certificat"),
                                          ("client_key_pem", "clé")) if t[key].strip()]
        lines.append("Certificats TLS : %s" % (", ".join(parts) or "aucun"))
    if cfg["switch"]["apply"]:
        lines.append("Relais : mode %s, état au démarrage %s"
                     % (cfg["switch"]["in_mode"], cfg["switch"]["initial_state"]))
    if cfg["input"]["apply"]:
        lines.append("Entrée : type %s" % cfg["input"]["type"])
    return lines
