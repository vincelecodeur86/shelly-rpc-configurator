# -*- coding: utf-8 -*-
"""Éditeur de la configuration (onglets générés à partir d'une description des champs)."""
import copy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .. import APP_TITLE
from ..config_store import get_path, set_path, validate_config
from .common import ModalDialog, ScrollableFrame

TEMPLATE_HELP = "Variables possibles : {id} identifiant, {mac} adresse MAC, {mac6} 6 derniers caractères, {n} numéro"

TIMEZONES = ["Europe/Paris", "Europe/Brussels", "Europe/Luxembourg", "Europe/Zurich", "Europe/Monaco",
             "Europe/London", "Europe/Berlin", "Europe/Madrid", "Europe/Rome", "UTC",
             "America/Montreal", "America/Toronto", "America/New_York", "Africa/Casablanca",
             "Africa/Algiers", "Africa/Tunis", "Africa/Dakar", "Africa/Abidjan", "Indian/Reunion",
             "Indian/Mayotte", "America/Martinique", "America/Guadeloupe", "America/Cayenne",
             "Pacific/Noumea", "Pacific/Tahiti"]

SSL_CHOICES = [("none", "Pas de TLS"),
               ("noverify", "TLS sans vérification du certificat"),
               ("builtin", "TLS, CA intégrée au Shelly (ca.pem)"),
               ("user", "TLS, CA utilisateur (user_ca.pem, onglet Certificats)")]
IPV4_CHOICES = [("dhcp", "Automatique (DHCP)"), ("static", "Adresse IP fixe")]
IN_MODE_CHOICES = [("follow", "Interrupteur : le relais suit l'entrée"),
                   ("flip", "Interrupteur : bascule à chaque changement"),
                   ("momentary", "Bouton poussoir"),
                   ("detached", "Détaché (entrée indépendante du relais)")]
INITIAL_STATE_CHOICES = [("restore_last", "Dernier état connu"), ("off", "Éteint"), ("on", "Allumé"),
                         ("match_input", "Selon l'entrée")]
INPUT_TYPE_CHOICES = [("switch", "Interrupteur"), ("button", "Bouton poussoir")]
REBOOT_CHOICES = [("required", "Seulement si le Shelly le demande"), ("always", "Toujours"),
                  ("never", "Jamais")]


def F(kind, path, label="", **opts):
    spec = dict(opts)
    spec.update(kind=kind, path=path, label=label)
    return spec


def H(text):
    return {"kind": "header", "label": text, "path": None}


def N(text):
    return {"kind": "note", "label": text, "path": None}


def _sta_fields(key, title, ip_help=None):
    base = ("wifi", key)
    on = (base + ("enable",), True)
    static = (base + ("ipv4mode",), "static")
    return [
        H(title),
        F("bool", base + ("enable",), "Activer ce réseau"),
        F("str", base + ("ssid",), "Nom du réseau (SSID)", when=[on]),
        F("secret", base + ("password",), "Mot de passe (vide = réseau ouvert)", when=[on]),
        F("choice", base + ("ipv4mode",), "Adressage IP", choices=IPV4_CHOICES, when=[on]),
        F("str", base + ("ip",), "Adresse IP", when=[on, static], help=ip_help),
        F("str", base + ("netmask",), "Masque de sous-réseau", when=[on, static]),
        F("str", base + ("gw",), "Passerelle", when=[on, static]),
        F("str", base + ("nameserver",), "Serveur DNS (optionnel)", when=[on, static]),
    ]


MQTT_ON = (("mqtt", "enable"), True)
WS_ON = (("ws", "enable"), True)
AP_ON = (("ap", "enable"), True)
AUTH_ON = (("auth", "enable"), True)

