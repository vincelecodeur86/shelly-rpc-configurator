# -*- coding: utf-8 -*-
"""Faux Shelly Plus 1PM (serveur HTTP local) pour tester l'utilitaire sans matériel.

Utilisation manuelle :  python -m tests.fake_shelly --port 8080 [--password secret] [--ver 1.6.2]
puis dans l'utilitaire, adresse « 127.0.0.1:8080 ».
"""
import argparse
import copy
import hashlib
import json
import os
import re
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn


def sha256_hex(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def deep_merge(dst, src):
    for key, value in src.items():
        if isinstance(value, dict) and isinstance(dst.get(key), dict):
            deep_merge(dst[key], value)
        else:
            dst[key] = value


_AUTH_RE = re.compile(r'(\w+)=(?:"([^"]*)"|([^,\s]+))')


class FakeShelly(object):
    def __init__(self, password=None, ver="1.7.1", app="Plus1PM", model="SNSW-001P16EU",
                 device_id="shellyplus1pm-a8032ab12345", mac="A8032AB12345", update_to="1.7.4",
                 sta_ip=None, sta_ssid=None, move_on_wifi=False):
        self.device_id = device_id
        self.info = {"name": None, "id": device_id, "mac": mac, "slot": 0, "model": model, "gen": 2,
                     "fw_id": "20250101-000000/%s-gabcdef" % ver, "ver": ver, "app": app,
                     "auth_en": False, "auth_domain": None}
        self.ha1 = None
        if password:
            self.set_password(password)
        self.update_to = update_to
        self.calls = []
        self.files = {"user_ca": "", "client_cert": "", "client_key": ""}
        self.rebooted = 0
        self.down_until = 0.0
        self.fail_methods = {}
        self.downloaded_firmware = None
        self.config = {
            "sys": {"device": {"name": None, "eco_mode": False, "discoverable": True, "mac": mac},
                    "location": {"tz": None, "lat": None, "lon": None}, "sntp": {"server": "time.google.com"}},
            "cloud": {"enable": True, "server": "shelly-103-eu.shelly.cloud:6022/jrpc"},
            "ble": {"enable": True, "rpc": {"enable": True}},
            "mqtt": {"enable": False, "server": None, "client_id": device_id, "user": None,
                     "ssl_ca": None, "topic_prefix": device_id, "rpc_ntf": True, "status_ntf": False,
                     "use_client_cert": False, "enable_rpc": True, "enable_control": True},
            "ws": {"enable": False, "server": None, "ssl_ca": "ca.pem"},
            "wifi": {"ap": {"ssid": device_id, "is_open": True, "enable": True,
                            "range_extender": {"enable": False}},
                     "sta": {"ssid": None, "is_open": True, "enable": False, "ipv4mode": "dhcp",
                             "ip": None, "netmask": None, "gw": None, "nameserver": None},
                     "sta1": {"ssid": None, "is_open": True, "enable": False, "ipv4mode": "dhcp",
                              "ip": None, "netmask": None, "gw": None, "nameserver": None},
                     "roam": {"rssi_thr": -80, "interval": 60}},
            "switch:0": {"id": 0, "name": None, "in_mode": "follow", "initial_state": "match_input",
                         "auto_on": False, "auto_on_delay": 60.0, "auto_off": False,
                         "auto_off_delay": 60.0, "power_limit": 4480, "voltage_limit": 280,
                         "undervoltage_limit": 0, "autorecover_voltage_errors": False,
                         "current_limit": 16.0},
            "input:0": {"id": 0, "name": None, "type": "switch", "enable": True, "invert": False,
                        "factory_reset": True},
        }
        self.secret_values = {}
        self.sta_ip = sta_ip          # adresse du Shelly vue côté Wi-Fi client (None = non connecté)
        self.sta_ssid = sta_ssid
        self.move_on_wifi = move_on_wifi  # après WiFi.SetConfig, le 1er serveur cesse de répondre
        self.boot_time = time.time() - 3600
        self.blocked_ports = set()
        self.current_port = None
        self._servers = []
        if sta_ssid:
            self.config["wifi"]["sta"].update(ssid=sta_ssid, enable=True, is_open=False)

    def set_password(self, password):
        self.ha1 = sha256_hex("admin:%s:%s" % (self.device_id, password)) if password else None
        self.info["auth_en"] = bool(password)
        self.info["auth_domain"] = self.device_id if password else None

    # -- serveur --------------------------------------------------------------------------------
    def start(self, port=0):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, obj, headers=None):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _blocked(self):
                return time.time() < fake.down_until or self.server.server_address[1] in fake.blocked_ports

            def do_GET(self):  # noqa: N802
                if self._blocked():
                    self.close_connection = True
                    return
                if self.path == "/shelly":
                    return self._send(200, fake.info)
                self._send(404, {"error": "not found"})

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length)
                if self._blocked():
                    self.close_connection = True
                    return
                req = json.loads(body.decode("utf-8"))
                method = req.get("method")
                if fake.ha1 and method != "Shelly.GetDeviceInfo" and not fake._auth_ok(self.headers.get("Authorization")):
                    nonce = str(int(time.time()))
                    return self._send(401, {"id": req.get("id"), "src": fake.device_id,
                                            "error": {"code": 401, "message": "unauthorized"}},
                                      {"WWW-Authenticate": 'Digest qop="auth", realm="%s", nonce="%s", '
                                                          'algorithm=SHA-256' % (fake.device_id, nonce)})
                fake.calls.append((method, req.get("params")))
                fake.current_port = self.server.server_address[1]
                try:
                    result = fake.handle(method, req.get("params") or {})
                except KeyError as exc:
                    return self._send(500, {"id": req.get("id"), "src": fake.device_id,
                                            "error": {"code": -105, "message": str(exc)}})
                except ValueError as exc:
                    return self._send(500, {"id": req.get("id"), "src": fake.device_id,
                                            "error": {"code": -103, "message": str(exc)}})
                self._send(200, {"id": req.get("id"), "src": fake.device_id, "result": result})

        class Server(ThreadingMixIn, HTTPServer):
            daemon_threads = True
            allow_reuse_address = True

        server = Server(("127.0.0.1", port), Handler)
        self._servers.append(server)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()
        return "127.0.0.1:%d" % server.server_address[1]

    def stop(self):
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers = []

    def _auth_ok(self, header):
        if not header or not header.startswith("Digest "):
            return False
        p = {m.group(1): (m.group(2) if m.group(2) is not None else m.group(3))
             for m in _AUTH_RE.finditer(header[7:])}
        ha2 = sha256_hex("POST:/rpc")
        expected = sha256_hex(":".join([self.ha1, p.get("nonce", ""), p.get("nc", ""), p.get("cnonce", ""),
                                        p.get("qop", ""), ha2]))
        return p.get("response") == expected and p.get("realm") == self.device_id

    # -- méthodes RPC ---------------------------------------------------------------------------
    def handle(self, method, params):
        if method in self.fail_methods:
            raise ValueError(self.fail_methods[method])
        comp_map = {"Sys": "sys", "Cloud": "cloud", "BLE": "ble", "MQTT": "mqtt", "Ws": "ws", "WiFi": "wifi"}
        comp, _, action = method.partition(".")
        if comp in comp_map and action == "GetConfig":
            return self._public(comp_map[comp])
        if comp in comp_map and action == "SetConfig":
            new = copy.deepcopy(params["config"])
            self._strip_secrets(comp_map[comp], new)
            deep_merge(self.config[comp_map[comp]], new)
            if comp == "WiFi" and self.move_on_wifi:
                self.blocked_ports.add(self.current_port)  # l'ancienne adresse ne répond plus
            return {"restart_required": comp in ("BLE", "WiFi")}
        if comp in ("Switch", "Input") and action in ("GetConfig", "SetConfig"):
            key = "%s:%d" % (comp.lower(), params["id"])
            if action == "SetConfig":
                deep_merge(self.config[key], params["config"])
                return {"restart_required": False}
            return copy.deepcopy(self.config[key])
        if method == "Shelly.GetDeviceInfo":
            return copy.deepcopy(self.info)
        if method == "Shelly.GetConfig":
            return {k: self._public(k) for k in self.config}
        if method == "WiFi.GetStatus":
            return {"sta_ip": self.sta_ip, "status": "got ip" if self.sta_ip else "disconnected",
                    "ssid": self.sta_ssid, "rssi": -58 if self.sta_ip else 0, "ap_client_count": 0 if self.sta_ip else 1}
        if method == "Sys.GetStatus":
            return {"uptime": int(time.time() - self.boot_time)}
        if method == "Shelly.GetStatus":
            return {"sys": {"uptime": 123}, "switch:0": {"output": False}}
        if method == "Shelly.SetAuth":
            if params.get("realm") != self.device_id:
                raise ValueError("realm invalide")
            self.ha1 = params.get("ha1")
            self.info["auth_en"] = bool(self.ha1)
            return None
        if method in ("Shelly.PutUserCA", "Shelly.PutTLSClientCert", "Shelly.PutTLSClientKey"):
            name = {"Shelly.PutUserCA": "user_ca", "Shelly.PutTLSClientCert": "client_cert",
                    "Shelly.PutTLSClientKey": "client_key"}[method]
            data = params.get("data") or ""
            self.files[name] = (self.files[name] + data) if params.get("append") else data
            return {"len": len(self.files[name])}
        if method == "Shelly.Reboot":
            self.rebooted += 1
            threading.Timer(0.1, self._do_reboot).start()
            return None
        if method == "Shelly.CheckForUpdate":
            if self.update_to:
                return {"stable": {"version": self.update_to, "build_id": "x"}}
            return {}
        if method == "Shelly.Update":
            if "url" in params:
                with urllib.request.urlopen(params["url"], timeout=5) as resp:
                    self.downloaded_firmware = resp.read()
            threading.Timer(0.3, self._finish_update).start()
            return None
        raise KeyError("Méthode inconnue : %s" % method)

    def _do_reboot(self):
        self.down_until = time.time() + 0.3
        self.boot_time = time.time() + 0.3

    def _finish_update(self):
        self.down_until = time.time() + 0.5
        new = self.update_to or "1.7.4"
        self.info["ver"] = new
        self.info["fw_id"] = "20250601-000000/%s-g123456" % new

    def _strip_secrets(self, comp, cfg):
        if comp == "mqtt" and "pass" in cfg:
            self.secret_values["mqtt"] = cfg.pop("pass")
        if comp == "wifi":
            for key in ("sta", "sta1", "ap"):
                if key in cfg and "pass" in cfg[key]:
                    self.secret_values[key] = cfg[key].pop("pass")

    def _public(self, key):
        return copy.deepcopy(self.config[key])


def main():
    parser = argparse.ArgumentParser(description="Faux Shelly Plus 1PM pour essais")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--password", default=None)
    parser.add_argument("--ver", default="1.7.1")
    parser.add_argument("--app", default="Plus1PM")
    args = parser.parse_args()
    fake = FakeShelly(password=args.password, ver=args.ver, app=args.app)
    addr = fake.start(args.port)
    print("Faux Shelly en écoute sur %s (Ctrl+C pour arrêter)" % addr)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        fake.stop()


if __name__ == "__main__":
    main()
