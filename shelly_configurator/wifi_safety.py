# -*- coding: utf-8 -*-
"""Chemin de connexion au Shelly (point d'accès ou réseau Wi-Fi) et risques d'un changement Wi-Fi.

Quand le PC joint le Shelly par le réseau Wi-Fi auquel le Shelly est connecté (et non par le point
d'accès du Shelly), changer le réseau ou l'adresse du Shelly coupe la connexion. Si en plus le point
d'accès du Shelly est désactivé, une erreur de mot de passe Wi-Fi rendrait le Shelly injoignable.
"""

VIA_AP = "ap"
VIA_STA = "sta"
VIA_UNKNOWN = "unknown"
SHELLY_AP_PREFIX = "192.168.33."

VIA_LABELS = {
    VIA_AP: "point d'accès du Shelly",
    VIA_STA: "réseau Wi-Fi du Shelly",
    VIA_UNKNOWN: "réseau (chemin indéterminé)",
}


def host_ip(host):
    return (host or "").split(":")[0]


def connection_path(host, wifi_status):
    """VIA_AP, VIA_STA ou VIA_UNKNOWN selon l'adresse utilisée et WiFi.GetStatus."""
    ip = host_ip(host)
    sta_ip = (wifi_status or {}).get("sta_ip")
    if sta_ip and ip == sta_ip:
        return VIA_STA
    if ip.startswith(SHELLY_AP_PREFIX):
        return VIA_AP
    if not sta_ip:
        # Pas de connexion Wi-Fi client active : on passe forcément par le point d'accès
        return VIA_AP
    return VIA_UNKNOWN  # traduction d'adresse, redirection de port…


class WifiAssessment(object):
    def __init__(self, disruptive=False, danger=False, message="", current_ssid=""):
        self.disruptive = disruptive
        self.danger = danger
        self.message = message
        self.current_ssid = current_ssid


def assess_wifi_change(new_wifi, current_config, wifi_status, via, host):
    """Évalue l'effet de WiFi.SetConfig(new_wifi) sur la connexion en cours."""
    current_wifi = (current_config or {}).get("wifi") or {}
    cur_sta = current_wifi.get("sta") or {}
    cur_ap = current_wifi.get("ap") or {}
    status = wifi_status or {}
    cur_ssid = status.get("ssid") or cur_sta.get("ssid") or ""
    if via == VIA_AP or "sta" not in (new_wifi or {}):
        return WifiAssessment(current_ssid=cur_ssid)

    new_sta = new_wifi["sta"]
    ip = host_ip(host)
    reasons = []
    if not new_sta.get("enable"):
        reasons.append("son Wi-Fi client sera désactivé")
    else:
        if new_sta.get("ssid") != cur_ssid:
            reasons.append("il passera du réseau « %s » au réseau « %s »" % (cur_ssid or "?", new_sta.get("ssid")))
        if new_sta.get("ipv4mode") == "static" and new_sta.get("ip") != ip:
            reasons.append("son adresse deviendra %s" % new_sta.get("ip"))
        elif new_sta.get("ipv4mode") == "dhcp" and cur_sta.get("ipv4mode") == "static":
            reasons.append("il passera en adressage automatique (DHCP) : son adresse changera probablement")
    if not reasons:
        return WifiAssessment(current_ssid=cur_ssid)

    ap_after = (new_wifi.get("ap") or {}).get("enable", cur_ap.get("enable", False))
    danger = not ap_after
    message = ("Ce PC communique avec le Shelly %s par le réseau Wi-Fi « %s ».\n\n"
               "Avec la configuration enregistrée, %s.\n\n"
               "La connexion sera donc coupée ; l'utilitaire essaiera ensuite de retrouver le Shelly "
               "(nouvelle adresse fixe, même adresse, nom .local)."
               % (ip, cur_ssid or "?", " ; ".join(reasons)))
    if danger:
        message += ("\n\nATTENTION : le point d'accès du Shelly sera désactivé. Si le nom ou le mot de passe "
                    "du nouveau réseau est erroné, le Shelly peut devenir injoignable ; il faudra alors le "
                    "réinitialiser (appui long sur le bouton) pour le récupérer.")
    return WifiAssessment(True, danger, message, cur_ssid)


def candidate_hosts(new_wifi, original_host):
    """Adresses où chercher le Shelly après un changement Wi-Fi (hors mDNS)."""
    out = []
    for key in ("sta", "sta1"):
        sta = (new_wifi or {}).get(key) or {}
        if sta.get("enable") and sta.get("ipv4mode") == "static" and sta.get("ip"):
            out.append(sta["ip"])
    out.append(original_host)
    seen = []
    for host in out:
        if host not in seen:
            seen.append(host)
    return seen
