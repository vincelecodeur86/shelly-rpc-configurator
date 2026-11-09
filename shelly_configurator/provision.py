# -*- coding: utf-8 -*-
"""Déroulé complet de la configuration d'un Shelly :

1. lecture des infos, contrôle du type d'appareil et du firmware (>= 1.7.1, mise à jour proposée)
2. sauvegarde de la configuration actuelle
3. envoi des réglages (système, relais, TLS, MQTT, WebSocket), vérification par relecture
4. mot de passe administrateur, puis Wi-Fi en dernier (la connexion peut être coupée)
5. redémarrage si nécessaire
"""
import json
import os
import re
import time

from . import firmware, mdns, paths, wifi_safety
from .log_setup import get_logger
from .payloads import KIND_AUTH, KIND_TLS, KIND_WIFI, build_plan, diff_subset
from .rpc import ShellyAuthError, ShellyClient, ShellyConnectionError, ShellyError, ShellyRpcError

log = get_logger("provision")


class ProvisionError(Exception):
    pass


class ProvisionAborted(ProvisionError):
    pass


class UiCallbacks(object):
    """Interface entre le déroulé et l'interface graphique (implémentation par défaut : silencieuse)."""

    def log(self, message, level="info"):
        pass

    def progress(self, fraction, text):
        pass

    def ask_password(self, host, reason):
        """Renvoie le mot de passe actuel du Shelly, ou None pour abandonner."""
        return None

    def ask_firmware_update(self, info):
        """Renvoie ('online', None), ('file', chemin) ou None (refus)."""
        return None

    def ask_wifi_risk(self, host, message, danger):
        """Changement Wi-Fi qui va couper la connexion : 'continue', 'skip_wifi' ou None (annuler)."""
        return "continue"


class Result(object):
    def __init__(self, host):
        self.host = host
        self.ok = False
        self.device_id = ""
        self.model = ""
        self.fw_before = ""
        self.fw_after = ""
        self.backup_path = ""
        self.warnings = []
        self.error = ""
        self.rebooted = False
        self.connection = ""      # wifi_safety.VIA_AP / VIA_STA / VIA_UNKNOWN
        self.new_host = ""        # adresse où le Shelly a été retrouvé après changement Wi-Fi
        self.wifi_skipped = False

    def summary(self):
        if self.ok:
            text = "OK"
            if self.warnings:
                text += " (%d avertissement(s))" % len(self.warnings)
            if self.new_host and self.new_host != self.host:
                text += " – désormais à %s" % self.new_host
            return text
        return "ÉCHEC : %s" % self.error


def _safe_filename(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text) or "shelly"


