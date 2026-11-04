# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Shared look & feel for the client and server windows: Windows 11 style theme,
light/dark mode, app icons, tray icon and single-instance handling."""
from __future__ import annotations

import logging
import queue
import socket
import sys
import threading
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk
from typing import Callable, Dict, List, Optional, Tuple

log = logging.getLogger("usbshare.ui")
IS_WIN = sys.platform.startswith("win")


# -- theme ------------------------------------------------------------------------------
def windows_dark_mode() -> bool:
    import os
    forced = os.environ.get("USBSHARE_THEME", "").lower()
    if forced in ("dark", "light"):
        return forced == "dark"
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return winreg.QueryValueEx(k, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


class Palette:
    def __init__(self, dark: bool):
        self.dark = dark
        self.ok = "#4ade80" if dark else "#15803d"
        self.warn = "#fbbf24" if dark else "#b45309"
        self.err = "#f87171" if dark else "#b91c1c"
        self.muted = "#9ca3af" if dark else "#6b7280"
        self.accent = "#60a5fa" if dark else "#2563eb"
        self.banner_bg = "#422006" if dark else "#fef3c7"
        self.banner_fg = "#fde68a" if dark else "#92400e"


def apply_theme(root: tk.Tk, kind: str) -> Palette:
    dark = windows_dark_mode()
    try:
        import sv_ttk
        sv_ttk.set_theme("dark" if dark else "light")
    except Exception as e:  # pragma: no cover - theme missing: plain ttk still works
        log.warning("theme not available: %s", e)
    family = "Segoe UI Variable Text" if IS_WIN and "Segoe UI Variable Text" in tkfont.families() \
        else ("Segoe UI" if IS_WIN else None)
    if family:
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            try:
                tkfont.nametofont(name).configure(family=family, size=10)
            except tk.TclError:
                pass
    base = tkfont.nametofont("TkDefaultFont").actual()
    fam = base["family"]
    st = ttk.Style()
    st.configure("Treeview", rowheight=32)
    st.configure("Treeview.Heading", font=(fam, 9, "bold"))
    st.configure("Title.TLabel", font=(fam, 17, "bold"))
    st.configure("Section.TLabel", font=(fam, 11, "bold"))
    pal = Palette(dark)
    st.configure("Muted.TLabel", foreground=pal.muted)
    st.configure("Ok.TLabel", foreground=pal.ok)
    st.configure("Err.TLabel", foreground=pal.err)
    st.configure("Banner.TFrame", background=pal.banner_bg)
    st.configure("Banner.TLabel", background=pal.banner_bg, foreground=pal.banner_fg)
    _patch_label_colors()
    set_window_icon(root, kind)
    root.after(50, lambda: dark_title_bar(root, dark))
    return pal


_PATCHED = False


def _patch_label_colors() -> None:
    """sv_ttk calls tk_setPalette, which puts "*foreground"/"*background" into the option
    database. ttk.Label takes those as widget options, which override the colors of its
    style (Muted.TLabel etc.). So copy the style's colors onto the widget instead."""
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True
    orig_init, orig_conf = ttk.Label.__init__, ttk.Label.configure

    def sync(w):
        style = str(orig_conf(w, "style")[-1] or "")
        if not style:
            return
        st = ttk.Style(w)
        fg, bg = st.lookup(style, "foreground"), st.lookup(style, "background")
        if fg and "foreground" not in getattr(w, "_own", ()):
            orig_conf(w, foreground=fg)
        if bg and style.startswith("Banner"):
            orig_conf(w, background=bg)

    def init(self, master=None, **kw):
        orig_init(self, master, **kw)
        self._own = {k for k in ("foreground", "background") if k in kw}
        sync(self)

    def conf(self, cnf=None, **kw):
        res = orig_conf(self, cnf, **kw)
        if "style" in kw or (isinstance(cnf, dict) and "style" in cnf):
            sync(self)
        return res

    ttk.Label.__init__ = init
    ttk.Label.configure = ttk.Label.config = conf


def dark_title_bar(win: tk.Misc, dark: bool) -> None:
    if not IS_WIN or not dark:
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):   # DWMWA_USE_IMMERSIVE_DARK_MODE (new / old builds)
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val),
                                                          ctypes.sizeof(val)) == 0:
                break
    except Exception:
        pass


_ICON_REFS: List = []


def set_window_icon(win: tk.Misc, kind: str) -> None:
    try:
        from PIL import ImageTk
        from .icons import make_icon
        imgs = [ImageTk.PhotoImage(make_icon(kind, s)) for s in (16, 32, 48, 256)]
        _ICON_REFS.extend(imgs)
        win.iconphoto(True, *imgs)
    except Exception as e:  # pragma: no cover
        log.debug("no window icon: %s", e)


