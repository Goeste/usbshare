# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""'Nach Updates suchen' dialog shared by the client and the server window."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from tkinter import ttk
from typing import Callable, Optional

from . import __version__
from . import updater as U
from .gui import Dialog

IS_WIN = sys.platform.startswith("win")
DETACHED = 0x00000008 | 0x00000200 if IS_WIN else 0      # DETACHED_PROCESS | NEW_PROCESS_GROUP


# -- installers ----------------------------------------------------------------------------
def install_windows_exe(rel: U.Release, kind: str, member: str,
                        progress: Callable[[int, int], None], restart_args=("--after-update",)) -> None:
    """Replace the running .exe (Windows allows renaming a running file) and start the new one."""
    exe = U.frozen_executable()
    if not exe:
        raise U.UpdateError("Nur die installierte .exe kann sich selbst aktualisieren.")
    data = U.extract_member(U.download_package(rel, kind, progress), member)
    new, old = exe + ".new", exe + ".old"
    with open(new, "wb") as f:
        f.write(data)
    try:
        if os.path.exists(old):
            os.remove(old)
    except OSError:
        pass
    try:
        os.replace(exe, old)
        os.replace(new, exe)
    except OSError as e:
        raise U.UpdateError(f"Programmdatei konnte nicht ersetzt werden: {e}") from e
    subprocess.Popen([exe, *restart_args], close_fds=True, creationflags=DETACHED,
                     env=clean_environment())


