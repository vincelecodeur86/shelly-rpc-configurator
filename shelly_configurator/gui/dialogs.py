# -*- coding: utf-8 -*-
"""Boîtes de dialogue : garder/modifier, mot de passe, mise à jour firmware, phrase de passe, bilan."""
import tkinter as tk
from tkinter import filedialog, ttk

from .. import firmware, paths
from .common import ModalDialog, open_folder


class KeepOrChangeDialog(ModalDialog):
    def __init__(self, parent, summary_lines):
        ModalDialog.__init__(self, parent, "Configuration enregistrée")
        ttk.Label(self.body, text="Une configuration est déjà enregistrée :",
                  font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        box = ttk.Frame(self.body, padding=(10, 8))
        box.pack(fill="x")
        for line in summary_lines or ["(aucune section active)"]:
            ttk.Label(box, text="• " + line, wraplength=520, justify="left").pack(anchor="w")
        ttk.Label(self.body, text="Voulez-vous la garder ou la modifier ?").pack(anchor="w", pady=(6, 10))
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x")
        keep = ttk.Button(buttons, text="Garder cette configuration", command=lambda: self.done("keep"))
        keep.pack(side="left")
        ttk.Button(buttons, text="Modifier…", command=lambda: self.done("change")).pack(side="left", padx=8)
        ttk.Button(buttons, text="Quitter", command=self.cancel).pack(side="right")
        keep.focus_set()
        self.bind("<Return>", lambda e: self.done("keep"))
        self.bind("<Escape>", self.cancel)


class PasswordDialog(ModalDialog):
    def __init__(self, parent, host, reason):
        ModalDialog.__init__(self, parent, "Mot de passe du Shelly")
        ttk.Label(self.body, text=reason, wraplength=380, justify="left").pack(anchor="w")
        ttk.Label(self.body, text="Mot de passe actuel de l'utilisateur « admin » du Shelly %s :" % host,
                  wraplength=380, justify="left").pack(anchor="w", pady=(10, 4))
        self.var = tk.StringVar()
        entry = ttk.Entry(self.body, textvariable=self.var, show="*", width=40)
        entry.pack(anchor="w")
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Valider", command=self._ok).pack(side="right")
        ttk.Button(buttons, text="Abandonner", command=self.cancel).pack(side="right", padx=8)
        entry.focus_set()
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", self.cancel)

    def _ok(self):
        self.done(self.var.get() or None)


class FirmwareDialog(ModalDialog):
    def __init__(self, parent, info):
        ModalDialog.__init__(self, parent, "Firmware trop ancien")
        text = ("Le Shelly %s est en firmware %s.\nLa version minimale requise est %s.\n\n"
                "Voulez-vous le mettre à jour maintenant ?\n\n"
                "• En ligne : le Shelly télécharge la dernière version stable (il doit avoir accès à Internet).\n"
                "• Depuis un fichier : choisissez un firmware .zip pour Shelly Plus 1PM téléchargé sur le site "
                "Shelly ; ce PC le sert au Shelly sur le réseau local (le pare-feu doit l'autoriser).\n\n"
                "Ne coupez pas l'alimentation du Shelly pendant la mise à jour."
                % (info.get("id", "?"), info.get("ver", "?"), firmware.MIN_FIRMWARE_STR))
        ttk.Label(self.body, text=text, wraplength=470, justify="left").pack(anchor="w")
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Mettre à jour en ligne", command=lambda: self.done(("online", None))).pack(side="left")
        ttk.Button(buttons, text="Depuis un fichier…", command=self._file).pack(side="left", padx=8)
        ttk.Button(buttons, text="Annuler", command=self.cancel).pack(side="right")

    def _file(self):
        path = filedialog.askopenfilename(parent=self, title="Firmware Shelly Plus 1PM",
                                          filetypes=[("Firmware Shelly", "*.zip"), ("Tous les fichiers", "*.*")])
        if path:
            self.done(("file", path))


class WifiRiskDialog(ModalDialog):
    """Le changement Wi-Fi va couper la connexion en cours : continuer, ignorer le Wi-Fi ou annuler."""

    def __init__(self, parent, host, message, danger, allow_remember):
        ModalDialog.__init__(self, parent, "Changement de réseau Wi-Fi – Shelly %s" % host)
        ttk.Label(self.body, text=message, wraplength=520, justify="left",
                  foreground="#b00020" if danger else "").pack(anchor="w")
        self.remember = tk.BooleanVar(value=False)
        if allow_remember:
            ttk.Checkbutton(self.body, text="Appliquer ce choix aux Shelly suivants de la série",
                            variable=self.remember).pack(anchor="w", pady=(12, 0))
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=(14, 0))
        ttk.Button(buttons, text="Continuer", command=lambda: self._choose("continue")).pack(side="left")
        ttk.Button(buttons, text="Appliquer sans le Wi-Fi",
                   command=lambda: self._choose("skip_wifi")).pack(side="left", padx=8)
        cancel = ttk.Button(buttons, text="Annuler ce Shelly", command=self.cancel)
        cancel.pack(side="right")
        cancel.focus_set()  # choix par défaut le plus prudent
        self.bind("<Escape>", self.cancel)

    def _choose(self, choice):
        self.done((choice, bool(self.remember.get())))

    def cancel(self, _event=None):
        self.done((None, bool(self.remember.get())))


