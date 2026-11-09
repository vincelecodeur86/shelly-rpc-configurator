# -*- coding: utf-8 -*-
"""Client mDNS minimal (bibliothèque standard) : recherche des Shelly et résolution des noms .local.

Utile surtout sous Windows 7, qui ne sait pas résoudre les noms « .local » sans logiciel additionnel.
Les Shelly Gen2 s'annoncent en « _shelly._tcp.local » et « _http._tcp.local », avec un nom d'hôte
du type « shellyplus1pm-a8032ab12345.local ».

Deux sockets sont utilisés : un port éphémère (réponses « unicast » aux requêtes dites legacy, RFC 6762
§6.7) et, si le système le permet, le port 5353 en multicast (réponses multicast).
"""
import os
import select
import socket
import struct
import time

from .log_setup import get_logger

log = get_logger("mdns")

MDNS_GROUP = "224.0.0.251"
MDNS_PORT = 5353
TYPE_A = 1
TYPE_PTR = 12
TYPE_SRV = 33
CLASS_IN = 1
SERVICES = ("_shelly._tcp.local", "_http._tcp.local")


def encode_name(name):
    out = b""
    for label in name.rstrip(".").split("."):
        raw = label.encode("utf-8")
        out += struct.pack("B", len(raw)) + raw
    return out + b"\x00"


def build_query(questions, query_id=0):
    """questions : liste de (nom, type). Renvoie le paquet DNS."""
    packet = struct.pack(">HHHHHH", query_id, 0, len(questions), 0, 0, 0)
    for name, qtype in questions:
        packet += encode_name(name) + struct.pack(">HH", qtype, CLASS_IN)
    return packet


def _read_name(data, offset, depth=0):
    labels = []
    jumped_to = None
    while True:
        if offset >= len(data) or depth > 20:
            raise ValueError("nom DNS invalide")
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:  # compression
            pointer = struct.unpack(">H", data[offset:offset + 2])[0] & 0x3FFF
            if jumped_to is None:
                jumped_to = offset + 2
            offset = pointer
            depth += 1
            continue
        offset += 1
        labels.append(data[offset:offset + length].decode("utf-8", "replace"))
        offset += length
    return ".".join(labels), (jumped_to if jumped_to is not None else offset)


def parse_packet(data):
    """Renvoie la liste des enregistrements (nom, type, valeur) des sections réponse/additionnelle."""
    if len(data) < 12:
        raise ValueError("paquet DNS trop court")
    _id, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", data[:12])
    offset = 12
    for _ in range(qd):
        _name, offset = _read_name(data, offset)
        offset += 4
    records = []
    for _ in range(an + ns + ar):
        name, offset = _read_name(data, offset)
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[offset:offset + 10])
        offset += 10
        rdata_start = offset
        offset += rdlen
        if rtype == TYPE_A and rdlen == 4:
            value = socket.inet_ntoa(data[rdata_start:rdata_start + 4])
        elif rtype == TYPE_PTR:
            value = _read_name(data, rdata_start)[0]
        elif rtype == TYPE_SRV:
            port = struct.unpack(">H", data[rdata_start + 4:rdata_start + 6])[0]
            value = (_read_name(data, rdata_start + 6)[0], port)
        else:
            continue
        records.append((name.lower(), rtype, value))
    return records


def _open_sockets():
    socks = []
    uni = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    uni.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
    uni.bind(("", 0))
    socks.append(uni)
    try:
        multi = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        multi.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                multi.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        multi.bind(("", MDNS_PORT))
        mreq = struct.pack("4s4s", socket.inet_aton(MDNS_GROUP), socket.inet_aton("0.0.0.0"))
        multi.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        socks.append(multi)
    except OSError as exc:  # port 5353 indisponible : les réponses unicast suffisent souvent
        log.debug("Écoute multicast mDNS impossible : %s", exc)
    return socks


def _exchange(questions, timeout, stop_when=None):
    """Envoie la requête et collecte (ip_source, enregistrements) jusqu'au délai."""
    packets = []
    try:
        socks = _open_sockets()
    except OSError as exc:
        log.warning("mDNS indisponible : %s", exc)
        return packets
    try:
        query = build_query(questions, query_id=struct.unpack(">H", os.urandom(2))[0])
        for attempt in range(2):
            try:
                socks[0].sendto(query, (MDNS_GROUP, MDNS_PORT))
            except OSError as exc:
                log.warning("Envoi mDNS impossible : %s", exc)
                return packets
            deadline = time.time() + timeout / 2.0
            while True:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                ready, _w, _x = select.select(socks, [], [], remaining)
                for sock in ready:
                    try:
                        data, addr = sock.recvfrom(9000)
                        packets.append((addr[0], parse_packet(data)))
                    except (OSError, ValueError, struct.error, IndexError):
                        continue
                if stop_when is not None and stop_when(packets):
                    return packets
    finally:
        for sock in socks:
            sock.close()
    return packets


def browse(timeout=3.0):
    """Adresses IP des appareils qui répondent aux services Shelly/HTTP (à confirmer par /shelly)."""
    found = []
    for source, records in _exchange([(name, TYPE_PTR) for name in SERVICES], timeout):
        relevant = any(rtype in (TYPE_PTR, TYPE_SRV) and any(s in name or s in str(value).lower()
                                                            for s in SERVICES)
                       for name, rtype, value in records)
        if not relevant:
            continue
        addresses = [value for _n, rtype, value in records if rtype == TYPE_A] or [source]
        for ip in addresses:
            if ip not in found:
                found.append(ip)
    log.info("mDNS : %d appareil(s) ont répondu", len(found))
    return found


def resolve(hostname, timeout=2.0):
    """Adresse IPv4 de « nom.local » via mDNS, ou None."""
    name = hostname.lower().rstrip(".")
    if not name.endswith(".local"):
        name += ".local"

    def answered(packets):
        return any(n == name and t == TYPE_A for _s, recs in packets for n, t, _v in recs)

    for _source, records in _exchange([(name, TYPE_A)], timeout, stop_when=answered):
        for rname, rtype, value in records:
            if rname == name and rtype == TYPE_A:
                return value
    return None


def resolve_host(host, timeout=2.0):
    """Renvoie l'IP à utiliser pour `host` (IP, nom DNS ou nom .local). Lève ValueError si introuvable."""
    host = host.strip()
    hostname, sep, port = host.partition(":")
    try:
        socket.inet_aton(hostname)
        if hostname.count(".") == 3:
            return host
    except OSError:
        pass
    ip = None
    try:
        ip = socket.gethostbyname(hostname)
    except OSError:
        if hostname.lower().endswith(".local") or "." not in hostname:
            ip = resolve(hostname, timeout)
    if not ip:
        raise ValueError("Nom « %s » introuvable sur le réseau (ni DNS ni mDNS)." % hostname)
    log.info("%s résolu en %s", hostname, ip)
    return ip + sep + port
