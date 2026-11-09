# -*- coding: utf-8 -*-
"""Démarrage : première saisie de la configuration, ou question « garder / modifier »."""
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from .. import APP_TITLE, config_store
from ..log_setup import get_logger
from . import dialogs
from .common import show_error
from .editor import ConfigEditor
from .main_window import MainWindow

log = get_logger("gui.app")


def _install_error_handlers(root):
    def tk_error(exc_type, exc_value, exc_tb):
        log.error("Erreur dans l'interface", exc_info=(exc_type, exc_value, exc_tb))
        try:
            show_error(root, "Erreur inattendue : %s" % exc_value)
        except Exception:
            pass

    root.report_callback_exception = tk_error

    if hasattr(threading, "excepthook"):  # Python 3.8+
        def thread_error(args):
            log.error("Erreur dans le fil %s", args.thread.name if args.thread else "?",
                      exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
        threading.excepthook = thread_error


def _choose_theme(root):
    style = ttk.Style(root)
    names = style.theme_names()
    for theme in ("vista", "winnative", "clam"):  # vista : Windows 7+ ; clam : Linux
        if theme in names:
            style.theme_use(theme)
            break
    # Tk 8.6.9 (fourni avec Python 3.7/3.8 sous Windows) ignore les couleurs des lignes de Treeview :
    # contournement classique en retirant les entrées de style par défaut.
    def fixed_map(option):
        return [elm for elm in style.map("Treeview", query_opt=option) if elm[:2] != ("!disabled", "!selected")]
    try:
        style.map("Treeview", foreground=fixed_map("foreground"), background=fixed_map("background"))
    except tk.TclError:
        pass


def _initial_config(root):
    """Renvoie la configuration à utiliser, ou None si l'utilisateur quitte."""
    cfg = None
    if config_store.config_exists():
        try:
            cfg = config_store.load_config()
        except config_store.ConfigError as exc:
            show_error(root, "%s\n\nVous allez pouvoir ressaisir la configuration." % exc,
                       "Configuration illisible")
    if cfg is None:
        new_cfg = ConfigEditor(root, config_store.default_config(), first_run=True).show()
        if new_cfg is None:
            return None
        config_store.save_config(new_cfg)
        return new_cfg

    choice = dialogs.ask_keep_or_change(root, config_store.summary_lines(cfg))
    if choice is None:
        return None
    if choice == "change":
        edited = ConfigEditor(root, cfg).show()
        if edited is not None:
            config_store.save_config(edited)
            return edited
        messagebox.showinfo(APP_TITLE, "Modifications annulées : la configuration enregistrée est conservée.",
                            parent=root)
    return cfg


def _enable_dpi_awareness():
    """Texte net sur les écrans à haute résolution (Windows Vista/7 et suivants)."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Windows 8.1+
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()  # Windows 7
    except Exception as exc:
        log.debug("Réglage DPI impossible : %s", exc)


def run():
    _enable_dpi_awareness()
    root = tk.Tk()
    root.withdraw()
    root.title(APP_TITLE)
    _install_error_handlers(root)
    _choose_theme(root)
    try:
        cfg = _initial_config(root)
    except Exception as exc:
        log.exception("Démarrage")
        show_error(root, "Erreur au démarrage : %s" % exc)
        root.destroy()
        return 1
    if cfg is None:
        root.destroy()
        return 0
    MainWindow(root, cfg)
    root.deiconify()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(run())
