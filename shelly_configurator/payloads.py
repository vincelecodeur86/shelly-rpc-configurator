# -*- coding: utf-8 -*-
"""Traduction de la configuration enregistrée en appels RPC Shelly (ordonnés) + vérification."""
import ipaddress

from .rpc import USERNAME, sha256_hex

# Valeurs « ssl_ca » acceptées par le firmware Shelly
SSL_CA_VALUES = {"none": None, "noverify": "*", "builtin": "ca.pem", "user": "user_ca.pem"}
TLS_CHUNK = 1024

KIND_CONFIG = "config"
KIND_TLS = "tls"
KIND_AUTH = "auth"
KIND_WIFI = "wifi"


class Step(object):
    def __init__(self, label, method, params, kind=KIND_CONFIG, verify_method=None,
                 verify_params=None, expected=None):
        self.label = label
        self.method = method
        self.params = params
        self.kind = kind
        self.verify_method = verify_method
        self.verify_params = verify_params
        self.expected = expected

    def __repr__(self):
        return "Step(%r, %r)" % (self.label, self.method)


def template_context(info, index):
    mac = (info.get("mac") or "").upper().replace(":", "")
    return {"id": info.get("id", ""), "mac": mac, "mac6": mac[-6:], "n": index}


def render(template, ctx):
    return template.format(**ctx) if template else template


def offset_ip(ip, offset):
    return str(ipaddress.IPv4Address(ip) + offset) if offset else ip


def compute_ha1(realm, password, user=USERNAME):
    return sha256_hex("%s:%s:%s" % (user, realm, password))


