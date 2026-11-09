# -*- coding: utf-8 -*-
"""Shelly joint par le réseau Wi-Fi (et non par son point d'accès) : détection, confirmation, relocalisation."""
import struct
import time
import unittest

from shelly_configurator import mdns, wifi_safety
from shelly_configurator.provision import Provisioner
from tests.fake_shelly import FakeShelly
from tests.helpers import TempHomeTestCase, full_config
from tests.test_core import RecordingUi


class ConnectionPathTests(unittest.TestCase):
    def test_paths(self):
        self.assertEqual(wifi_safety.connection_path("192.168.1.23", {"sta_ip": "192.168.1.23"}), "sta")
        self.assertEqual(wifi_safety.connection_path("192.168.33.1", {"sta_ip": "192.168.1.23"}), "ap")
        self.assertEqual(wifi_safety.connection_path("10.0.0.5:80", {"sta_ip": None}), "ap")
        self.assertEqual(wifi_safety.connection_path("10.0.0.5", {"sta_ip": "192.168.1.23"}), "unknown")

    def _cfg(self, **sta):
        base = {"enable": True, "ssid": "MaBox", "ipv4mode": "dhcp"}
        base.update(sta)
        return {"sta": base}

    def test_assessment(self):
        current = {"wifi": {"sta": {"ssid": "MaBox", "ipv4mode": "dhcp"}, "ap": {"enable": False}}}
        status = {"sta_ip": "192.168.1.23", "ssid": "MaBox"}
        same = wifi_safety.assess_wifi_change(self._cfg(), current, status, "sta", "192.168.1.23")
        self.assertFalse(same.disruptive)
        other = wifi_safety.assess_wifi_change(self._cfg(ssid="Autre"), current, status, "sta", "192.168.1.23")
        self.assertTrue(other.disruptive)
        self.assertTrue(other.danger)  # point d'accès désactivé
        self.assertIn("Autre", other.message)
        new_ip = dict(self._cfg(ipv4mode="static", ip="192.168.1.80"), ap={"enable": True})
        moved = wifi_safety.assess_wifi_change(new_ip, current, status, "sta", "192.168.1.23")
        self.assertTrue(moved.disruptive)
        self.assertFalse(moved.danger)
        self.assertIn("192.168.1.80", moved.message)
        via_ap = wifi_safety.assess_wifi_change(self._cfg(ssid="Autre"), current, status, "ap", "192.168.33.1")
        self.assertFalse(via_ap.disruptive)

    def test_candidates(self):
        wifi = {"sta": {"enable": True, "ipv4mode": "static", "ip": "192.168.1.80"}, "sta1": {"enable": False}}
        self.assertEqual(wifi_safety.candidate_hosts(wifi, "192.168.1.23"), ["192.168.1.80", "192.168.1.23"])


class MdnsTests(unittest.TestCase):
    def test_query_and_parse(self):
        query = mdns.build_query([("_shelly._tcp.local", mdns.TYPE_PTR)])
        self.assertIn(b"\x07_shelly\x04_tcp\x05local\x00", query)
        # réponse : PTR -> instance, puis A dont le nom est compressé (pointeur vers l'instance)
        header = struct.pack(">HHHHHH", 0, 0x8400, 0, 2, 0, 0)
        service = mdns.encode_name("_shelly._tcp.local")
        instance = mdns.encode_name("shellyplus1pm-abc._shelly._tcp.local")
        ptr = service + struct.pack(">HHIH", mdns.TYPE_PTR, 1, 120, len(instance)) + instance
        instance_offset = 12 + len(service) + 10
        a_rec = (struct.pack(">H", 0xC000 | instance_offset)
                 + struct.pack(">HHIH", mdns.TYPE_A, 1, 120, 4) + bytes([192, 168, 1, 42]))
        records = mdns.parse_packet(header + ptr + a_rec)
        self.assertIn(("_shelly._tcp.local", mdns.TYPE_PTR, "shellyplus1pm-abc._shelly._tcp.local"), records)
        self.assertIn(("shellyplus1pm-abc._shelly._tcp.local", mdns.TYPE_A, "192.168.1.42"), records)

    def test_resolve_host_passthrough(self):
        self.assertEqual(mdns.resolve_host("192.168.1.5"), "192.168.1.5")
        self.assertEqual(mdns.resolve_host("127.0.0.1:8080"), "127.0.0.1:8080")
        with self.assertRaises(ValueError):
            mdns.resolve_host("shelly-inexistant-zz.local", timeout=0.5)