def header(parent, kind: str, title: str) -> Tuple[ttk.Frame, ttk.Label, ttk.Frame]:
    """Title bar inside the window: icon, title, subtitle; returns (frame, subtitle, right side)."""
    frm = ttk.Frame(parent, padding=(18, 14, 18, 6))
    frm.pack(fill="x")
    try:
        from PIL import ImageTk
        from .icons import make_icon
        img = ImageTk.PhotoImage(make_icon(kind, 40))
        _ICON_REFS.append(img)
        ttk.Label(frm, image=img).pack(side="left", padx=(0, 12))
    except Exception:
        pass
    txt = ttk.Frame(frm)
    txt.pack(side="left")
    ttk.Label(txt, text=title, style="Title.TLabel").pack(anchor="w")
    sub = ttk.Label(txt, style="Muted.TLabel")
    sub.pack(anchor="w")
    right = ttk.Frame(frm)
    right.pack(side="right")
    return frm, sub, right


# -- thread -> Tk calls -------------------------------------------------------------
class UiQueue:
    """Run callables on the Tk thread (pystray and sockets call from other threads)."""

    def __init__(self, root: tk.Tk):
        self.root, self.q = root, queue.Queue()
        self._pump()

    def call(self, fn: Callable, *args) -> None:
        self.q.put((fn, args))

    def _pump(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                try:
                    fn(*args)
                except Exception:
                    log.exception("ui callback failed")
        except queue.Empty:
            pass
        self.root.after(120, self._pump)


# -- single instance ---------------------------------------------------------------------
class SingleInstance:
    """First instance listens on a local UDP port; later ones ask it to show its window."""

    def __init__(self, port: int):
        self.port = port
        self.sock: Optional[socket.socket] = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if IS_WIN:
                s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
            s.bind(("127.0.0.1", port))
            self.sock = s
        except OSError:
            self.sock = None

    @property
    def first(self) -> bool:
        return self.sock is not None

    def notify_running(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(b"show", ("127.0.0.1", self.port))
        except OSError:
            pass

    def listen(self, on_show: Callable[[], None]) -> None:
        def run():
            while True:
                try:
                    data, _ = self.sock.recvfrom(64)
                except OSError:
                    return
                if data == b"show":
                    on_show()
        threading.Thread(target=run, daemon=True).start()


# -- tray icon --------------------------------------------------------------------------
class Tray:
    """Icon in the Windows notification area (next to the clock)."""

    def __init__(self, kind: str, title: str, ui: UiQueue,
                 items: List[Tuple[str, Optional[Callable[[], None]], bool]]):
        """items: (label, callback or None for separator, is_default)."""
        self.kind, self.ui = kind, ui
        self.icon = None
        self._badge = None
        if not IS_WIN:
            return
        try:
            import pystray
            from .icons import make_icon
            menu = []
            for label, cb, default in items:
                if cb is None:
                    menu.append(pystray.Menu.SEPARATOR)
                else:
                    menu.append(pystray.MenuItem(label, (lambda c: lambda *_: ui.call(c))(cb),
                                                 default=default))
            self._images: Dict[str, object] = {}
            self.icon = pystray.Icon(f"usbshare-{kind}", make_icon(kind, 64), title,
                                     menu=pystray.Menu(*menu))
            self.icon.run_detached()
        except Exception as e:
            log.warning("tray icon not available: %s", e)
            self.icon = None

    @property
    def available(self) -> bool:
        return self.icon is not None

    def update(self, badge: str, tooltip: str) -> None:
        if not self.icon:
            return
        try:
            if badge != self._badge:
                from .icons import make_icon
                self.icon.icon = make_icon(self.kind, 64, badge)
                self._badge = badge
            tooltip = tooltip[:120]
            if self.icon.title != tooltip:
                self.icon.title = tooltip
        except Exception:
            pass

    def notify(self, title: str, text: str) -> None:
        if not self.icon:
            return
        try:
            self.icon.notify(text, title)
        except Exception:
            pass

    def stop(self) -> None:
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass


def show_window(root: tk.Tk) -> None:
    root.deiconify()
    root.state("normal")
    root.lift()
    root.attributes("-topmost", True)
    root.after(200, lambda: root.attributes("-topmost", False))
    root.focus_force()


# -- autostart (current user, no admin rights) ------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def run_key_enabled(name: str) -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, name)
            return True
    except OSError:
        return False


def set_run_key(name: str, command: Optional[str]) -> bool:
    if not IS_WIN:
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if command:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
