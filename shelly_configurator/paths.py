# -*- coding: utf-8 -*-
"""Emplacements des fichiers de l'utilitaire (configuration, journaux, sauvegardes).

Windows : %APPDATA%\\ShellyConfigurator\\
Linux   : ~/.config/shelly-configurator/ (config) et ~/.local/share/shelly-configurator/ (journaux, sauvegardes)

La variable d'environnement SHELLY_CONFIGURATOR_HOME remplace tous ces dossiers (utile pour les tests).
"""
import os
import sys

APP_DIR_WINDOWS = "ShellyConfigurator"
APP_DIR_LINUX = "shelly-configurator"
ENV_OVERRIDE = "SHELLY_CONFIGURATOR_HOME"


def is_windows():
    return sys.platform.startswith("win")


def _ensure(path):
    if not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def _override():
    return os.environ.get(ENV_OVERRIDE) or None


def config_dir():
    override = _override()
    if override:
        return _ensure(override)
    if is_windows():
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return _ensure(os.path.join(base, APP_DIR_WINDOWS))
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return _ensure(os.path.join(base, APP_DIR_LINUX))


def data_dir():
    override = _override()
    if override:
        return _ensure(override)
    if is_windows():
        return config_dir()
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return _ensure(os.path.join(base, APP_DIR_LINUX))


def log_dir():
    return _ensure(os.path.join(data_dir(), "logs"))


def backup_dir():
    return _ensure(os.path.join(data_dir(), "backups"))


def config_file():
    return os.path.join(config_dir(), "config.json")


def key_file():
    return os.path.join(config_dir(), ".secret.key")