TABS = [
    ("Appareil", [
        F("bool", ("device", "apply"), "Appliquer cette section", apply=True),
        F("str", ("device", "name"), "Nom de l'appareil (vide = inchangé)", help=TEMPLATE_HELP),
        F("combo", ("device", "timezone"), "Fuseau horaire", values=TIMEZONES),
        F("str", ("device", "sntp_server"), "Serveur de temps (SNTP)"),
        F("str", ("device", "lat"), "Latitude (optionnel)", help="Utilisée pour les horaires lever/coucher du soleil"),
        F("str", ("device", "lon"), "Longitude (optionnel)"),
        F("bool", ("device", "eco_mode"), "Mode éco (consommation réduite)"),
        F("bool", ("device", "discoverable"), "Visible par la découverte réseau (mDNS)"),
        H("Services"),
        F("bool", ("device", "cloud_enable"), "Cloud Shelly"),
        F("bool", ("device", "ble_enable"), "Bluetooth"),
        F("bool", ("device", "ble_rpc_enable"), "Commandes RPC par Bluetooth",
          when=[(("device", "ble_enable"), True)]),
    ]),
    ("Wi-Fi", [F("bool", ("wifi", "apply"), "Appliquer cette section", apply=True)]
     + _sta_fields("sta", "Réseau Wi-Fi principal",
                   "En série (plusieurs Shelly), incrémentée pour chaque appareil : voir l'onglet Options")
     + _sta_fields("sta1", "Réseau Wi-Fi de secours")
     + [H("Itinérance (roaming)"),
        F("int", ("wifi", "roam_rssi_thr"), "Seuil de signal (dBm)"),
        F("int", ("wifi", "roam_interval"), "Intervalle de recherche (s)")]),
    ("Point d'accès", [
        F("bool", ("ap", "apply"), "Appliquer cette section", apply=True),
        F("bool", ("ap", "enable"), "Activer le point d'accès Wi-Fi du Shelly"),
        N("Conseil : désactivez le point d'accès une fois le Shelly connecté à votre Wi-Fi. "
          "Attention : si vous configurez le Shelly via son point d'accès, la connexion sera coupée."),
        F("str", ("ap", "ssid"), "Nom du point d'accès (vide = nom par défaut)", when=[AP_ON]),
        F("bool", ("ap", "is_open"), "Point d'accès ouvert (sans mot de passe)", when=[AP_ON]),
        F("secret", ("ap", "password"), "Mot de passe (8 caractères min.)",
          when=[AP_ON, (("ap", "is_open"), False)]),
        F("bool", ("ap", "range_extender"), "Répéteur Wi-Fi (range extender)", when=[AP_ON]),
    ]),
    ("MQTT", [
        F("bool", ("mqtt", "apply"), "Appliquer cette section", apply=True),
        F("bool", ("mqtt", "enable"), "Activer MQTT"),
        F("str", ("mqtt", "server"), "Serveur (hôte:port)", when=[MQTT_ON], help="Exemple : 192.168.1.10:1883 ou broker.exemple.fr:8883"),
        F("str", ("mqtt", "client_id"), "Identifiant client (vide = par défaut)", when=[MQTT_ON], help=TEMPLATE_HELP),
        F("str", ("mqtt", "user"), "Utilisateur", when=[MQTT_ON]),
        F("secret", ("mqtt", "password"), "Mot de passe", when=[MQTT_ON]),
        F("str", ("mqtt", "topic_prefix"), "Préfixe des topics (vide = par défaut)", when=[MQTT_ON], help=TEMPLATE_HELP),
        F("choice", ("mqtt", "ssl_ca"), "Sécurité TLS", choices=SSL_CHOICES, when=[MQTT_ON]),
        F("bool", ("mqtt", "use_client_cert"), "Utiliser le certificat client (authentification mTLS)", when=[MQTT_ON]),
        F("bool", ("mqtt", "rpc_ntf"), "Publier les notifications RPC", when=[MQTT_ON]),
        F("bool", ("mqtt", "status_ntf"), "Publier les notifications d'état", when=[MQTT_ON]),
        F("bool", ("mqtt", "enable_rpc"), "Accepter les commandes RPC via MQTT", when=[MQTT_ON]),
        F("bool", ("mqtt", "enable_control"), "Accepter les commandes simples via MQTT", when=[MQTT_ON]),
    ]),
    ("WebSocket", [
        F("bool", ("ws", "apply"), "Appliquer cette section", apply=True),
        F("bool", ("ws", "enable"), "Activer le WebSocket sortant"),
        F("str", ("ws", "server"), "Adresse du serveur", when=[WS_ON], help="Exemple : ws://192.168.1.10:8080/shelly ou wss://serveur/ws"),
        F("choice", ("ws", "ssl_ca"), "Sécurité TLS (wss://)", choices=SSL_CHOICES, when=[WS_ON]),
    ]),
    ("Sécurité", [
        F("bool", ("auth", "apply"), "Appliquer cette section", apply=True),
        F("bool", ("auth", "enable"), "Protéger le Shelly par un mot de passe"),
        N("Utilisateur : admin. Le mot de passe n'est jamais envoyé en clair (hachage SHA-256)."),
        F("secret", ("auth", "password"), "Mot de passe administrateur", when=[AUTH_ON]),
        F("confirm", ("auth", "password"), "Confirmation du mot de passe", when=[AUTH_ON]),
    ]),
    ("Certificats TLS", [
        F("bool", ("tls", "apply"), "Appliquer cette section", apply=True),
        N("Les certificats sont envoyés au Shelly puis utilisés par MQTT / WebSocket selon l'option TLS "
          "choisie. Un champ vide ne modifie pas le certificat présent sur le Shelly. Format PEM."),
        F("pem", ("tls", "user_ca_pem"), "CA utilisateur (user_ca.pem)"),
        F("pem", ("tls", "client_cert_pem"), "Certificat client"),
        F("pem", ("tls", "client_key_pem"), "Clé privée client (enregistrée chiffrée)"),
    ]),
    ("Relais / Entrée", [
        F("bool", ("switch", "apply"), "Appliquer les réglages du relais", apply=True),
        F("str", ("switch", "name"), "Nom du relais (vide = inchangé)", help=TEMPLATE_HELP),
        F("choice", ("switch", "in_mode"), "Mode de l'entrée", choices=IN_MODE_CHOICES),
        F("choice", ("switch", "initial_state"), "État à la mise sous tension", choices=INITIAL_STATE_CHOICES),
        F("bool", ("switch", "auto_on"), "Rallumage automatique"),
        F("float", ("switch", "auto_on_delay"), "Délai avant rallumage (s)", when=[(("switch", "auto_on"), True)]),
        F("bool", ("switch", "auto_off"), "Extinction automatique"),
        F("float", ("switch", "auto_off_delay"), "Délai avant extinction (s)", when=[(("switch", "auto_off"), True)]),
        F("int", ("switch", "power_limit"), "Limite de puissance (W)"),
        F("int", ("switch", "voltage_limit"), "Limite de surtension (V)"),
        F("int", ("switch", "undervoltage_limit"), "Seuil de sous-tension (V, 0 = désactivé)"),
        F("float", ("switch", "current_limit"), "Limite de courant (A)"),
        F("bool", ("switch", "autorecover_voltage_errors"), "Réarmement automatique après défaut de tension"),
        H("Entrée"),
        F("bool", ("input", "apply"), "Appliquer les réglages de l'entrée", apply=True),
        F("str", ("input", "name"), "Nom de l'entrée (vide = inchangé)", help=TEMPLATE_HELP),
        F("choice", ("input", "type"), "Type de commande raccordée", choices=INPUT_TYPE_CHOICES),
        F("bool", ("input", "invert"), "Inverser l'entrée"),
    ]),
    ("Options", [
        H("Déroulé de la configuration"),
        F("bool", ("options", "backup_before"), "Sauvegarder la configuration du Shelly avant modification"),
        F("bool", ("options", "verify_after"), "Relire et vérifier chaque réglage après envoi"),
        F("choice", ("options", "reboot"), "Redémarrer le Shelly à la fin", choices=REBOOT_CHOICES),
        F("bool", ("options", "increment_static_ip"),
          "En série, ajouter le numéro de l'appareil à l'IP fixe (n°1 : IP saisie, n°2 : IP + 1…)"),
        F("str", ("options", "default_host"), "Adresse proposée par défaut",
          help="192.168.33.1 = point d'accès du Shelly (sortie d'usine) ; sinon son adresse sur votre réseau "
               "Wi-Fi ou son nom shellyplus1pm-xxxx.local"),
        H("Shelly joint par votre réseau Wi-Fi"),
        F("bool", ("options", "confirm_wifi_risk"),
          "Demander confirmation si le changement Wi-Fi va couper la connexion avec le Shelly"),
        F("int", ("options", "relocate_timeout"), "Délai de recherche du Shelly après changement Wi-Fi (s)",
          help="L'utilitaire cherche le Shelly à sa nouvelle adresse fixe, à la même adresse et par son nom "
               ".local. 0 = pas de recherche."),
    ]),
]