class PassphraseDialog(ModalDialog):
    def __init__(self, parent, title, message, confirm=False, allow_empty=False):
        ModalDialog.__init__(self, parent, title)
        self.confirm = confirm
        self.allow_empty = allow_empty
        ttk.Label(self.body, text=message, wraplength=420, justify="left").pack(anchor="w")
        self.var1, self.var2 = tk.StringVar(), tk.StringVar()
        form = ttk.Frame(self.body)
        form.pack(fill="x", pady=8)
        ttk.Label(form, text="Phrase de passe :").grid(row=0, column=0, sticky="w")
        first = ttk.Entry(form, textvariable=self.var1, show="*", width=34)
        first.grid(row=0, column=1, padx=6, pady=2)
        if confirm:
            ttk.Label(form, text="Confirmation :").grid(row=1, column=0, sticky="w")
            ttk.Entry(form, textvariable=self.var2, show="*", width=34).grid(row=1, column=1, padx=6, pady=2)
        self.error = ttk.Label(self.body, text="", foreground="#b00020")
        self.error.pack(anchor="w")
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Valider", command=self._ok).pack(side="right")
        ttk.Button(buttons, text="Annuler", command=self.cancel).pack(side="right", padx=8)
        first.focus_set()
        self.bind("<Return>", lambda e: self._ok())
        self.bind("<Escape>", self.cancel)

    def _ok(self):
        value = self.var1.get()
        if not value and not self.allow_empty:
            self.error.configure(text="Phrase de passe obligatoire.")
            return
        if value and len(value) < 6:
            self.error.configure(text="6 caractères minimum.")
            return
        if self.confirm and value != self.var2.get():
            self.error.configure(text="Les deux saisies sont différentes.")
            return
        self.done(value)


class SummaryDialog(ModalDialog):
    """Bilan d'une série de configurations (un appareil par ligne + détail)."""

    def __init__(self, parent, results):
        failed = [r for r in results if not r.ok]
        ModalDialog.__init__(self, parent, "Bilan : %d erreur(s)" % len(failed) if failed else "Bilan", resizable=True)
        head = ("%d appareil(s) configuré(s), %d en échec." % (len(results) - len(failed), len(failed)))
        ttk.Label(self.body, text=head, font=("TkDefaultFont", 10, "bold"),
                  foreground="#b00020" if failed else "#1b5e20").pack(anchor="w")
        cols = ("host", "id", "fw", "result")
        tree = ttk.Treeview(self.body, columns=cols, show="headings", height=min(max(len(results), 3), 10))
        for col, label, width in (("host", "Adresse", 130), ("id", "Identifiant", 200),
                                  ("fw", "Firmware", 110), ("result", "Résultat", 260)):
            tree.heading(col, text=label)
            tree.column(col, width=width, anchor="w")
        tree.tag_configure("fail", foreground="#b00020")
        tree.tag_configure("warn", foreground="#a15c00")
        for num, res in enumerate(results):
            fw = res.fw_after if res.fw_after == res.fw_before else "%s → %s" % (res.fw_before, res.fw_after)
            tag = "fail" if not res.ok else ("warn" if res.warnings else "")
            tree.insert("", "end", iid=str(num), values=(res.host, res.device_id, fw, res.summary()), tags=(tag,))
        tree.pack(fill="both", expand=True, pady=8)
        details = tk.Text(self.body, height=9, width=90, wrap="word")
        details.pack(fill="both", expand=True)
        for res in results:
            if res.error or res.warnings:
                details.insert("end", "%s (%s)\n" % (res.host, res.device_id or "?"))
                if res.error:
                    details.insert("end", "  ERREUR : %s\n" % res.error)
                for warn in res.warnings:
                    details.insert("end", "  Avertissement : %s\n" % warn)
            if res.backup_path:
                details.insert("end", "%s : sauvegarde %s\n" % (res.host, res.backup_path))
        details.configure(state="disabled")
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Ouvrir le dossier des journaux",
                   command=lambda: open_folder(paths.log_dir(), self)).pack(side="left")
        ttk.Button(buttons, text="Fermer", command=lambda: self.done(True)).pack(side="right")


# -- raccourcis appelés depuis le fil de travail (via TkBridge.call_sync) ------------------------
def ask_password(parent, host, reason):
    return PasswordDialog(parent, host, reason).show()


def ask_firmware_update(parent, info):
    return FirmwareDialog(parent, info).show()


def ask_wifi_risk(parent, host, message, danger, allow_remember=False):
    """Renvoie (choix, mémoriser) ; choix = 'continue', 'skip_wifi' ou None."""
    return WifiRiskDialog(parent, host, message, danger, allow_remember).show() or (None, False)


def ask_keep_or_change(parent, lines):
    return KeepOrChangeDialog(parent, lines).show()


def ask_passphrase(parent, title, message, confirm=False, allow_empty=False):
    return PassphraseDialog(parent, title, message, confirm, allow_empty).show()


def show_summary(parent, results):
    return SummaryDialog(parent, results).show()

