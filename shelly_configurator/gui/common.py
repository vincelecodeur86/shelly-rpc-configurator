# -*- coding: utf-8 -*-
"""Éléments d'interface partagés : fenêtre modale de base, zone défilante, ouverture de dossier."""
import gc
import os
import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from .. import APP_TITLE
from ..log_setup import get_logger, log_file_path

log = get_logger("gui")


def center(window, parent=None):
    window.update_idletasks()
    w, h = window.winfo_reqwidth(), window.winfo_reqheight()
    if parent is not None and parent.winfo_viewable():
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 3
    else:
        x = (window.winfo_screenwidth() - w) // 2
        y = (window.winfo_screenheight() - h) // 3
    window.geometry("+%d+%d" % (max(x, 0), max(y, 0)))


class ModalDialog(tk.Toplevel):
    """Fenêtre modale : show() bloque et renvoie self.result."""

    def __init__(self, parent, title, resizable=False):
        tk.Toplevel.__init__(self, parent)
        self.withdraw()
        self.parent = parent
        self.title(title)
        self.resizable(resizable, resizable)
        # une fenêtre « transient » d'une fenêtre masquée resterait invisible
        if parent is not None and parent.winfo_viewable():
            self.transient(parent)
        self.result = None
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.body = ttk.Frame(self, padding=14)
        self.body.pack(fill="both", expand=True)

    def show(self):
        center(self, self.parent)
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window(self)
        # Libère tout de suite, dans le fil Tkinter, les variables Tk de la fenêtre fermée : sous Python 3.8,
        # leur destruction par le ramasse-miettes depuis un autre fil peut faire planter Tcl.
        gc.collect()
        return self.result

    def done(self, result):
        self.result = result
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def cancel(self, _event=None):
        self.done(None)


class ScrollableFrame(ttk.Frame):
    """Cadre avec barre de défilement verticale ; placer les widgets dans self.inner."""

    def __init__(self, master, padding=10):
        ttk.Frame.__init__(self, master)
        bg = ttk.Style().lookup("TFrame", "background") or None
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        if bg:
            self.canvas.configure(background=bg)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self.canvas, padding=padding)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.bind("<Enter>", self._bind_wheel)
        self.bind("<Leave>", self._unbind_wheel)

    def _bind_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        if getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = -1 if event.delta > 0 else 1
        first, last = self.canvas.yview()
        if first <= 0 and last >= 1:
            return
        self.canvas.yview_scroll(step * 2, "units")


def open_folder(path, parent=None):
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # noqa: S606 - ouverture d'un dossier local
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        log.warning("Ouverture de %s impossible : %s", path, exc)
        messagebox.showinfo(APP_TITLE, "Dossier :\n%s" % path, parent=parent)


def show_error(parent, message, title="Erreur"):
    """Fenêtre d'erreur standard ; l'erreur est aussi écrite dans le journal."""
    log.error(message)
    messagebox.showerror("%s – %s" % (APP_TITLE, title),
                         "%s\n\nDétails dans le journal :\n%s" % (message, log_file_path()),
                         parent=parent)