class Provisioner(object):
    def __init__(self, host, cfg, ui=None, index=1, client=None, sleep=time.sleep,
                 update_timeout=480, poll_interval=3.0, locator=None):
        self.host = host
        self.cfg = cfg
        self.ui = ui or UiCallbacks()
        self.index = index
        initial_pw = cfg["auth"]["password"] if cfg["auth"]["enable"] else None
        self._client_injected = client is not None
        self.client = client or ShellyClient(host, password=initial_pw)
        self.sleep = sleep
        self.update_timeout = update_timeout
        self.poll_interval = poll_interval
        # locator(info, wifi_config) -> adresses candidates où chercher le Shelly après un changement Wi-Fi
        self.locator = locator or self._default_locator
        self.result = Result(host)
        self._restart_required = False
        self._wifi_lost = False
        self._wifi_step = None
        self._wifi_sent = False
        self._wifi_verified = False
        self._info = {}

    # -- utilitaires --------------------------------------------------------------------------
    def _log(self, message, level="info"):
        getattr(log, "warning" if level == "warning" else ("error" if level == "error" else "info"))(
            "[%s] %s", self.host, message)
        self.ui.log(message, level)

    def _warn(self, message):
        self.result.warnings.append(message)
        self._log(message, "warning")

    def _call(self, method, params=None, timeout=None):
        """Appel RPC ; si le mot de passe est refusé, le demande à l'utilisateur (3 essais)."""
        attempts = 0
        while True:
            try:
                return self.client.call(method, params, timeout=timeout)
            except ShellyAuthError as exc:
                attempts += 1
                if attempts > 3:
                    raise ProvisionError("Authentification refusée par le Shelly après 3 essais.")
                self._log(str(exc), "warning")
                password = self.ui.ask_password(self.host, str(exc))
                if not password:
                    raise ProvisionAborted("Mot de passe du Shelly non fourni : configuration abandonnée.")
                self.client.set_password(password)

    # -- déroulé ------------------------------------------------------------------------------
    def run(self):
        try:
            self._run()
            self.result.ok = True
            self._log("Configuration terminée avec succès.", "success")
        except ProvisionAborted as exc:
            self.result.error = str(exc)
            self._log(str(exc), "error")
        except (ProvisionError, ShellyError) as exc:
            self.result.error = str(exc)
            self._log(str(exc), "error")
        except Exception as exc:  # erreur inattendue : trace complète dans le journal
            log.exception("Erreur inattendue sur %s", self.host)
            self.result.error = "Erreur inattendue : %s" % exc
            self.ui.log(self.result.error, "error")
        return self.result

    def _run(self):
        opts = self.cfg["options"]
        self.ui.progress(0.02, "Connexion au Shelly %s" % self.host)
        self._resolve_host()
        self._log("Connexion au Shelly %s…" % self.client.host)
        info = self._call("Shelly.GetDeviceInfo")
        self._info = info
        self._record_info(info)
        self._log("Appareil détecté : %s" % firmware.describe_device(info))

        if not firmware.is_supported_device(info):
            raise ProvisionError("Ce n'est pas un %s : %s. Configuration refusée."
                                 % (firmware.DEVICE_LABEL, firmware.describe_device(info)))
        if not firmware.firmware_ok(info):
            info = self._update_firmware(info)

        self.ui.progress(0.15, "Lecture de la configuration actuelle")
        current = self._call("Shelly.GetConfig")
        if opts.get("backup_before", True):
            self._backup(info, current)

        wifi_status = self._wifi_status()
        via = wifi_safety.connection_path(self.client.host, wifi_status)
        self.result.connection = via
        if via == wifi_safety.VIA_STA:
            self._log("Connexion par le réseau Wi-Fi « %s » auquel le Shelly est connecté."
                      % (wifi_status.get("ssid") or "?"))
        elif via == wifi_safety.VIA_AP:
            self._log("Connexion par le point d'accès du Shelly.")
        else:
            self._log("Connexion par le réseau (le Shelly est connecté au Wi-Fi « %s », adresse %s)."
                      % (wifi_status.get("ssid") or "?", wifi_status.get("sta_ip") or "?"))

        steps = build_plan(self.cfg, info, self.index)
        wifi_steps = [s for s in steps if s.kind == KIND_WIFI]
        if wifi_steps:
            self._wifi_step = wifi_steps[0]
            if not self._confirm_wifi_change(self._wifi_step, current, wifi_status, via):
                steps.remove(self._wifi_step)
                self._wifi_step = None
                self.result.wifi_skipped = True
                self._warn("Réglages Wi-Fi et point d'accès non envoyés (choix de l'utilisateur).")
        if not steps:
            self._warn("Aucune section n'est cochée « Appliquer » : rien à envoyer.")
            return
        total = float(len(steps))
        verify = opts.get("verify_after", True)
        for num, step in enumerate(steps):
            self.ui.progress(0.2 + 0.7 * num / total, step.label)
            if step.kind == KIND_WIFI:
                self._apply_wifi(step, verify)
                continue
            self._log("Envoi : %s" % step.label)
            res = self._call(step.method, step.params)
            if isinstance(res, dict) and res.get("restart_required"):
                self._restart_required = True
            if step.kind == KIND_AUTH:
                new_pw = self.cfg["auth"]["password"] if self.cfg["auth"]["enable"] else None
                self.client.set_password(new_pw)
            if step.kind == KIND_TLS and isinstance(res, dict) and "len" in res:
                self._log("  %d octets reçus par le Shelly" % res["len"])
            if verify and step.verify_method:
                self._verify(step)

        self._finish(opts)

    def _resolve_host(self):
        """Noms « .local » : résolus par mDNS si le système ne sait pas le faire (Windows 7)."""
        if self._client_injected:
            return
        try:
            resolved = mdns.resolve_host(self.host)
        except ValueError as exc:
            raise ProvisionError(str(exc))
        if resolved != self.client.host:
            self._log("%s correspond à l'adresse %s" % (self.host, resolved))
            self.client = ShellyClient(resolved, password=self.client.password)

    def _wifi_status(self):
        try:
            status = self._call("WiFi.GetStatus")
            return status if isinstance(status, dict) else {}
        except ShellyRpcError as exc:
            log.warning("WiFi.GetStatus indisponible : %s", exc)
            return {}

    def _confirm_wifi_change(self, step, current, wifi_status, via):
        """True pour envoyer le Wi-Fi, False pour l'ignorer ; lève ProvisionAborted pour tout annuler."""
        check = wifi_safety.assess_wifi_change(step.params["config"], current, wifi_status, via, self.client.host)
        if not check.disruptive:
            return True
        self._log(check.message.replace("\n\n", " "), "warning")
        if not self.cfg["options"].get("confirm_wifi_risk", True):
            return True
        choice = self.ui.ask_wifi_risk(self.client.host, check.message, check.danger)
        if choice == "skip_wifi":
            return False
        if choice != "continue":
            raise ProvisionAborted("Configuration annulée avant toute modification du Shelly.")
        return True

    def _record_info(self, info):
        self.result.device_id = info.get("id", "")
        self.result.model = info.get("model", "")
        if not self.result.fw_before:
            self.result.fw_before = info.get("ver", "")
        self.result.fw_after = info.get("ver", "")

    def _verify(self, step):
        actual = self._call(step.verify_method, step.verify_params)
        diffs = diff_subset(step.expected, actual)
        if diffs:
            for diff in diffs:
                self._warn("Vérification %s : %s" % (step.label, diff))
        else:
            self._log("  vérifié : %s conforme" % step.label)

    def _apply_wifi(self, step, verify):
        self._log("Envoi : %s (envoyé en dernier, la connexion peut être coupée)" % step.label)
        try:
            res = self._call(step.method, step.params, timeout=15)
        except ShellyConnectionError:
            self._log("Connexion coupée pendant l'envoi du Wi-Fi (le Shelly change probablement de réseau).",
                      "warning")
            self._wifi_sent = True
            self._wifi_lost = True
            return
        self._wifi_sent = True
        if isinstance(res, dict) and res.get("restart_required"):
            self._restart_required = True
        if verify:
            try:
                self._verify(step)
                self._wifi_verified = True
            except ShellyConnectionError:
                self._log("Wi-Fi envoyé, mais le Shelly ne répond plus à cette adresse.", "warning")
                self._wifi_lost = True

    def _finish(self, opts):
        mode = opts.get("reboot", "required")
        reboot = mode == "always" or (mode == "required" and self._restart_required)
        if self._wifi_lost:
            if not self._relocate("après le changement Wi-Fi"):
                if reboot:
                    self._warn("Redémarrage non effectué (Shelly injoignable) : redémarrez-le manuellement "
                               "pour appliquer tous les réglages.")
                self.ui.progress(1.0, "Terminé")
                return
            if opts.get("verify_after", True) and not self._wifi_verified:
                self._verify_quietly(self._wifi_step)
        if reboot:
            self.ui.progress(0.95, "Redémarrage du Shelly")
            self._log("Redémarrage du Shelly…")
            rebooted_at = time.time()
            try:
                self._call("Shelly.Reboot", {"delay_ms": 1000})
            except ShellyConnectionError:
                self._log("Le Shelly n'a pas confirmé la demande de redémarrage.", "warning")
            self.result.rebooted = True
            if self._wifi_sent:
                # le Wi-Fi définitif n'est souvent actif qu'après redémarrage : on contrôle où il revient
                self._relocate("après redémarrage", wait_first=True, rebooted_at=rebooted_at)
        elif self._restart_required:
            self._warn("Le Shelly indique qu'un redémarrage est nécessaire pour appliquer certains réglages.")
        self.ui.progress(1.0, "Terminé")

    def _verify_quietly(self, step):
        if step is None:
            return
        try:
            self._verify(step)
            self._wifi_verified = True
        except ShellyError as exc:
            self._warn("Vérification Wi-Fi impossible : %s" % exc)

    # -- recherche du Shelly après changement Wi-Fi -------------------------------------------
    def _default_locator(self, info, wifi_cfg):
        hosts = wifi_safety.candidate_hosts(wifi_cfg, self.client.host)
        ip = mdns.resolve(info.get("id", ""), timeout=1.5) if info.get("id") else None
        if ip and ip not in hosts:
            hosts.append(ip)
        return hosts

    def _relocate(self, when, wait_first=False, rebooted_at=None):
        """Cherche le Shelly (même identifiant) ; met à jour self.client. Renvoie l'adresse ou None."""
        timeout = int(self.cfg["options"].get("relocate_timeout", 60) or 0)
        wifi_cfg = self._wifi_step.params["config"] if self._wifi_step else {}
        if timeout <= 0:
            self._warn("Le Shelly ne répond plus %s ; recherche désactivée dans les options." % when)
            return None
        self._log("Recherche du Shelly %s (jusqu'à %d s)…" % (when, timeout))
        deadline = time.time() + timeout
        if wait_first:
            self.sleep(max(self.poll_interval, 1.0))
        while True:
            remaining = int(deadline - time.time())
            self.ui.progress(0.97, "Recherche du Shelly (%d s)" % max(remaining, 0))
            for host in self.locator(self._info, wifi_cfg):
                client = ShellyClient(host, password=self.client.password, timeout=3)
                try:
                    found = client.get_device_info(timeout=3)
                except ShellyError:
                    continue
                if found.get("id") == self._info.get("id"):
                    if rebooted_at is not None and not self._has_rebooted_since(client, rebooted_at):
                        continue  # encore l'instance d'avant le redémarrage
                    self.client = client
                    self._wifi_lost = False
                    self.result.new_host = host
                    self._report_location(host, client)
                    return host
            if time.time() >= deadline:
                break
            self.sleep(self.poll_interval)
        ssids = [wifi_cfg.get(k, {}).get("ssid") for k in ("sta", "sta1")
                 if wifi_cfg.get(k, {}).get("enable")]
        self._warn("Shelly introuvable %s. C'est normal si ce PC n'est pas connecté au réseau %s ; "
                   "sinon vérifiez le nom et le mot de passe Wi-Fi saisis."
                   % (when, " ou ".join("« %s »" % s for s in ssids if s) or "configuré"))
        return None

    @staticmethod
    def _has_rebooted_since(client, rebooted_at):
        try:
            status = client.call("Sys.GetStatus", timeout=3) or {}
        except ShellyError:
            return True  # impossible de vérifier : on fait confiance
        uptime = status.get("uptime")
        if not isinstance(uptime, (int, float)):
            return True
        return uptime <= (time.time() - rebooted_at) + 2

    def _report_location(self, host, client):
        details = ""
        try:
            status = client.call("WiFi.GetStatus", timeout=3) or {}
            if status.get("ssid"):
                details = " (Wi-Fi « %s », signal %s dBm)" % (status.get("ssid"), status.get("rssi", "?"))
        except ShellyError:
            pass
        if host == self.result.host:
            self._log("Shelly joignable à la même adresse %s%s." % (host, details), "success")
        else:
            self._log("Shelly retrouvé à sa nouvelle adresse %s%s." % (host, details), "success")

    # -- sauvegarde ---------------------------------------------------------------------------
    def _backup(self, info, current_config):
        try:
            status = self._call("Shelly.GetStatus")
        except ShellyError as exc:
            status = {"erreur": str(exc)}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(paths.backup_dir(), "%s_%s.json" % (_safe_filename(info.get("id", self.host)), stamp))
        data = {"saved_at": time.strftime("%Y-%m-%d %H:%M:%S"), "host": self.host,
                "device_info": info, "config": current_config, "status": status}
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
        except OSError as exc:
            raise ProvisionError("Impossible d'écrire la sauvegarde %s : %s" % (path, exc))
        self.result.backup_path = path
        self._log("Configuration actuelle sauvegardée : %s" % path)

    # -- firmware -----------------------------------------------------------------------------
    def _update_firmware(self, info):
        self._log("Firmware %s inférieur au minimum requis %s."
                  % (info.get("ver"), firmware.MIN_FIRMWARE_STR), "warning")
        choice = self.ui.ask_firmware_update(info)
        if not choice:
            raise ProvisionError("Firmware %s trop ancien (minimum %s) et mise à jour refusée."
                                 % (info.get("ver"), firmware.MIN_FIRMWARE_STR))
        mode, file_path = choice
        old_ver = info.get("ver", "")
        server = None
        try:
            if mode == "online":
                self.ui.progress(0.05, "Recherche d'une mise à jour en ligne")
                avail = self._call("Shelly.CheckForUpdate", timeout=30) or {}
                stable = avail.get("stable")
                if not stable:
                    raise ProvisionError(
                        "Aucune mise à jour stable proposée en ligne. Le Shelly doit être connecté à un "
                        "réseau avec accès Internet ; sinon utilisez un fichier firmware (.zip).")
                self._log("Mise à jour en ligne vers %s…" % stable.get("version", "?"))
                self._call("Shelly.Update", {"stage": "stable"})
            elif mode == "file":
                server = firmware.LocalFileServer(file_path, self.client.host)
                url = server.start()
                self._log("Mise à jour depuis le fichier %s (servi sur %s)…" % (file_path, url))
                self._call("Shelly.Update", {"url": url})
            else:
                raise ProvisionError("Mode de mise à jour inconnu : %s" % mode)
            new_info = self._wait_for_new_firmware(old_ver)
        finally:
            if server is not None:
                server.stop()
        self._record_info(new_info)
        if not firmware.firmware_ok(new_info):
            raise ProvisionError("Après mise à jour, le firmware %s est toujours inférieur à %s."
                                 % (new_info.get("ver"), firmware.MIN_FIRMWARE_STR))
        self._log("Firmware mis à jour : %s → %s" % (old_ver, new_info.get("ver")), "success")
        return new_info

    def _wait_for_new_firmware(self, old_ver):
        deadline = time.time() + self.update_timeout
        old = firmware.parse_version(old_ver)
        self.sleep(self.poll_interval)
        while time.time() < deadline:
            remaining = int(deadline - time.time())
            self.ui.progress(0.1, "Mise à jour du firmware en cours (jusqu'à %d s)…" % max(remaining, 0))
            try:
                info = self.client.call("Shelly.GetDeviceInfo", timeout=4)
                if firmware.parse_version(info.get("ver")) != old:
                    return info
            except ShellyError:
                pass  # le Shelly redémarre : normal
            self.sleep(self.poll_interval)
        raise ProvisionError("La mise à jour du firmware n'a pas abouti dans le délai imparti (%d s)."
                             % self.update_timeout)


def read_device(host, password=None, timeout=5.0):
    """Lecture rapide des infos d'un Shelly (bouton « Lire les infos »)."""
    client = ShellyClient(host, password=password, timeout=timeout)
    info = client.get_device_info()
    checks = {
        "supported": firmware.is_supported_device(info),
        "firmware_ok": firmware.firmware_ok(info),
    }
    return info, checks
