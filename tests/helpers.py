# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest

from shelly_configurator import paths


class TempHomeTestCase(unittest.TestCase):
    """Isole les fichiers de l'utilitaire dans un dossier temporaire."""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="shellycfg-test-")
        self._old = os.environ.get(paths.ENV_OVERRIDE)
        os.environ[paths.ENV_OVERRIDE] = self._home

    def tearDown(self):
        if self._old is None:
            os.environ.pop(paths.ENV_OVERRIDE, None)
        else:
            os.environ[paths.ENV_OVERRIDE] = self._old
        shutil.rmtree(self._home, ignore_errors=True)


def full_config():
    from shelly_configurator.config_store import default_config
    cfg = default_config()
    cfg["device"]["name"] = "Salon-{n}-{mac6}"
    cfg["wifi"]["sta"].update(ssid="MaBox", password="wifi-secret-123", ipv4mode="static",
                              ip="192.168.1.50", netmask="255.255.255.0", gw="192.168.1.1",
                              nameserver="192.168.1.1")
    cfg["ap"].update(enable=False)
    cfg["mqtt"].update(enable=True, server="broker.local:8883", user="shelly", password="mqtt-secret",
                       client_id="{id}", topic_prefix="maison/{id}", ssl_ca="user", use_client_cert=True)
    cfg["ws"].update(enable=True, server="wss://serveur.local/shelly", ssl_ca="builtin")
    cfg["auth"].update(enable=True, password="admin-secret-42")
    cfg["tls"].update(apply=True,
                      user_ca_pem="-----BEGIN CERTIFICATE-----\n" + "A" * 1500 + "\n-----END CERTIFICATE-----\n",
                      client_cert_pem="-----BEGIN CERTIFICATE-----\nBBBB\n-----END CERTIFICATE-----\n",
                      client_key_pem="-----BEGIN PRIVATE KEY-----\nCCCC\n-----END PRIVATE KEY-----\n")
    cfg["switch"].update(apply=True, initial_state="off", power_limit=3000)
    cfg["input"].update(apply=True, type="button")
    return cfg