class _Binding(object):
    def __init__(self, spec, var=None, text=None):
        self.spec = spec
        self.kind = spec["kind"]
        self.path = spec["path"]
        self.var = var
        self.text = text
        self.widgets = []
        choices = spec.get("choices") or []
        self.to_label = dict(choices)
        self.to_value = dict((label, value) for value, label in choices)

    def set(self, value):
        if self.kind == "bool":
            self.var.set(bool(value))
        elif self.kind == "choice":
            self.var.set(self.to_label.get(value, next(iter(self.to_label.values()))))
        elif self.kind == "pem":
            self.text.delete("1.0", "end")
            self.text.insert("1.0", value or "")
        else:
            self.var.set("" if value is None else str(value))

    def raw(self):
        if self.kind == "bool":
            return bool(self.var.get())
        if self.kind == "choice":
            return self.to_value.get(self.var.get())
        if self.kind == "pem":
            return self.text.get("1.0", "end-1c")
        return self.var.get()

    def get(self):
        value = self.raw()
        label = self.spec["label"]
        if self.kind == "int":
            try:
                return int(str(value).strip())
            except ValueError:
                raise ValueError("« %s » : nombre entier attendu." % label)
        if self.kind == "float":
            try:
                return float(str(value).strip().replace(",", "."))
            except ValueError:
                raise ValueError("« %s » : nombre attendu." % label)
        if self.kind in ("str", "combo"):
            return value.strip()
        return value

    def set_enabled(self, enabled):
        for widget in self.widgets:
            try:
                if isinstance(widget, tk.Text):
                    widget.configure(state="normal" if enabled else "disabled",
                                     foreground="black" if enabled else "#888888")
                else:
                    widget.state(["!disabled"] if enabled else ["disabled"])
            except tk.TclError:
                pass


