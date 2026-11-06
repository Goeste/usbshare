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
from .updater import clean_environment
from .gui import Dialog
from .i18n import _

IS_WIN = sys.platform.startswith("win")
DETACHED = 0x00000008 | 0x00000200 if IS_WIN else 0      # DETACHED_PROCESS | NEW_PROCESS_GROUP


# -- installers ----------------------------------------------------------------------------
def install_windows_exe(rel: U.Release, kind: str, member: str,
                        progress: Callable[[int, int], None], restart_args=("--after-update",)) -> None:
    """Replace the running .exe (Windows allows renaming a running file) and start the new one."""
    exe = U.frozen_executable()
    if not exe:
        raise U.UpdateError(_("Nur die installierte .exe kann sich selbst aktualisieren."))
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
        raise U.UpdateError(_("Programmdatei konnte nicht ersetzt werden: {error}", error=e)) from e
    subprocess.Popen([exe, *restart_args], close_fds=True, creationflags=DETACHED,
                     env=clean_environment())


def install_linux_server(rel: U.Release, progress: Callable[[int, int], None]) -> None:
    """Run `usbshare update --yes` with root rights via pkexec (graphical password prompt)."""
    exe = U.frozen_executable()
    if not exe:
        raise U.UpdateError(_("Aus dem Quellcode gestartet – bitte mit git aktualisieren."))
    pk = shutil.which("pkexec")
    if not pk:
        raise U.UpdateError(_("pkexec fehlt. Bitte im Terminal ausführen:") + "\n  sudo usbshare update")
    progress(0, 0)
    r = subprocess.run([pk, exe, "update", "--yes"], capture_output=True, text=True)
    if r.returncode in (126, 127):
        raise U.UpdateError(_("Abgebrochen (keine Berechtigung erteilt)."))
    if r.returncode != 0:
        raise U.UpdateError((r.stderr or r.stdout).strip() or _("Fehlercode {code}", code=r.returncode))


def run_elevated(exe: str, params: str) -> int:
    """Windows: start a program with administrator rights (UAC prompt), wait for it and
    return its exit code. Raises UpdateError if the user declines."""
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong),
                    ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
                    ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
                    ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p),
                    ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE),
                    ("hProcess", wintypes.HANDLE)]
    info = SHELLEXECUTEINFOW(cbSize=ctypes.sizeof(SHELLEXECUTEINFOW), fMask=0x40,  # NOCLOSEPROCESS
                             lpVerb="runas", lpFile=exe, lpParameters=params,
                             lpDirectory=os.path.dirname(exe), nShow=0)
    shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(SHELLEXECUTEINFOW)]
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        if ctypes.GetLastError() == 1223:                                   # ERROR_CANCELLED
            raise U.UpdateError(_("Abgebrochen (keine Administratorrechte erteilt)."))
        raise U.UpdateError(_("Konnte {program} nicht starten (Fehler {code}).",
                              program=os.path.basename(exe), code=ctypes.GetLastError()))
    kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
    code = wintypes.DWORD()
    kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(info.hProcess)
    return code.value


def install_windows_server(rel: U.Release, progress: Callable[[int, int], None]) -> None:
    """Run `usbshare.exe update --yes` (next to this window's program) as administrator. It
    replaces usbshare.exe and usbshare-server-gui.exe and restarts the service."""
    exe = U.frozen_executable()
    if not exe:
        raise U.UpdateError(_("Aus dem Quellcode gestartet – bitte mit git aktualisieren."))
    server = os.path.join(os.path.dirname(exe), "usbshare.exe")
    if not os.path.exists(server):
        raise U.UpdateError(_("usbshare.exe fehlt in {folder}.", folder=os.path.dirname(exe)))
    progress(0, 0)
    import tempfile
    fd, report = tempfile.mkstemp(prefix="usbshare-update-", suffix=".txt")
    os.close(fd)
    try:
        rc = run_elevated(server, f'update --yes --report "{report}"')
        try:
            with open(report, encoding="utf-8", errors="replace") as f:
                text = f.read().strip()
        except OSError:
            text = ""
    finally:
        try:
            os.remove(report)
        except OSError:
            pass
    if rc != 0:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        raise U.UpdateError(lines[-1] if lines else _("Fehlercode {code}", code=rc))


