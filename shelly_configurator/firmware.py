# -*- coding: utf-8 -*-
"""Vérification de version et mise à jour du firmware (en ligne ou depuis un fichier local)."""
import os
import re
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

from .log_setup import get_logger

log = get_logger("firmware")

MIN_FIRMWARE = (1, 7, 1)
MIN_FIRMWARE_STR = "1.7.1"

# Shelly Plus 1PM : application « Plus1PM », modèles UE et US (le Plus 1PM Mini est exclu)
SUPPORTED_APPS = ("Plus1PM",)
SUPPORTED_MODELS = ("SNSW-001P16EU", "SNSW-001P15UL")
DEVICE_LABEL = "Shelly Plus 1PM"

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def parse_version(text):
    """'1.7.1', '1.7.1-beta2', '20250101-xxx/1.7.1-gabc' -> (1, 7, 1). (0, 0, 0) si illisible."""
    if not text:
        return (0, 0, 0)
    match = _VERSION_RE.search(str(text).split("/")[-1]) or _VERSION_RE.search(str(text))
    if not match:
        return (0, 0, 0)
    return tuple(int(x or 0) for x in match.groups())


def firmware_ok(info):
    return parse_version(info.get("ver")) >= MIN_FIRMWARE


def is_supported_device(info):
    return info.get("app") in SUPPORTED_APPS or info.get("model") in SUPPORTED_MODELS


def describe_device(info):
    return "%s (modèle %s, application %s, génération %s, firmware %s)" % (
        info.get("id", "?"), info.get("model", "?"), info.get("app", "?"),
        info.get("gen", "?"), info.get("ver", "?"))


def local_ip_for(peer_host):
    """Adresse IP locale utilisée pour joindre `peer_host` (aucun paquet n'est envoyé)."""
    host = peer_host.split(":")[0]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((host, 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class LocalFileServer(object):
    """Petit serveur HTTP temporaire qui sert un fichier firmware au Shelly."""

    def __init__(self, file_path, peer_host):
        if not os.path.isfile(file_path):
            raise IOError("Fichier firmware introuvable : %s" % file_path)
        self.file_path = file_path
        self.peer_host = peer_host
        self.downloads = 0
        self._server = None
        self._thread = None
        self.url = None

    def start(self):
        owner = self
        path = self.file_path

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (nom imposé par http.server)
                try:
                    size = os.path.getsize(path)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Length", str(size))
                    self.end_headers()
                    with open(path, "rb") as fh:
                        while True:
                            block = fh.read(65536)
                            if not block:
                                break
                            self.wfile.write(block)
                    owner.downloads += 1
                    log.info("Firmware téléchargé par %s", self.client_address[0])
                except (OSError, socket.error) as exc:
                    log.warning("Envoi du firmware interrompu : %s", exc)

            def log_message(self, fmt, *args):
                log.debug("Serveur firmware : " + fmt, *args)

        local_ip = local_ip_for(self.peer_host)
        self._server = _ThreadingHTTPServer(("0.0.0.0", 0), Handler)
        port = self._server.server_address[1]
        self.url = "http://%s:%d/%s" % (local_ip, port, urllib.parse.quote(os.path.basename(path)))
        self._thread = threading.Thread(target=self._server.serve_forever, name="fw-server")
        self._thread.daemon = True
        self._thread.start()
        log.info("Serveur firmware démarré : %s", self.url)
        return self.url

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
            log.info("Serveur firmware arrêté")
