# -*- coding: utf-8 -*-
"""Pont entre le fil de travail (réseau) et le fil Tkinter (seul autorisé à toucher l'interface)."""
import queue
import threading

from ..log_setup import get_logger
from ..provision import UiCallbacks
from . import dialogs

log = get_logger("gui.bridge")


class TkBridge(UiCallbacks):
    def __init__(self, root, window):
        self.root = root
        self.window = window
        self.queue = queue.Queue()
        self.alive = True
        self._series_count = 1
        self._wifi_choice = None
        self.root.after(50, self._pump)

    def _pump(self):
        try:
            while True:
                func = self.queue.get_nowait()
                try:
                    func()
                except Exception:
                    log.exception("Erreur dans une mise à jour de l'interface")
        except queue.Empty:
            pass
        if self.alive:
            try:
                self.root.after(50, self._pump)
            except Exception:  # fenêtre détruite
                self.alive = False

    def post(self, func, *args, **kwargs):
        """Exécute func dans le fil Tkinter, sans attendre."""
        self.queue.put(lambda: func(*args, **kwargs))

    def call_sync(self, func, *args):
        """Exécute func dans le fil Tkinter et attend son résultat (dialogues)."""
        if threading.current_thread() is threading.main_thread():
            return func(*args)
        box = {}
        done = threading.Event()

        def runner():
            try:
                box["result"] = func(*args)
            except BaseException as exc:  # renvoyée au fil appelant
                box["error"] = exc
            finally:
                done.set()

        self.queue.put(runner)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("result")

    # -- UiCallbacks ----------------------------------------------------------------------------
    def log(self, message, level="info"):
        self.post(self.window.append_log, message, level)

    def progress(self, fraction, text):
        self.post(self.window.set_progress, fraction, text)

    def ask_password(self, host, reason):
        return self.call_sync(dialogs.ask_password, self.root, host, reason)

    def ask_firmware_update(self, info):
        return self.call_sync(dialogs.ask_firmware_update, self.root, info)

    # Choix « changement Wi-Fi » mémorisable pour le reste d'une série (remis à zéro par start_series)
    def start_series(self, count):
        self._series_count = count
        self._wifi_choice = None

    def ask_wifi_risk(self, host, message, danger):
        if self._wifi_choice:
            self.log("Changement Wi-Fi : choix mémorisé appliqué (%s)." % self._wifi_choice, "warning")
            return self._wifi_choice
        choice, remember = self.call_sync(dialogs.ask_wifi_risk, self.root, host, message, danger,
                                          self._series_count > 1)
        if remember and choice:
            self._wifi_choice = choice
        return choice