class ConfigEditor(ModalDialog):
    def __init__(self, parent, cfg, first_run=False):
        ModalDialog.__init__(self, parent, "%s – configuration à appliquer" % APP_TITLE, resizable=True)
        self.original = copy.deepcopy(cfg)
        self.bindings = []
        self._by_path = {}
        self.minsize(640, 480)
        if first_run:
            ttk.Label(self.body, wraplength=700, justify="left",
                      text="Première utilisation : renseignez la configuration à appliquer à vos Shelly Plus 1PM. "
                           "Elle sera enregistrée sur ce poste (mots de passe chiffrés) et ne vous sera plus "
                           "redemandée : au prochain lancement, vous pourrez simplement la garder ou la modifier."
                      ).pack(anchor="w", pady=(0, 8))
        self.notebook = ttk.Notebook(self.body)
        self.notebook.pack(fill="both", expand=True)
        for title, fields in TABS:
            self._build_tab(title, fields)
        for binding in self.bindings:
            if binding.path is not None:
                binding.set(get_path(cfg, binding.path))
        for binding in self.bindings:
            if binding.var is not None:
                binding.var.trace_add("write", lambda *a: self._refresh_states())
        self._refresh_states()
        buttons = ttk.Frame(self.body)
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Enregistrer", command=self._save).pack(side="right")
        ttk.Button(buttons, text="Annuler", command=self._cancel).pack(side="right", padx=8)
        self.bind("<Escape>", lambda e: self._cancel())
        self.geometry("780x640")

    # -- construction -------------------------------------------------------------------------
    def _build_tab(self, title, fields):
        scroll = ScrollableFrame(self.notebook)
        self.notebook.add(scroll, text=title)
        frame = scroll.inner
        frame.columnconfigure(1, weight=1)
        row = 0
        current_apply = None
        for spec in fields:
            kind = spec["kind"]
            when = list(spec.get("when") or [])
            if spec.get("apply"):
                current_apply = spec["path"]
            elif current_apply is not None:
                when.insert(0, (current_apply, True))
            spec = dict(spec, when=when)
            if kind == "header":
                ttk.Label(frame, text=spec["label"], font=("TkDefaultFont", 10, "bold")).grid(
                    row=row, column=0, columnspan=3, sticky="w", pady=(12, 4))
                row += 1
                continue
            if kind == "note":
                ttk.Label(frame, text=spec["label"], wraplength=640, justify="left", foreground="#555555").grid(
                    row=row, column=0, columnspan=3, sticky="w", pady=(2, 6))
                row += 1
                continue
            row = self._build_field(frame, row, spec)

    def _build_field(self, frame, row, spec):
        kind = spec["kind"]
        label = spec["label"]
        if kind == "bool":
            var = tk.BooleanVar()
            binding = _Binding(spec, var=var)
            chk = ttk.Checkbutton(frame, text=label, variable=var)
            if spec.get("apply"):
                chk.grid(row=row, column=0, columnspan=3, sticky="w", pady=(0, 8))
            else:
                chk.grid(row=row, column=0, columnspan=3, sticky="w", pady=2)
                binding.widgets.append(chk)
            self._add_binding(binding)
            return row + 1
        if kind == "pem":
            binding = _Binding(spec)
            ttk.Label(frame, text=label).grid(row=row, column=0, columnspan=3, sticky="w", pady=(8, 2))
            text = tk.Text(frame, height=7, width=60, wrap="none", font=("Courier", 9))
            text.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
            binding.text = text
            side = ttk.Frame(frame)
            side.grid(row=row + 1, column=2, sticky="n", padx=(6, 0))
            load = ttk.Button(side, text="Charger un fichier…", command=lambda: self._load_pem(text))
            load.pack(fill="x")
            clear = ttk.Button(side, text="Effacer", command=lambda: text.delete("1.0", "end"))
            clear.pack(fill="x", pady=4)
            binding.widgets.extend([text, load, clear])
            self._add_binding(binding)
            return row + 2
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=3)
        var = tk.StringVar()
        binding = _Binding(spec, var=var)
        if kind == "choice":
            widget = ttk.Combobox(frame, textvariable=var, state="readonly",
                                  values=[lab for _v, lab in spec["choices"]], width=44)
        elif kind == "combo":
            widget = ttk.Combobox(frame, textvariable=var, values=spec.get("values", []), width=44)
        elif kind in ("secret", "confirm"):
            widget = ttk.Entry(frame, textvariable=var, show="*", width=46)
            show_var = tk.BooleanVar(value=False)
            toggle = ttk.Checkbutton(frame, text="Afficher", variable=show_var,
                                     command=lambda w=widget, v=show_var: w.configure(show="" if v.get() else "*"))
            toggle.grid(row=row, column=2, sticky="w", padx=(6, 0))
            binding.widgets.append(toggle)
        else:
            widget = ttk.Entry(frame, textvariable=var, width=46)
        widget.grid(row=row, column=1, sticky="ew", pady=3)
        binding.widgets.append(widget)
        self._add_binding(binding)
        row += 1
        if spec.get("help"):
            ttk.Label(frame, text=spec["help"], foreground="#666666", wraplength=460, justify="left",
                      font=("TkDefaultFont", 8)).grid(row=row, column=1, columnspan=2, sticky="w")
            row += 1
        return row

    def _add_binding(self, binding):
        self.bindings.append(binding)
        if binding.kind != "confirm":
            self._by_path[binding.path] = binding

    def _load_pem(self, text):
        path = filedialog.askopenfilename(parent=self, title="Fichier PEM",
                                          filetypes=[("Certificats / clés", "*.pem *.crt *.cer *.key"),
                                                     ("Tous les fichiers", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            messagebox.showerror(APP_TITLE, "Lecture impossible de %s :\n%s\n\n"
                                 "Le fichier doit être au format PEM (texte)." % (path, exc), parent=self)
            return
        text.delete("1.0", "end")
        text.insert("1.0", content)

    # -- états actif/inactif ------------------------------------------------------------------
    def _refresh_states(self):
        for binding in self.bindings:
            when = binding.spec.get("when") or []
            enabled = True
            for path, expected in when:
                other = self._by_path.get(path)
                if other is not None and other.raw() != expected:
                    enabled = False
                    break
            binding.set_enabled(enabled)

    # -- validation ---------------------------------------------------------------------------
    def collect(self):
        """Renvoie (config, erreurs de saisie)."""
        cfg = copy.deepcopy(self.original)
        errors = []
        confirms = []
        for binding in self.bindings:
            if binding.kind == "confirm":
                confirms.append(binding)
                continue
            try:
                set_path(cfg, binding.path, binding.get())
            except ValueError as exc:
                errors.append(str(exc))
        for binding in confirms:
            enabled = all(self._by_path[p].raw() == v for p, v in binding.spec["when"] if p in self._by_path)
            if enabled and binding.raw() != get_path(cfg, binding.path):
                errors.append("La confirmation du mot de passe administrateur ne correspond pas.")
        return cfg, errors

    def _save(self):
        cfg, errors = self.collect()
        if not errors:
            errors, warnings = validate_config(cfg)
        else:
            warnings = []
        if errors:
            messagebox.showerror(APP_TITLE, "Corrigez les points suivants :\n\n• " + "\n• ".join(errors), parent=self)
            return
        if warnings:
            if not messagebox.askyesno(APP_TITLE, "Attention :\n\n• " + "\n• ".join(warnings)
                                       + "\n\nEnregistrer quand même ?", parent=self):
                return
        self.done(cfg)

    def _cancel(self):
        cfg, _errors = self.collect()
        if cfg != self.original and not messagebox.askyesno(
                APP_TITLE, "Abandonner les modifications non enregistrées ?", parent=self):
            return
        self.done(None)

    def cancel(self, _event=None):
        self._cancel()
