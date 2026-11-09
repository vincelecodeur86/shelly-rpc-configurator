# -*- coding: utf-8 -*-
import json
import os
import tempfile
import unittest

from shelly_configurator import config_store, discovery, firmware, payloads, secrets_store
from shelly_configurator.provision import Provisioner, UiCallbacks
from shelly_configurator.rpc import (ShellyAuthError, ShellyClient, ShellyConnectionError,
                                     ShellyRpcError, parse_www_authenticate, redact)
from tests.fake_shelly import FakeShelly
from tests.helpers import TempHomeTestCase, full_config


class RpcTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeShelly(password="pw-ok")
        self.addr = self.fake.start()

    def tearDown(self):
        self.fake.stop()

    def test_device_info_without_auth(self):
        info = ShellyClient(self.addr).get_device_info()
        self.assertEqual(info["app"], "Plus1PM")

    def test_digest_auth_ok(self):
        client = ShellyClient(self.addr, password="pw-ok")
        self.assertIn("sys", client.call("Shelly.GetConfig"))
        self.assertIn("mqtt", client.call("Shelly.GetConfig"))  # nonce réutilisé

    def test_missing_password(self):
        with self.assertRaises(ShellyAuthError):
            ShellyClient(self.addr).call("Shelly.GetConfig")

    def test_wrong_password(self):
        with self.assertRaises(ShellyAuthError):
            ShellyClient(self.addr, password="faux").call("Shelly.GetConfig")

    def test_rpc_error(self):
        with self.assertRaises(ShellyRpcError) as ctx:
            ShellyClient(self.addr, password="pw-ok").call("Nope.Method")
        self.assertEqual(ctx.exception.code, -105)

    def test_connection_error(self):
        with self.assertRaises(ShellyConnectionError):
            ShellyClient("127.0.0.1:1", timeout=1).call("Shelly.GetDeviceInfo")

    def test_parse_header_and_redact(self):
        p = parse_www_authenticate('Digest qop="auth", realm="r1", nonce="123", algorithm=SHA-256')
        self.assertEqual(p, {"qop": "auth", "realm": "r1", "nonce": "123", "algorithm": "SHA-256"})
        self.assertEqual(redact({"config": {"pass": "x", "ssid": "y"}, "data": "abc"}),
                         {"config": {"pass": "***", "ssid": "y"}, "data": "<3 caractères>"})


class SecretsTests(TempHomeTestCase):
    def test_local_roundtrip(self):
        enc = secrets_store.encrypt_local("motdepasse é")
        self.assertTrue(enc.startswith("enc:"))
        self.assertNotIn("motdepasse", enc)
        self.assertEqual(secrets_store.decrypt_local(enc), "motdepasse é")

    def test_lk1_roundtrip_and_tamper(self):
        key = os.urandom(32)
        blob = secrets_store.seal(key, b"secret")
        self.assertEqual(secrets_store.unseal(key, blob), b"secret")
        bad = bytearray(blob)
        bad[20] ^= 1
        with self.assertRaises(secrets_store.SecretError):
            secrets_store.unseal(key, bytes(bad))

    def test_passphrase(self):
        enc = secrets_store.encrypt_with_passphrase("abc", "phrase")
        self.assertEqual(secrets_store.decrypt_with_passphrase(enc, "phrase"), "abc")
        with self.assertRaises(secrets_store.SecretError):
            secrets_store.decrypt_with_passphrase(enc, "autre")


class ConfigTests(TempHomeTestCase):
    def test_save_load_encrypted(self):
        cfg = full_config()
        config_store.save_config(cfg)
        with open(os.path.join(self._home, "config.json"), encoding="utf-8") as fh:
            raw = fh.read()
        for secret in ("wifi-secret-123", "mqtt-secret", "admin-secret-42", "CCCC"):
            self.assertNotIn(secret, raw)
        loaded = config_store.load_config()
        self.assertEqual(loaded["mqtt"]["password"], "mqtt-secret")
        self.assertEqual(loaded["tls"]["client_key_pem"], cfg["tls"]["client_key_pem"])

    def test_merge_defaults_adds_missing(self):
        merged = config_store.merge_defaults({"mqtt": {"server": "x"}})
        self.assertEqual(merged["mqtt"]["server"], "x")
        self.assertIn("ws", merged)

    def test_export_import(self):
        cfg = full_config()
        dest = os.path.join(self._home, "export.json")
        config_store.export_config(cfg, dest, passphrase="phrase")
        self.assertTrue(config_store.export_needs_passphrase(dest))
        with self.assertRaises(config_store.ConfigError):
            config_store.import_config(dest, passphrase="mauvaise")
        imported = config_store.import_config(dest, passphrase="phrase")
        self.assertEqual(imported["auth"]["password"], "admin-secret-42")
        config_store.export_config(cfg, dest, passphrase=None)
        self.assertEqual(config_store.import_config(dest)["auth"]["password"], "")

    def test_validation(self):
        errors, _ = config_store.validate_config(full_config())
        self.assertEqual(errors, [])
        cfg = config_store.default_config()
        cfg["wifi"]["sta"]["ssid"] = ""
        cfg["mqtt"].update(enable=True, server="host:99999")
        cfg["device"]["name"] = "x-{inconnu}"
        cfg["ws"].update(enable=True, server="http://x")
        errors, _ = config_store.validate_config(cfg)
        joined = "\n".join(errors)
        for needle in ("SSID", "port invalide", "variable inconnue", "ws://", "administrateur"):
            self.assertIn(needle, joined)