class WifiProvisionTests(TempHomeTestCase):
    """Le PC joint le faux Shelly « par son Wi-Fi » : sta_ip = 127.0.0.1 (l'adresse utilisée)."""

    def _run(self, fake, ui, cfg=None, extra_hosts=None, timeout=5):
        addr = fake.start()
        hosts = [addr] + (extra_hosts or [])
        cfg = cfg or full_config()
        cfg["options"]["relocate_timeout"] = timeout
        try:
            return Provisioner(addr, cfg, ui, sleep=lambda s: time.sleep(0.2), poll_interval=0.2,
                               locator=lambda info, wifi: hosts).run(), fake
        finally:
            fake.stop()

    def test_cancel_changes_nothing(self):
        ui = RecordingUi(wifi_choice=None)
        res, fake = self._run(FakeShelly(sta_ip="127.0.0.1", sta_ssid="AncienWifi"), ui)
        self.assertFalse(res.ok)
        self.assertEqual(res.connection, "sta")
        self.assertEqual(len(ui.wifi_asked), 1)
        self.assertTrue(ui.wifi_asked[0][1])  # point d'accès désactivé dans la config : danger
        self.assertFalse([m for m, _p in fake.calls if m.endswith("SetConfig") or m == "Shelly.SetAuth"])

    def test_skip_wifi(self):
        res, fake = self._run(FakeShelly(sta_ip="127.0.0.1", sta_ssid="AncienWifi"),
                              RecordingUi(wifi_choice="skip_wifi"))
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.wifi_skipped)
        self.assertEqual(fake.config["wifi"]["sta"]["ssid"], "AncienWifi")
        self.assertEqual(fake.config["mqtt"]["server"], "broker.local:8883")

    def test_no_question_when_wifi_unchanged(self):
        cfg = full_config()
        cfg["wifi"]["sta"].update(ssid="MaBox", ipv4mode="dhcp")
        ui = RecordingUi(wifi_choice=None)
        res, _fake = self._run(FakeShelly(sta_ip="127.0.0.1", sta_ssid="MaBox"), ui, cfg)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(ui.wifi_asked, [])

    def test_option_disables_question(self):
        cfg = full_config()
        cfg["options"]["confirm_wifi_risk"] = False
        ui = RecordingUi(wifi_choice=None)
        res, _fake = self._run(FakeShelly(sta_ip="127.0.0.1", sta_ssid="AncienWifi"), ui, cfg)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(ui.wifi_asked, [])

    def test_relocated_after_wifi_change(self):
        fake = FakeShelly(sta_ip="127.0.0.1", sta_ssid="AncienWifi", move_on_wifi=True)
        second = fake.start()  # « nouvelle adresse » du même Shelly (2e serveur, même état)
        res, fake = self._run(fake, RecordingUi(), extra_hosts=[second])
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.new_host, second)
        self.assertEqual(res.warnings, [])
        self.assertEqual(fake.rebooted, 1)
        self.assertIn("désormais à %s" % second, res.summary())

    def test_not_found_is_warning(self):
        res, fake = self._run(FakeShelly(sta_ip="127.0.0.1", sta_ssid="AncienWifi", move_on_wifi=True),
                              RecordingUi(), timeout=1)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(any("introuvable" in w and "MaBox" in w for w in res.warnings))
        self.assertEqual(fake.config["wifi"]["sta"]["ssid"], "MaBox")


if __name__ == "__main__":
    unittest.main()
