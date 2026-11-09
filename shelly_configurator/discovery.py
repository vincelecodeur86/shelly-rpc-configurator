# -*- coding: utf-8 -*-
"""Recherche des Shelly sur le réseau local (balayage HTTP de /shelly, sans authentification)."""
import ipaddress
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from .log_setup import get_logger
from .rpc import http_get_json

log = get_logger("discovery")

SHELLY_AP_ADDRESS = "192.168.33.1"
MAX_HOSTS = 4096


def local_ipv4_addresses():
    addresses = set()
    try:
        for res in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(res[4][0])
    except socket.error:
        pass
    for probe in ("8.8.8.8", SHELLY_AP_ADDRESS):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((probe, 80))
            addresses.add(sock.getsockname()[0])
        except socket.error:
            pass
        finally:
            sock.close()
    return sorted(a for a in addresses if not a.startswith("127.") and not a.startswith("169.254."))


def guess_networks():
    """Réseaux /24 des interfaces locales, ex. ['192.168.1.0/24']."""
    nets = []
    for addr in local_ipv4_addresses():
        net = str(ipaddress.IPv4Network(addr + "/24", strict=False))
        if net not in nets:
            nets.append(net)
    return nets


def parse_targets(text):
    """'192.168.1.0/24, 10.0.0.5' -> liste d'adresses. Lève ValueError si invalide ou trop grand."""
    hosts = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            net = ipaddress.IPv4Network(part, strict=False)
            if net.num_addresses > MAX_HOSTS:
                raise ValueError("Réseau %s trop grand (maximum %d adresses)" % (part, MAX_HOSTS))
            hosts.extend(str(h) for h in net.hosts())
        elif "-" in part:
            start, end = [p.strip() for p in part.split("-", 1)]
            first = ipaddress.IPv4Address(start)
            last = ipaddress.IPv4Address(end) if "." in end else ipaddress.IPv4Address(
                start.rsplit(".", 1)[0] + "." + end)
            if int(last) < int(first) or int(last) - int(first) >= MAX_HOSTS:
                raise ValueError("Plage invalide : %s" % part)
            hosts.extend(str(ipaddress.IPv4Address(i)) for i in range(int(first), int(last) + 1))
        else:
            ipaddress.IPv4Address(part.split(":")[0])
            hosts.append(part)
    return hosts


def probe(host, timeout=1.0):
    """Renvoie les infos du Shelly à `host` (dict, avec la clé 'host') ou None."""
    data = http_get_json(host, "/shelly", timeout=timeout)
    if not isinstance(data, dict):
        return None
    if "gen" not in data and not str(data.get("id", "")).lower().startswith("shelly"):
        return None
    data = dict(data)
    data["host"] = host
    return data


def scan(hosts, on_found=None, on_progress=None, stop_event=None, timeout=1.0, workers=64):
    """Interroge chaque hôte en parallèle. Renvoie la liste des Shelly trouvés."""
    stop_event = stop_event or threading.Event()
    found = []
    total = len(hosts)
    done = 0
    log.info("Recherche de Shelly sur %d adresse(s)", total)
    with ThreadPoolExecutor(max_workers=max(1, min(workers, total or 1))) as pool:
        futures = [pool.submit(_probe_unless_stopped, h, timeout, stop_event) for h in hosts]
        for fut in as_completed(futures):
            done += 1
            info = fut.result()
            if info:
                found.append(info)
                log.info("Shelly trouvé : %s (%s, %s)", info["host"], info.get("id"), info.get("ver"))
                if on_found:
                    on_found(info)
            if on_progress:
                on_progress(done, total)
            if stop_event.is_set():
                for other in futures:
                    other.cancel()
                break
    return found


def _probe_unless_stopped(host, timeout, stop_event):
    if stop_event.is_set():
        return None
    try:
        return probe(host, timeout)
    except Exception as exc:  # une adresse problématique ne doit pas arrêter le balayage
        log.debug("Sonde %s : %s", host, exc)
        return None


def find_shellies(hosts, on_found=None, on_progress=None, stop_event=None, use_mdns=True, timeout=1.0):
    """Recherche complète : annonces mDNS d'abord (rapide, tout réseau local), puis balayage des adresses."""
    from . import mdns
    stop_event = stop_event or threading.Event()
    found = []
    seen = set()

    def collect(info):
        if info["host"] in seen:
            return
        seen.add(info["host"])
        found.append(info)
        if on_found:
            on_found(info)

    if use_mdns:
        if on_progress:
            on_progress(0, 1, "Écoute des annonces mDNS…")
        try:
            for ip in mdns.browse():
                info = probe(ip, timeout)
                if info:
                    collect(info)
        except Exception as exc:  # le balayage prend le relais
            log.warning("Recherche mDNS impossible : %s", exc)
    remaining = [h for h in hosts if h not in seen]
    if remaining and not stop_event.is_set():
        def progress(done, total):
            if on_progress:
                on_progress(done, total, "Balayage %d/%d" % (done, total))
        scan(remaining, collect, progress, stop_event, timeout)
    return found
