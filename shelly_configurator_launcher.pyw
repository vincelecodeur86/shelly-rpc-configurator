# -*- coding: utf-8 -*-
"""Lanceur sans console (double-clic sous Windows, point d'entrée PyInstaller)."""
import sys

from shelly_configurator.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
