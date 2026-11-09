# -*- coding: utf-8 -*-
"""Point d'entrée : python -m shelly_configurator"""
import argparse
import sys

from . import APP_TITLE, __version__
from .log_setup import log_file_path, setup_logging


def main(argv=None):
    parser = argparse.ArgumentParser(prog="shelly-configurator", description=APP_TITLE)
    parser.add_argument("--version", action="version", version="%s %s" % (APP_TITLE, __version__))
    parser.parse_args(argv)
    logger = setup_logging()
    logger.info("Démarrage de %s %s (Python %s, %s)", APP_TITLE, __version__,
                sys.version.split()[0], sys.platform)
    try:
        from .gui.app import run
    except ImportError as exc:  # Linux sans python3-tk
        logger.error("Tkinter indisponible : %s", exc)
        msg = ("Tkinter est introuvable (%s).\nSous Linux, installez le paquet python3-tk "
               "(Debian/Ubuntu) ou python3-tkinter (Fedora/RHEL).\n" % exc)
        if sys.stderr is not None:
            sys.stderr.write(msg)
        return 2
    try:
        return run()
    except Exception as exc:
        logger.exception("Erreur fatale")
        try:
            from tkinter import messagebox
            messagebox.showerror(APP_TITLE, "Erreur fatale : %s\n\nJournal : %s" % (exc, log_file_path()))
        except Exception:
            pass
        return 1
    finally:
        logger.info("Arrêt de %s", APP_TITLE)


if __name__ == "__main__":
    sys.exit(main())
