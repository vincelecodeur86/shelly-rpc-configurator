# -*- coding: utf-8 -*-
"""Fenêtre principale : liste des Shelly, recherche réseau, lancement de la configuration, journal."""
import copy
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .. import APP_TITLE, __version__, config_store, discovery, firmware, mdns, paths, wifi_safety
from ..log_setup import get_logger, log_file_path
from ..provision import Provisioner, read_device
from ..rpc import ShellyError
from . import dialogs
from .bridge import TkBridge
from .common import open_folder, show_error
from .editor import ConfigEditor

log = get_logger("gui.main")

LEVEL_COLORS = {"info": "#000000", "warning": "#a15c00", "error": "#b00020", "success": "#1b5e20"}


class MainWindow(object):
    def __init__(self, root, cfg):
        self.root = root
        self.cfg = cfg
        self.busy = False
        self.stop_event = None
        self.bridge = TkBridge(root, self)
        root.title("%s %s" % (APP_TITLE, __version__))
        root.minsize(760, 560)
        root.geometry("900x680")
        root.protocol("WM_DELETE_WINDOW", self.quit)
        self._build_menu()
        self._build_body()
        self.append_log("Configuration chargée depuis %s" % paths.config_file())
        self.append_log("Journal : %s" % log_file_path())

    # -- construction -------------------------------------------------------------------------
    def _build_menu(self):
        bar = tk.Menu(self.root)
        file_menu = tk.Menu(bar, tearoff=False)
        file_menu.add_command(label="Importer une configuration…", command=self.import_config)
        file_menu.add_command(label="Exporter la configuration…", command=self.export_config)
        file_menu.add_separator()
        file_menu.add_command(label="Ouvrir le dossier des journaux", command=lambda: open_folder(paths.log_dir(), self.root))
        file_menu.add_command(label="Ouvrir le dossier des sauvegardes", command=lambda: open_folder(paths.backup_dir(), self.root))
        file_menu.add_separator()
        file_menu.add_command(label="Quitter", command=self.quit)
        bar.add_cascade(label="Fichier", menu=file_menu)
        cfg_menu = tk.Menu(bar, tearoff=False)
        cfg_menu.add_command(label="Modifier la configuration…", command=self.edit_config)
        cfg_menu.add_command(label="Afficher le résumé", command=self.show_config_summary)
        bar.add_cascade(label="Configuration", menu=cfg_menu)
        help_menu = tk.Menu(bar, tearoff=False)
        help_menu.add_command(label="À propos", command=self.about)
        bar.add_cascade(label="Aide", menu=help_menu)
        self.root.configure(menu=bar)

    def _build_body(self):
        main = ttk.Frame(self.root, padding=10)
        main.pack(fill="both", expand=True)

        dev = ttk.LabelFrame(main, text="Shelly à configurer", padding=8)
        dev.pack(fill="both", expand=True)
        line1 = ttk.Frame(dev)
        line1.pack(fill="x")
        ttk.Label(line1, text="Adresse IP ou nom :").pack(side="left")
        self.host_var = tk.StringVar(value=self.cfg["options"].get("default_host") or discovery.SHELLY_AP_ADDRESS)
        host_entry = ttk.Entry(line1, textvariable=self.host_var, width=22)
        host_entry.pack(side="left", padx=6)
        host_entry.bind("<Return>", lambda e: self.add_manual())
        self.btn_add = ttk.Button(line1, text="Ajouter et lire les infos", command=self.add_manual)
        self.btn_add.pack(side="left")
        ttk.Label(dev, foreground="#666666", font=("TkDefaultFont", 8), justify="left",
                  text="192.168.33.1 si ce PC est connecté au point d'accès Wi-Fi du Shelly ; sinon l'adresse du "
                       "Shelly sur votre réseau Wi-Fi (voir votre box) ou son nom, ex. shellyplus1pm-a8032ab12345.local"
                  ).pack(anchor="w", pady=(2, 0))
        line2 = ttk.Frame(dev)
        line2.pack(fill="x", pady=(6, 0))
        ttk.Label(line2, text="Réseau à scanner :").pack(side="left")
        self.scan_var = tk.StringVar(value=", ".join(discovery.guess_networks()) or "192.168.1.0/24")
        ttk.Entry(line2, textvariable=self.scan_var, width=34).pack(side="left", padx=6)
        self.btn_scan = ttk.Button(line2, text="Rechercher les Shelly", command=self.scan)
        self.btn_scan.pack(side="left")
        self.btn_stop = ttk.Button(line2, text="Arrêter", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6)

        cols = ("host", "id", "model", "fw", "auth", "state")
        tree_frame = ttk.Frame(dev)
        tree_frame.pack(fill="both", expand=True, pady=(8, 0))
        self.tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=6, selectmode="extended")
        for col, label, width in (("host", "Adresse", 120), ("id", "Identifiant", 200), ("model", "Modèle", 120),
                                  ("fw", "Firmware", 80), ("auth", "Mot de passe", 90), ("state", "État", 220)):
            self.tree.heading(col, text=label)
            self.tree.column(col, width=width, anchor="w")
        for tag, color in LEVEL_COLORS.items():
            self.tree.tag_configure(tag, foreground=color)
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        line3 = ttk.Frame(dev)
        line3.pack(fill="x", pady=(6, 0))
        ttk.Button(line3, text="Tout sélectionner",
                   command=lambda: self.tree.selection_set(self.tree.get_children())).pack(side="left")
        ttk.Button(line3, text="Retirer de la liste", command=self.remove_selected).pack(side="left", padx=6)
        self.btn_read = ttk.Button(line3, text="Relire les infos", command=self.read_selected)
        self.btn_read.pack(side="left")
        ttk.Label(line3, text="N° du prochain appareil :").pack(side="left", padx=(20, 4))
        self.next_index = tk.StringVar(value="1")
        spin = tk.Spinbox(line3, from_=1, to=9999, textvariable=self.next_index, width=5)
        spin.pack(side="left")
        ttk.Label(line3, text="(variable {n}, incrément IP fixe)", foreground="#666666").pack(side="left", padx=4)

        actions = ttk.Frame(main)
        actions.pack(fill="x", pady=8)
        self.btn_run = ttk.Button(actions, text="▶ Configurer les Shelly sélectionnés", command=self.configure)
        self.btn_run.pack(side="left")
        self.btn_edit = ttk.Button(actions, text="Modifier la configuration…", command=self.edit_config)
        self.btn_edit.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(actions, mode="determinate", maximum=1.0, length=220)
        self.progress.pack(side="right")
        self.progress_label = ttk.Label(actions, text="", width=34, anchor="e")
        self.progress_label.pack(side="right", padx=6)

        log_frame = ttk.LabelFrame(main, text="Journal", padding=4)
        log_frame.pack(fill="both", expand=True)
        self.log_text = ScrolledText(log_frame, height=12, wrap="word", state="disabled", font=("TkFixedFont", 9))
        self.log_text.pack(fill="both", expand=True)
        for tag, color in LEVEL_COLORS.items():
            self.log_text.tag_configure(tag, foreground=color)

    # -- affichage ----------------------------------------------------------------------------
    def append_log(self, message, level="info"):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", "%s  %s\n" % (time.strftime("%H:%M:%S"), message), (level,))
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def set_progress(self, fraction, text):
        self.progress["value"] = max(0.0, min(1.0, fraction))
        self.progress_label.configure(text=text[:48])

    def set_busy(self, busy):
        self.busy = busy
        state = ["disabled"] if busy else ["!disabled"]
        for btn in (self.btn_add, self.btn_scan, self.btn_run, self.btn_edit, self.btn_read):
            btn.state(state)
        self.btn_stop.state(["!disabled"] if busy else ["disabled"])
        self.root.configure(cursor="watch" if busy else "")

    def upsert_device(self, host, info=None, state=None, level="info"):
        values = list(self.tree.item(host, "values")) if self.tree.exists(host) else [host, "", "", "", "", ""]
        if info:
            values[1] = info.get("id", "")
            values[2] = info.get("model", "")
            values[3] = info.get("ver", "")
            values[4] = "oui" if info.get("auth_en") else "non"
            if state is None:
                if not firmware.is_supported_device(info):
                    state, level = "Type non supporté (%s)" % info.get("app", "?"), "error"
                elif not firmware.firmware_ok(info):
                    state, level = "Firmware < %s (MAJ proposée)" % firmware.MIN_FIRMWARE_STR, "warning"
                else:
                    state, level = "Compatible", "success"
        if state is not None:
            values[5] = state
        if self.tree.exists(host):
            self.tree.item(host, values=values, tags=(level,))
        else:
            self.tree.insert("", "end", iid=host, values=values, tags=(level,))

    def _rename_row(self, old, new):
        if not self.tree.exists(old):
            return
        selected = old in self.tree.selection()
        values = list(self.tree.item(old, "values"))
        self.tree.delete(old)
        values[0] = new
        if not self.tree.exists(new):
            self.tree.insert("", "end", iid=new, values=values)
        if selected:
            self.tree.selection_add(new)

    def remove_selected(self):
        for item in self.tree.selection():
            self.tree.delete(item)

    # -- tâches de fond -----------------------------------------------------------------------
    def _start(self, target, *args):
        if self.busy:
            return
        self.set_busy(True)
        self.stop_event = threading.Event()

        def runner():
            try:
                target(*args)
            except Exception as exc:
                log.exception("Erreur dans la tâche de fond")
                self.bridge.post(show_error, self.root, "Erreur inattendue : %s" % exc)
            finally:
                self.bridge.post(self.set_busy, False)

        thread = threading.Thread(target=runner, name="travail")
        thread.daemon = True
        thread.start()

    def stop(self):
        if self.stop_event is not None:
            self.stop_event.set()
            self.append_log("Arrêt demandé (après l'appareil en cours)…", "warning")

    # lecture des infos
    def add_manual(self):
        host = self.host_var.get().strip()
        if not host:
            show_error(self.root, "Saisissez l'adresse IP du Shelly.", "Adresse manquante")
            return
        self.upsert_device(host, state="Lecture…")
        self.tree.selection_set([host])
        self._start(self._read_worker, [host], True)

    def read_selected(self):
        hosts = list(self.tree.selection())
        if not hosts:
            show_error(self.root, "Sélectionnez au moins un Shelly dans la liste.", "Aucune sélection")
            return
        self._start(self._read_worker, hosts, len(hosts) == 1)

    def _read_worker(self, hosts, popup):
        password = self.cfg["auth"]["password"] if self.cfg["auth"]["enable"] else None
        for host in hosts:
            if self.stop_event.is_set():
                break
            self.bridge.post(self.set_progress, 0.5, "Lecture de %s" % host)
            try:
                address = mdns.resolve_host(host)
            except ValueError as exc:
                self.bridge.post(self.upsert_device, host, None, "Nom introuvable", "error")
                self.bridge.log(str(exc), "error")
                if popup:
                    self.bridge.post(show_error, self.root, str(exc), "Shelly introuvable")
                continue
            if address != host:
                self.bridge.log("%s correspond à l'adresse %s" % (host, address))
                self.bridge.post(self._rename_row, host, address)
                host = address
            try:
                info, checks = read_device(host, password)
            except ShellyError as exc:
                log.warning("Lecture %s : %s", host, exc)
                self.bridge.post(self.upsert_device, host, None, "Injoignable", "error")
                self.bridge.log("%s : %s" % (host, exc), "error")
                if popup:
                    self.bridge.post(show_error, self.root, str(exc), "Shelly injoignable")
                continue
            self.bridge.post(self.upsert_device, host, info)
            self.bridge.log("%s : %s" % (host, firmware.describe_device(info)))
            if popup and not checks["supported"]:
                self.bridge.post(show_error, self.root, "L'appareil %s n'est pas un %s :\n%s"
                                 % (host, firmware.DEVICE_LABEL, firmware.describe_device(info)), "Appareil non supporté")
        self.bridge.post(self.set_progress, 0.0, "")

    # recherche réseau
    def scan(self):
        try:
            hosts = discovery.parse_targets(self.scan_var.get())
        except ValueError as exc:
            show_error(self.root, "Réseau à scanner invalide : %s\nExemple : 192.168.1.0/24" % exc, "Recherche")
            return
        if not hosts:
            show_error(self.root, "Indiquez un réseau à scanner, par exemple 192.168.1.0/24.", "Recherche")
            return
        if discovery.SHELLY_AP_ADDRESS not in hosts:
            hosts.append(discovery.SHELLY_AP_ADDRESS)
        self.append_log("Recherche des Shelly : annonces mDNS puis %d adresse(s)…" % len(hosts))
        self._start(self._scan_worker, hosts)

    def _scan_worker(self, hosts):
        def on_found(info):
            self.bridge.post(self.upsert_device, info["host"], info)

        def on_progress(done, total, text):
            if done % 8 == 0 or done == total:
                self.bridge.post(self.set_progress, float(done) / max(total, 1), text)

        found = discovery.find_shellies(hosts, on_found, on_progress, self.stop_event)
        plus = [f for f in found if firmware.is_supported_device(f)]
        self.bridge.log("Recherche terminée : %d Shelly trouvé(s), dont %d Plus 1PM." % (len(found), len(plus)),
                        "success" if found else "warning")
        self.bridge.post(self.set_progress, 0.0, "")

    # configuration
    def configure(self):
        hosts = list(self.tree.selection())
        if not hosts:
            if not self.tree.get_children() and self.host_var.get().strip():
                host = self.host_var.get().strip()
                self.upsert_device(host)
                hosts = [host]
            else:
                show_error(self.root, "Sélectionnez les Shelly à configurer dans la liste "
                                      "(Ctrl+clic pour en choisir plusieurs).", "Aucune sélection")
                return
        try:
            first_index = int(self.next_index.get())
        except ValueError:
            first_index = 1
        lines = config_store.summary_lines(self.cfg)
        if not messagebox.askyesno(
                APP_TITLE, "Configurer %d Shelly avec la configuration enregistrée ?\n\n%s\n\n• %s"
                % (len(hosts), ", ".join(hosts), "\n• ".join(lines)), parent=self.root):
            return
        self._start(self._configure_worker, hosts, copy.deepcopy(self.cfg), first_index)

    def _configure_worker(self, hosts, cfg, first_index):
        results = []
        index = first_index
        self.bridge.start_series(len(hosts))
        for host in hosts:
            if self.stop_event.is_set():
                self.bridge.log("Série interrompue par l'utilisateur.", "warning")
                break
            self.bridge.post(self.upsert_device, host, None, "Configuration en cours…", "info")
            self.bridge.log("===== Shelly %s (appareil n°%d) =====" % (host, index))
            result = Provisioner(host, cfg, self.bridge, index=index).run()
            results.append(result)
            if result.ok:
                level = "warning" if result.warnings else "success"
                self.bridge.post(self.upsert_device, host, None, result.summary(), level)
                if result.new_host and result.new_host != host:
                    self.bridge.post(self.upsert_device, result.new_host, None,
                                     "Configuré (anciennement %s)" % host, level)
                index += 1
                self.bridge.post(self.next_index.set, str(index))
            else:
                self.bridge.post(self.upsert_device, host, None, result.summary(), "error")
        self.bridge.post(self.set_progress, 0.0, "")
        self.bridge.post(self._finished, results, hosts)

    def _finished(self, results, hosts):
        if not results:
            return
        if len(results) > 1:
            dialogs.show_summary(self.root, results)
            return
        res = results[0]
        if not res.ok:
            show_error(self.root, "La configuration du Shelly %s a échoué :\n\n%s" % (res.host, res.error),
                       "Échec de la configuration")
        elif res.warnings:
            messagebox.showwarning(APP_TITLE, "Shelly %s configuré, avec des avertissements :\n\n• %s"
                                   % (res.host, "\n• ".join(res.warnings)), parent=self.root)
        else:
            messagebox.showinfo(APP_TITLE, "Shelly %s (%s) configuré avec succès." % (res.host, res.device_id),
                                parent=self.root)
        # enchaînement pratique pour les Shelly configurés un par un via leur point d'accès
        if res.ok and res.connection == wifi_safety.VIA_AP and messagebox.askyesno(
                APP_TITLE, "Configurer un autre Shelly à la même adresse (%s) ?\n\n"
                           "Connectez d'abord ce PC au point d'accès Wi-Fi du Shelly suivant, puis cliquez sur Oui."
                           % res.host, parent=self.root):
            try:
                index = int(self.next_index.get())
            except ValueError:
                index = 1
            self.upsert_device(res.host, state="En attente…")
            self._start(self._configure_worker, [res.host], copy.deepcopy(self.cfg), index)

    # -- configuration ------------------------------------------------------------------------
    def edit_config(self):
        if self.busy:
            return
        new_cfg = ConfigEditor(self.root, self.cfg).show()
        if new_cfg is None:
            return
        self._save(new_cfg)

    def _save(self, new_cfg):
        try:
            config_store.save_config(new_cfg)
        except Exception as exc:
            log.exception("Enregistrement de la configuration")
            show_error(self.root, "Impossible d'enregistrer la configuration : %s" % exc)
            return
        self.cfg = new_cfg
        self.append_log("Configuration enregistrée.", "success")

    def show_config_summary(self):
        messagebox.showinfo(APP_TITLE, "\n".join(config_store.summary_lines(self.cfg)) or "(aucune section active)",
                            parent=self.root)

    def export_config(self):
        dest = filedialog.asksaveasfilename(parent=self.root, title="Exporter la configuration",
                                            defaultextension=".json", initialfile="shelly-config-export.json",
                                            filetypes=[("Export Shelly Configurator", "*.json")])
        if not dest:
            return
        passphrase = dialogs.ask_passphrase(
            self.root, "Protection de l'export",
            "Choisissez une phrase de passe pour chiffrer les mots de passe et la clé privée dans le fichier "
            "exporté. Elle sera demandée à l'import.\n\nLaissez vide pour exporter SANS aucun mot de passe.",
            confirm=True, allow_empty=True)
        if passphrase is None:
            return
        try:
            config_store.export_config(self.cfg, dest, passphrase or None)
        except Exception as exc:
            log.exception("Export")
            show_error(self.root, "Export impossible : %s" % exc)
            return
        self.append_log("Configuration exportée vers %s%s" % (dest, "" if passphrase else " (sans mots de passe)"),
                        "success")

    def import_config(self):
        if self.busy:
            return
        src = filedialog.askopenfilename(parent=self.root, title="Importer une configuration",
                                         filetypes=[("Export Shelly Configurator", "*.json"), ("Tous", "*.*")])
        if not src:
            return
        try:
            passphrase = None
            if config_store.export_needs_passphrase(src):
                passphrase = dialogs.ask_passphrase(self.root, "Import", "Phrase de passe de ce fichier d'export :")
                if passphrase is None:
                    return
            new_cfg = config_store.import_config(src, passphrase)
        except config_store.ConfigError as exc:
            show_error(self.root, str(exc), "Import impossible")
            return
        self.append_log("Configuration importée depuis %s : vérifiez-la puis enregistrez." % src)
        edited = ConfigEditor(self.root, new_cfg).show()
        if edited is not None:
            self._save(edited)

    def about(self):
        messagebox.showinfo(APP_TITLE, "%s %s\n\nConfiguration automatisée des %s (firmware %s minimum) "
                                       "par RPC.\n\nConfiguration : %s\nJournaux : %s\nSauvegardes : %s"
                            % (APP_TITLE, __version__, firmware.DEVICE_LABEL, firmware.MIN_FIRMWARE_STR,
                               paths.config_file(), paths.log_dir(), paths.backup_dir()), parent=self.root)

    def quit(self):
        if self.busy and not messagebox.askyesno(
                APP_TITLE, "Une opération est en cours. Quitter quand même ?\n"
                           "(Le Shelly en cours de configuration peut rester partiellement configuré.)",
                parent=self.root):
            return
        self.bridge.alive = False
        self.root.destroy()