def normalize_pem(pem):
    text = (pem or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text + "\n" if text else ""


def _sta_payload(sta, ip_offset):
    payload = {"enable": bool(sta["enable"])}
    if not sta["enable"]:
        return payload
    payload["ssid"] = sta["ssid"]
    if sta["password"]:
        payload["pass"] = sta["password"]
        payload["is_open"] = False
    else:
        payload["is_open"] = True
    if sta["ipv4mode"] == "static":
        payload.update({
            "ipv4mode": "static",
            "ip": offset_ip(sta["ip"], ip_offset),
            "netmask": sta["netmask"],
            "gw": sta["gw"],
            "nameserver": sta["nameserver"] or None,
        })
    else:
        payload["ipv4mode"] = "dhcp"
    return payload


def _config_step(label, component, config, ident=None):
    params = {"config": config}
    verify_params = None
    if ident is not None:
        params = {"id": ident, "config": config}
        verify_params = {"id": ident}
    return Step(label, component + ".SetConfig", params, KIND_CONFIG,
                verify_method=component + ".GetConfig", verify_params=verify_params, expected=config)


def build_plan(cfg, info, index=1):
    """Liste ordonnée des étapes RPC pour l'appareil `info` (résultat de Shelly.GetDeviceInfo).

    index : rang de l'appareil dans la série (1 pour le premier), utilisé par {n} et pour
    l'incrément des IP fixes.
    """
    ctx = template_context(info, index)
    ip_offset = (index - 1) if cfg["options"].get("increment_static_ip") else 0
    steps = []

    dev = cfg["device"]
    if dev["apply"]:
        sys_cfg = {"device": {"eco_mode": bool(dev["eco_mode"]), "discoverable": bool(dev["discoverable"])}}
        name = render(dev["name"], ctx)
        if name:
            sys_cfg["device"]["name"] = name
        location = {}
        if dev["timezone"]:
            location["tz"] = dev["timezone"]
        if str(dev["lat"]).strip():
            location["lat"] = float(dev["lat"])
        if str(dev["lon"]).strip():
            location["lon"] = float(dev["lon"])
        if location:
            sys_cfg["location"] = location
        if dev["sntp_server"]:
            sys_cfg["sntp"] = {"server": dev["sntp_server"]}
        steps.append(_config_step("Paramètres système", "Sys", sys_cfg))
        steps.append(_config_step("Cloud Shelly", "Cloud", {"enable": bool(dev["cloud_enable"])}))
        steps.append(_config_step("Bluetooth", "BLE", {"enable": bool(dev["ble_enable"]),
                                                       "rpc": {"enable": bool(dev["ble_rpc_enable"])}}))

    sw = cfg["switch"]
    if sw["apply"]:
        sw_cfg = {
            "in_mode": sw["in_mode"], "initial_state": sw["initial_state"],
            "auto_on": bool(sw["auto_on"]), "auto_on_delay": float(sw["auto_on_delay"]),
            "auto_off": bool(sw["auto_off"]), "auto_off_delay": float(sw["auto_off_delay"]),
            "power_limit": int(sw["power_limit"]), "voltage_limit": int(sw["voltage_limit"]),
            "undervoltage_limit": int(sw["undervoltage_limit"]),
            "current_limit": float(sw["current_limit"]),
            "autorecover_voltage_errors": bool(sw["autorecover_voltage_errors"]),
        }
        name = render(sw["name"], ctx)
        if name:
            sw_cfg["name"] = name
        steps.append(_config_step("Relais (switch:0)", "Switch", sw_cfg, ident=0))

    inp = cfg["input"]
    if inp["apply"]:
        in_cfg = {"type": inp["type"], "invert": bool(inp["invert"])}
        name = render(inp["name"], ctx)
        if name:
            in_cfg["name"] = name
        steps.append(_config_step("Entrée (input:0)", "Input", in_cfg, ident=0))

    tls = cfg["tls"]
    if tls["apply"]:
        for key, method, label in (("user_ca_pem", "Shelly.PutUserCA", "Certificat CA utilisateur"),
                                   ("client_cert_pem", "Shelly.PutTLSClientCert", "Certificat client TLS"),
                                   ("client_key_pem", "Shelly.PutTLSClientKey", "Clé privée client TLS")):
            pem = normalize_pem(tls[key])
            if not pem:
                continue
            chunks = [pem[i:i + TLS_CHUNK] for i in range(0, len(pem), TLS_CHUNK)]
            for num, chunk in enumerate(chunks):
                steps.append(Step("%s (%d/%d)" % (label, num + 1, len(chunks)), method,
                                  {"data": chunk, "append": num > 0}, KIND_TLS))

    mqtt = cfg["mqtt"]
    if mqtt["apply"]:
        m_cfg = {"enable": bool(mqtt["enable"])}
        if mqtt["enable"]:
            m_cfg.update({
                "server": mqtt["server"].strip(),
                "user": mqtt["user"] or None,
                "ssl_ca": SSL_CA_VALUES[mqtt["ssl_ca"]],
                "use_client_cert": bool(mqtt["use_client_cert"]),
                "rpc_ntf": bool(mqtt["rpc_ntf"]), "status_ntf": bool(mqtt["status_ntf"]),
                "enable_rpc": bool(mqtt["enable_rpc"]), "enable_control": bool(mqtt["enable_control"]),
            })
            if mqtt["password"]:
                m_cfg["pass"] = mqtt["password"]
            client_id = render(mqtt["client_id"], ctx)
            if client_id:
                m_cfg["client_id"] = client_id
            prefix = render(mqtt["topic_prefix"], ctx)
            if prefix:
                m_cfg["topic_prefix"] = prefix
        steps.append(_config_step("MQTT", "MQTT", m_cfg))

    ws = cfg["ws"]
    if ws["apply"]:
        w_cfg = {"enable": bool(ws["enable"])}
        if ws["enable"]:
            w_cfg["server"] = ws["server"].strip()
            w_cfg["ssl_ca"] = SSL_CA_VALUES[ws["ssl_ca"]]
        steps.append(_config_step("WebSocket sortant", "Ws", w_cfg))

    auth = cfg["auth"]
    if auth["apply"]:
        realm = info.get("id", "")
        ha1 = compute_ha1(realm, auth["password"]) if auth["enable"] else None
        steps.append(Step("Mot de passe administrateur", "Shelly.SetAuth",
                          {"user": USERNAME, "realm": realm, "ha1": ha1}, KIND_AUTH,
                          verify_method="Shelly.GetDeviceInfo", expected={"auth_en": bool(auth["enable"])}))

    wifi_cfg = {}
    wifi = cfg["wifi"]
    if wifi["apply"]:
        wifi_cfg["sta"] = _sta_payload(wifi["sta"], ip_offset)
        wifi_cfg["sta1"] = _sta_payload(wifi["sta1"], ip_offset)
        wifi_cfg["roam"] = {"rssi_thr": int(wifi["roam_rssi_thr"]), "interval": int(wifi["roam_interval"])}
    ap = cfg["ap"]
    if ap["apply"]:
        ap_cfg = {"enable": bool(ap["enable"])}
        if ap["enable"]:
            if ap["ssid"]:
                ap_cfg["ssid"] = ap["ssid"]
            ap_cfg["is_open"] = bool(ap["is_open"])
            if not ap["is_open"]:
                ap_cfg["pass"] = ap["password"]
            ap_cfg["range_extender"] = {"enable": bool(ap["range_extender"])}
        wifi_cfg["ap"] = ap_cfg
    if wifi_cfg:
        steps.append(Step("Wi-Fi", "WiFi.SetConfig", {"config": wifi_cfg}, KIND_WIFI,
                          verify_method="WiFi.GetConfig", expected=wifi_cfg))
    return steps


IGNORED_VERIFY_KEYS = ("pass",)
_MISSING = object()


def diff_subset(expected, actual, prefix=""):
    """Compare récursivement les valeurs demandées à celles relues. Renvoie la liste des écarts."""
    diffs = []
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return ["%s : section absente dans la configuration relue" % (prefix or "racine")]
        for key, value in expected.items():
            if key in IGNORED_VERIFY_KEYS:
                continue
            diffs.extend(diff_subset(value, actual.get(key, _MISSING), (prefix + "." + key) if prefix else key))
        return diffs
    if actual is _MISSING:
        return ["%s : valeur absente (attendu %r)" % (prefix, expected)]
    if isinstance(expected, float) or isinstance(actual, float):
        try:
            if abs(float(expected) - float(actual)) < 1e-3:
                return []
        except (TypeError, ValueError):
            pass
    elif expected == actual:
        return []
    return ["%s : attendu %r, relu %r" % (prefix, expected, actual)]