# -- dialog ----------------------------------------------------------------------------------
class UpdateDialog(Dialog):
    """kind: 'client' or 'server'. on_installed(): called after a successful install."""

    def __init__(self, parent, kind: str, product: str, on_installed: Optional[Callable] = None):
        super().__init__(parent, _("Nach Updates suchen"))
        self.kind, self.product, self.on_installed = kind, product, on_installed
        self.rel: Optional[U.Release] = None
        b = self.body
        ttk.Label(b, text=_("{product} – Updates", product=product), style="Section.TLabel").pack(anchor="w")
        self.status = ttk.Label(b, text=_("Suche nach Updates …"), style="Muted.TLabel", wraplength=520,
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
        self.b_close = ttk.Button(btns, text=_("Schließen"), command=self.destroy)
        self.b_close.pack(side="right")
        self.b_install = ttk.Button(btns, text=_("Jetzt aktualisieren"), style="Accent.TButton",
                                    command=self._install)
        self.b_page = ttk.Button(btns, text=_("Release-Seite öffnen"),
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
            self.status.configure(text=_("{product} ist aktuell (Version {v}).", product=self.product, v=__version__))
            self._set_notes(U.short_notes(rel.notes))
            return
        can_install, hint = self._can_install()
        self.status.configure(text=_("Neue Version {new} verfügbar – installiert ist {v}.",
                                     new=rel.version, v=__version__) + (f"\n{hint}" if hint else ""))
        self._set_notes(_("Änderungen:") + "\n\n" + U.short_notes(rel.notes))
        if can_install:
            self.b_install.pack(side="right", padx=8)

    def _can_install(self):
        if not U.frozen_executable():
            return False, _("Aus dem Quellcode gestartet – bitte mit git aktualisieren.")
        if self.kind == "client":
            return (True, "") if IS_WIN else (False, "")
        if IS_WIN:
            return True, _("Für die Installation fragt Windows nach Administratorrechten.")
        return True, _("Für die Installation fragt Linux nach deinem Passwort.")

    def _show_error(self, msg: str):
        self.bar.pack_forget()
        self.status.configure(text=msg, style="Err.TLabel")

    def _install(self):
        self.b_install.state(["disabled"])
        self.b_close.state(["disabled"])
        self.bar.configure(mode="determinate", value=0)
        self.bar.pack(fill="x", pady=(10, 0), before=self.b_close.master)
        self.status.configure(text=_("Lade herunter und prüfe …"), style="Muted.TLabel")

        def progress(done, total):
            if total:
                self._ui(self.bar.configure, {"mode": "determinate", "value": done * 100 / total})
            else:
                self._ui(self._indeterminate)

        def work():
            try:
                if self.kind == "client":
                    install_windows_exe(self.rel, "client", "/usbshare-client.exe", progress)
                elif IS_WIN:
                    install_windows_server(self.rel, progress)
                else:
                    install_linux_server(self.rel, progress)
            except U.UpdateError as e:
                self._ui(self._failed, str(e))
                return
            except Exception as e:  # pragma: no cover
                self._ui(self._failed, _("Unerwarteter Fehler: {error}", error=e))
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
        self.status.configure(text=_("Update fehlgeschlagen: {error}", error=msg), style="Err.TLabel")
        self.b_close.state(["!disabled"])
        self.b_install.state(["!disabled"])

    def _done(self):
        self.bar.stop()
        self.status.configure(text=_("Version {v} installiert – starte neu …", v=self.rel.version),
                              style="Ok.TLabel")
        self.after(800, self._finish)

    def _finish(self):
        self.destroy()
        if self.on_installed:
            self.on_installed()


class DriverDialog(Dialog):
    """Install or update usbip-win2 (bundled installer, or the newest one from GitHub)."""

    def __init__(self, parent, on_installed: Optional[Callable] = None):
        from . import driver
        super().__init__(parent, "usbip-win2")
        self.driver, self.on_installed = driver, on_installed
        self.inst = None
        b = self.body
        ttk.Label(b, text=_("usbip-win2 – USB/IP-Treiber für Windows"), style="Section.TLabel").pack(anchor="w")
        self.status = ttk.Label(b, text=_("Prüfe usbip-win2 …"), style="Muted.TLabel", wraplength=520,
                                justify="left")
        self.status.pack(anchor="w", pady=(2, 10))
        self.bar = ttk.Progressbar(b, mode="indeterminate", maximum=100)
        self.bar.pack(fill="x", pady=(4, 0))
        self.bar.start(12)
        btns = ttk.Frame(b)
        btns.pack(fill="x", pady=(14, 0))
        self.btns = btns
        self.b_close = ttk.Button(btns, text=_("Schließen"), command=self.destroy)
        self.b_close.pack(side="right")
        self.b_install = ttk.Button(btns, text=_("Installieren"), style="Accent.TButton",
                                    command=self._install)
        self.b_reboot = ttk.Button(btns, text=_("Jetzt neu starten"), style="Accent.TButton",
                                   command=driver.reboot)
        ttk.Button(btns, text=_("Projektseite öffnen"),
                   command=lambda: webbrowser.open(driver.RELEASES_PAGE)).pack(side="left")
        threading.Thread(target=self._check, daemon=True).start()
        self.run()

    def _ui(self, fn, *args):
        try:
            self.after(0, fn, *args)
        except (tk.TclError, RuntimeError):
            pass

    def _check(self):
        d = self.driver
        installed, local = d.installed_version(), d.bundled()
        online = d.latest_online()
        self._ui(self._show, installed, d.choose(installed, local, online), online is not None)

    def _show(self, installed, inst, checked_online):
        self.inst = inst
        self.bar.stop()
        self.bar.pack_forget()
        have = (_("usbip-win2 ist nicht installiert.") if installed is None
                else _("usbip-win2 {v} ist installiert.", v=installed) if installed
                else _("usbip-win2 ist installiert."))
        if inst is None:
            if installed is None:
                text = have + "\n" + _("Kein Installationsprogramm gefunden und GitHub nicht "
                                       "erreichbar – bitte über die Projektseite installieren.")
            elif not checked_online:
                text = have + " " + _("(GitHub nicht erreichbar – keine neuere Version geprüft.)")
            else:
                text = have + " " + _("Das ist die aktuelle Version.")
            self.status.configure(text=text)
            return
        verb = _("Installieren") if installed is None else _("Aktualisieren")
        source = _("im Paket enthalten") if inst.source == "bundled" else _("von GitHub")
        self.status.configure(text=(
            have + " " + _("Verfügbar: Version {v} ({source}).", v=inst.version, source=source) + "\n"
            + _("Die Installation läuft ohne Rückfragen. Dabei werden alle USB-Geräte dieses "
                "Rechners kurz getrennt; danach muss Windows neu gestartet werden.")))
        self.b_install.configure(text=verb)
        self.b_install.pack(side="right", padx=8)

    def _install(self):
        self.b_install.state(["disabled"])
        self.b_close.state(["disabled"])
        self.bar.configure(mode="determinate", value=0)
        self.bar.pack(fill="x", pady=(4, 0), before=self.btns)
        self.status.configure(text=_("Lade herunter und prüfe …") if self.inst.source == "github"
                              else _("Prüfe Installationsprogramm …"), style="Muted.TLabel")

        def progress(done, total):
            if total:
                self._ui(self.bar.configure, {"mode": "determinate", "value": done * 100 / total})

        def work():
            try:
                path = self.driver.fetch(self.inst, progress)
                self._ui(self._installing)
                self.driver.run_installer(path)
            except U.UpdateError as e:
                self._ui(self._failed, str(e))
                return
            except Exception as e:  # pragma: no cover
                self._ui(self._failed, _("Unerwarteter Fehler: {error}", error=e))
                return
            self._ui(self._done)
        threading.Thread(target=work, daemon=True).start()

    def _installing(self):
        self.status.configure(text=_("Installiere usbip-win2 {v} … (USB-Geräte werden kurz getrennt)",
                                     v=self.inst.version))
        self.bar.configure(mode="indeterminate")
        self.bar.start(12)

    def _failed(self, msg: str):
        self.bar.stop()
        self.bar.pack_forget()
        self.status.configure(text=_("Installation fehlgeschlagen: {error}", error=msg), style="Err.TLabel")
        self.b_close.state(["!disabled"])
        self.b_install.state(["!disabled"])

    def _done(self):
        self.bar.stop()
        self.bar.pack_forget()
        self.b_install.pack_forget()
        self.status.configure(text=_("usbip-win2 {v} ist installiert. Windows muss neu gestartet "
                                     "werden, damit der Treiber geladen wird.", v=self.inst.version),
                              style="Ok.TLabel")
        self.b_close.configure(text=_("Später"))
        self.b_close.state(["!disabled"])
        self.b_reboot.pack(side="right", padx=8)
        if self.on_installed:
            self.on_installed()
