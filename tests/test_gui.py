# -*- coding: utf-8 -*-
"""Test de fumée de l'interface : construction des fenêtres et aller-retour des valeurs du formulaire."""
import gc
import unittest

from shelly_configurator import config_store
from shelly_configurator.provision import Result
from tests.helpers import TempHomeTestCase, full_config

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_DISPLAY = True
except Exception:  # pas d'affichage (serveur sans X11)
    HAS_DISPLAY = False


@unittest.skipUnless(HAS_DISPLAY, "affichage graphique indisponible")
class GuiSmokeTests(TempHomeTestCase):
    def setUp(self):
        TempHomeTestCase.setUp(self)
        import tkinter as tk
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        # Variables Tk détruites dans le fil principal, avant puis après la fenêtre racine (sinon Python 3.8
        # peut les libérer depuis un fil d'un faux Shelly : « Tcl_AsyncDelete … wrong thread »).
        gc.collect()
        self.root.destroy()
        self.root = None
        gc.collect()
        TempHomeTestCase.tearDown(self)

    def test_editor_roundtrip(self):
        from shelly_configurator.gui.editor import ConfigEditor
        for cfg in (config_store.default_config(), full_config()):
            editor = ConfigEditor(self.root, cfg)
            self.root.update_idletasks()
            collected, errors = editor.collect()
            self.assertEqual(errors, [])
            self.assertEqual(collected, cfg)
            editor.destroy()

    def test_editor_dependencies(self):
        from shelly_configurator.gui.editor import ConfigEditor
        cfg = config_store.default_config()
        editor = ConfigEditor(self.root, cfg)
        ssid = editor._by_path[("mqtt", "server")]
        self.assertIn("disabled", ssid.widgets[0].state())  # MQTT désactivé
        editor._by_path[("mqtt", "enable")].var.set(True)
        self.assertNotIn("disabled", ssid.widgets[0].state())
        editor._by_path[("mqtt", "apply")].var.set(False)
        self.assertIn("disabled", ssid.widgets[0].state())
        editor.destroy()

    def test_editor_confirm_mismatch(self):
        from shelly_configurator.gui.editor import ConfigEditor
        cfg = full_config()
        editor = ConfigEditor(self.root, cfg)
        editor._by_path[("auth", "password")].var.set("nouveau-mdp")
        _cfg, errors = editor.collect()
        self.assertTrue(any("confirmation" in e for e in errors))
        editor.destroy()

    def test_main_window_and_dialogs(self):
        from shelly_configurator.gui import dialogs
        from shelly_configurator.gui.main_window import MainWindow
        win = MainWindow(self.root, full_config())
        win.upsert_device("192.168.33.1", {"id": "shellyplus1pm-x", "model": "SNSW-001P16EU", "ver": "1.6.0",
                                           "app": "Plus1PM", "auth_en": False})
        self.assertIn("Firmware", win.tree.item("192.168.33.1", "values")[5])
        win.append_log("essai", "warning")
        win.set_progress(0.5, "étape")
        win.set_busy(True)
        win.set_busy(False)
        ok, bad = Result("10.0.0.1"), Result("10.0.0.2")
        ok.ok, bad.error = True, "boum"
        for dlg in (dialogs.SummaryDialog(self.root, [ok, bad]),
                    dialogs.FirmwareDialog(self.root, {"id": "x", "ver": "1.0.0"}),
                    dialogs.PasswordDialog(self.root, "h", "raison"),
                    dialogs.KeepOrChangeDialog(self.root, config_store.summary_lines(full_config())),
                    dialogs.PassphraseDialog(self.root, "t", "m", confirm=True),
                    dialogs.WifiRiskDialog(self.root, "192.168.1.23", "message", True, True)):
            self.root.update_idletasks()
            dlg.destroy()


if __name__ == "__main__":
    unittest.main()