class PayloadTests(unittest.TestCase):
    INFO = {"id": "shellyplus1pm-a8032ab12345", "mac": "A8032AB12345", "ver": "1.7.1", "app": "Plus1PM"}

    def test_plan_order_and_templates(self):
        steps = payloads.build_plan(full_config(), self.INFO, index=3)
        methods = [s.method for s in steps]
        self.assertEqual(methods[-1], "WiFi.SetConfig")
        self.assertEqual(methods[-2], "Shelly.SetAuth")
        self.assertLess(methods.index("Shelly.PutUserCA"), methods.index("MQTT.SetConfig"))
        self.assertEqual(methods.count("Shelly.PutUserCA"), 2)  # PEM > 1024 caractères : 2 morceaux
        sys_step = steps[0]
        self.assertEqual(sys_step.params["config"]["device"]["name"], "Salon-3-B12345")
        mqtt = [s for s in steps if s.method == "MQTT.SetConfig"][0].params["config"]
        self.assertEqual(mqtt["topic_prefix"], "maison/shellyplus1pm-a8032ab12345")
        self.assertEqual(mqtt["ssl_ca"], "user_ca.pem")
        wifi = steps[-1].params["config"]
        self.assertEqual(wifi["sta"]["ip"], "192.168.1.52")  # 50 + (3 - 1)

    def test_ha1(self):
        auth = [s for s in payloads.build_plan(full_config(), self.INFO) if s.method == "Shelly.SetAuth"][0]
        self.assertEqual(auth.params["ha1"],
                         payloads.compute_ha1(self.INFO["id"], "admin-secret-42"))

    def test_diff(self):
        self.assertEqual(payloads.diff_subset({"a": 1, "b": {"pass": "x", "c": 2.0}},
                                              {"a": 1, "b": {"c": 2}}), [])
        self.assertEqual(len(payloads.diff_subset({"a": 1}, {"a": 2})), 1)


class FirmwareTests(unittest.TestCase):
    def test_versions(self):
        self.assertEqual(firmware.parse_version("1.7.1"), (1, 7, 1))
        self.assertEqual(firmware.parse_version("20250101-000000/1.10.0-gabc"), (1, 10, 0))
        self.assertEqual(firmware.parse_version("1.7.1-beta2"), (1, 7, 1))
        self.assertFalse(firmware.firmware_ok({"ver": "1.6.9"}))
        self.assertTrue(firmware.firmware_ok({"ver": "1.10.0"}))
        self.assertTrue(firmware.firmware_ok({"ver": "2.0.0"}))

    def test_device_type(self):
        self.assertTrue(firmware.is_supported_device({"app": "Plus1PM"}))
        self.assertFalse(firmware.is_supported_device({"app": "Plus1PMMini", "model": "SNSW-001P8EU"}))


class RecordingUi(UiCallbacks):
    def __init__(self, password=None, fw_choice=None, wifi_choice="continue"):
        self.messages = []
        self.password = password
        self.fw_choice = fw_choice
        self.wifi_choice = wifi_choice
        self.wifi_asked = []
        self.password_asked = 0

    def ask_wifi_risk(self, host, message, danger):
        self.wifi_asked.append((message, danger))
        return self.wifi_choice

    def log(self, message, level="info"):
        self.messages.append((level, message))

    def ask_password(self, host, reason):
        self.password_asked += 1
        return self.password

    def ask_firmware_update(self, info):
        return self.fw_choice


