# -*- coding: utf-8 -*-
"""Configuration du journal (fichier tournant dans le dossier des journaux)."""
import logging
import logging.handlers
import os
import sys

from . import paths

LOGGER_NAME = "shelly_configurator"
LOG_FILENAME = "shelly_configurator.log"


def log_file_path():
    return os.path.join(paths.log_dir(), LOG_FILENAME)


def setup_logging(level=logging.DEBUG):
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_shelly_configured", False):
        return logger
    logger.setLevel(level)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s : %(message)s")
    try:
        fh = logging.handlers.RotatingFileHandler(
            log_file_path(), maxBytes=1000000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    except OSError as exc:  # dossier non accessible : on continue sans fichier
        if sys.stderr is not None:
            sys.stderr.write("Impossible d'ouvrir le journal : %s\n" % exc)
    # pythonw.exe (Windows, sans console) : sys.stderr vaut None
    if sys.stderr is not None:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(logging.INFO)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
    logger._shelly_configured = True
    return logger


def get_logger(suffix=None):
    return logging.getLogger(LOGGER_NAME + ("." + suffix if suffix else ""))