def clean_environment() -> dict:
    """Environment for starting the *new* program version from the running one.

    A PyInstaller one-file program passes variables to its children so that they reuse its
    unpacked files (_MEI… temp folder). The new version must not do that: the old one deletes
    that folder when it exits ("Can't find a usable tk.tcl")."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("_PYI", "_MEI")) and k not in ("TCL_LIBRARY", "TK_LIBRARY")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def cleanup_old_exe() -> None:
    exe = U.frozen_executable()
    if exe and os.path.exists(exe + ".old"):
        try:
            os.remove(exe + ".old")
        except OSError:
            pass


def install_linux_server(rel: U.Release, progress: Callable[[int, int], None]) -> None:
    """Run `usbshare update --yes` with root rights via pkexec (graphical password prompt)."""
    exe = U.frozen_executable()
    if not exe:
        raise U.UpdateError("Aus dem Quellcode gestartet – bitte mit git aktualisieren.")
    pk = shutil.which("pkexec")
    if not pk:
        raise U.UpdateError("pkexec fehlt. Bitte im Terminal ausführen:\n  sudo usbshare update")
    progress(0, 0)
    r = subprocess.run([pk, exe, "update", "--yes"], capture_output=True, text=True)
    if r.returncode in (126, 127):
        raise U.UpdateError("Abgebrochen (keine Berechtigung erteilt).")
    if r.returncode != 0:
        raise U.UpdateError((r.stderr or r.stdout).strip() or f"Fehlercode {r.returncode}")


# -- dialog ----------------------------------------------------------------------------------
class UpdateDialog(Dialog):
    """kind: 'client' or 'server'. on_installed(): called after a successful install."""

    def __init__(self, parent, kind: str, product: str, on_installed: Optional[Callable] = None):
        super().__init__(parent, "Nach Updates suchen")
        self.kind, self.product, self.on_installed = kind, product, on_installed
        self.rel: Optional[U.Release] = None
        b = self.body
        ttk.Label(b, text=f"{product} – Updates", style="Section.TLabel").pack(anchor="w")
        self.status = ttk.Label(b, text="Suche nach Updates …", style="Muted.TLabel", wraplength=520,
                                justify="left")
        self.status.pack(anchor="w", pady=(2, 10))
        holder = ttk.Frame(b)
        holder.pack(fill="both", expand=True)
        self.notes = tk.Text(holder, width=72, height=12, wrap="word", relief="flat", padx=10, pady=8,
                             font="TkDefaultFont")
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.notes.yview)
        self.notes.configure(yscrollcommand=sb.set, state="disabled")
        self.notes.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.bar = ttk.Progressbar(b, mode="determinate", maximum=100)
        self.bar.pack(fill="x", pady=(10, 0))
        btns = ttk.Frame(b)
        btns.pack(fill="x", pady=(14, 0))
        self.b_close = ttk.Button(btns, text="Schließen", command=self.destroy)
        self.b_close.pack(side="right")
        self.b_install = ttk.Button(btns, text="Jetzt aktualisieren", style="Accent.TButton",
                                    command=self._install)
        self.b_page = ttk.Button(btns, text="Release-Seite öffnen",
                                 command=lambda: webbrowser.open(self.rel.page if self.rel else U.RELEASES_PAGE))
        self.b_page.pack(side="left")
        threading.Thread(target=self._check, daemon=True).start()
        self.run()

    def _ui(self, fn, *args):
        try:
            self.after(0, fn, *args)
        except (tk.TclError, RuntimeError):
            pass

    def _set_notes(self, text: str):
        self.notes.configure(state="normal")
        self.notes.delete("1.0", "end")
        self.notes.insert("end", text)
        self.notes.configure(state="disabled")

    def _check(self):
        try:
            rel = U.latest_release()
        except U.UpdateError as e:
            self._ui(self._show_error, str(e))
            return
        self._ui(self._show, rel)

    def _show(self, rel: U.Release):
        self.rel = rel
        self.bar.pack_forget()
        if not U.is_newer(rel.version):
            self.status.configure(text=f"{self.product} ist aktuell (Version {__version__}).")
            self._set_notes(U.short_notes(rel.notes))
            return
        can_install, hint = self._can_install()
        self.status.configure(text=f"Neue Version {rel.version} verfügbar – installiert ist {__version__}."
                              + (f"\n{hint}" if hint else ""))
        self._set_notes("Änderungen:\n\n" + U.short_notes(rel.notes))
        if can_install:
            self.b_install.pack(side="right", padx=8)

    def _can_install(self):
        if not U.frozen_executable():
            return False, "Aus dem Quellcode gestartet – bitte mit git aktualisieren."
        if self.kind == "client":
            return (True, "") if IS_WIN else (False, "")
        if IS_WIN:
            return False, ("Den Windows-Server bitte über die Release-Seite aktualisieren "
                           "(Paket herunterladen, install-service.ps1 erneut ausführen).")
        return True, "Für die Installation fragt Linux nach deinem Passwort."

    def _show_error(self, msg: str):
        self.bar.pack_forget()
        self.status.configure(text=msg, style="Err.TLabel")

    def _install(self):
        self.b_install.state(["disabled"])
        self.b_close.state(["disabled"])
        self.bar.configure(mode="determinate", value=0)
        self.bar.pack(fill="x", pady=(10, 0), before=self.b_close.master)
        self.status.configure(text="Lade herunter und prüfe …", style="Muted.TLabel")

        def progress(done, total):
            if total:
                self._ui(self.bar.configure, {"mode": "determinate", "value": done * 100 / total})
            else:
                self._ui(self._indeterminate)

        def work():
            try:
                if self.kind == "client":
                    install_windows_exe(self.rel, "client", "/usbshare-client.exe", progress)
                else:
                    install_linux_server(self.rel, progress)
            except U.UpdateError as e:
                self._ui(self._failed, str(e))
                return
            except Exception as e:  # pragma: no cover
                self._ui(self._failed, f"Unerwarteter Fehler: {e}")
                return
            self._ui(self._done)
        threading.Thread(target=work, daemon=True).start()

    def _indeterminate(self):
        if str(self.bar.cget("mode")) != "indeterminate":
            self.bar.configure(mode="indeterminate")
            self.bar.start(12)

    def _failed(self, msg: str):
        self.bar.stop()
        self.bar.pack_forget()
        self.status.configure(text=f"Update fehlgeschlagen: {msg}", style="Err.TLabel")
        self.b_close.state(["!disabled"])
        self.b_install.state(["!disabled"])

    def _done(self):
        self.bar.stop()
        self.status.configure(text=f"Version {self.rel.version} installiert – starte neu …",
                              style="Ok.TLabel")
        self.after(800, self._finish)

    def _finish(self):
        self.destroy()
        if self.on_installed:
            self.on_installed()