class ProvisionTests(TempHomeTestCase):
    def run_prov(self, fake, cfg=None, ui=None, index=1):
        addr = fake.start()
        try:
            prov = Provisioner(addr, cfg or full_config(), ui or RecordingUi(), index=index,
                               sleep=lambda s: __import__("time").sleep(0.2), poll_interval=0.2,
                               update_timeout=10, locator=lambda info, wifi: [addr])
            return prov.run()
        finally:
            fake.stop()

    def test_full_provisioning(self):
        fake = FakeShelly()
        ui = RecordingUi()
        res = self.run_prov(fake, ui=ui)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.warnings, [])
        self.assertEqual(fake.config["mqtt"]["server"], "broker.local:8883")
        self.assertEqual(fake.secret_values["mqtt"], "mqtt-secret")
        self.assertEqual(fake.config["wifi"]["sta"]["ip"], "192.168.1.50")
        self.assertEqual(fake.config["switch:0"]["initial_state"], "off")
        self.assertEqual(fake.config["input:0"]["type"], "button")
        self.assertEqual(fake.files["user_ca"], payloads.normalize_pem(full_config()["tls"]["user_ca_pem"]))
        self.assertTrue(fake.info["auth_en"])
        self.assertEqual(fake.rebooted, 1)  # BLE/WiFi demandent un redémarrage
        self.assertTrue(os.path.isfile(res.backup_path))
        with open(res.backup_path, encoding="utf-8") as fh:
            self.assertIn("config", json.load(fh))
        # désormais protégé par le nouveau mot de passe
        with self.assertRaises(ShellyAuthError):
            ShellyClient(fake.start()).call("Shelly.GetConfig")
        fake.stop()

    def test_wrong_device_type(self):
        res = self.run_prov(FakeShelly(app="Plus2PM", model="SNSW-102P16EU"))
        self.assertFalse(res.ok)
        self.assertIn("Plus 1PM", res.error)

    def test_old_firmware_refused(self):
        res = self.run_prov(FakeShelly(ver="1.6.2"), ui=RecordingUi(fw_choice=None))
        self.assertFalse(res.ok)
        self.assertIn("trop ancien", res.error)

    def test_old_firmware_online_update(self):
        fake = FakeShelly(ver="1.6.2", update_to="1.7.4")
        res = self.run_prov(fake, ui=RecordingUi(fw_choice=("online", None)))
        self.assertTrue(res.ok, res.error)
        self.assertEqual((res.fw_before, res.fw_after), ("1.6.2", "1.7.4"))

    def test_old_firmware_file_update(self):
        fw = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=self._home)
        fw.write(b"FIRMWARE-BYTES")
        fw.close()
        fake = FakeShelly(ver="1.5.0", update_to="1.7.1")
        res = self.run_prov(fake, ui=RecordingUi(fw_choice=("file", fw.name)))
        self.assertTrue(res.ok, res.error)
        self.assertEqual(fake.downloaded_firmware, b"FIRMWARE-BYTES")

    def test_existing_password_prompt(self):
        fake = FakeShelly(password="ancien")
        ui = RecordingUi(password="ancien")
        res = self.run_prov(fake, ui=ui)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(ui.password_asked, 1)

    def test_password_prompt_cancelled(self):
        res = self.run_prov(FakeShelly(password="ancien"), ui=RecordingUi(password=None))
        self.assertFalse(res.ok)
        self.assertIn("abandonnée", res.error)

    def test_verification_mismatch_is_warning(self):
        fake = FakeShelly()
        original = fake.handle

        def lying(method, params):
            out = original(method, params)
            if method == "MQTT.GetConfig":
                out["server"] = "autre:1883"
            return out
        fake.handle = lying
        res = self.run_prov(fake)
        self.assertTrue(res.ok)
        self.assertTrue(any("MQTT" in w and "server" in w for w in res.warnings))

    def test_rpc_error_fails(self):
        fake = FakeShelly()
        fake.fail_methods["MQTT.SetConfig"] = "Invalid argument 'server'"
        res = self.run_prov(fake)
        self.assertFalse(res.ok)
        self.assertIn("MQTT.SetConfig", res.error)


class DiscoveryTests(unittest.TestCase):
    def test_probe_and_scan(self):
        fake = FakeShelly(password="x")
        addr = fake.start()
        try:
            found = discovery.scan([addr, "127.0.0.1:1"], timeout=1)
            self.assertEqual([f["host"] for f in found], [addr])
            self.assertEqual(found[0]["app"], "Plus1PM")
        finally:
            fake.stop()

    def test_parse_targets(self):
        self.assertEqual(len(discovery.parse_targets("192.168.1.0/24")), 254)
        self.assertEqual(discovery.parse_targets("10.0.0.1-3"), ["10.0.0.1", "10.0.0.2", "10.0.0.3"])
        with self.assertRaises(ValueError):
            discovery.parse_targets("10.0.0.0/8")


if __name__ == "__main__":
    unittest.main()
