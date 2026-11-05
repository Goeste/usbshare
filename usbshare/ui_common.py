# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Shared look & feel for the client and server windows: Windows 11 style theme,
light/dark mode, app icons, tray icon and single-instance handling."""
from __future__ import annotations

import logging
import os
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


ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def asset(name: str) -> str:
    return os.path.join(ASSETS, name)


def _photo(win: tk.Misc, kind: str, size: int) -> Optional[tk.PhotoImage]:
    path = asset(f"{kind}-{size}.png")
    if not os.path.exists(path):
        return None
    img = tk.PhotoImage(master=win, file=path)        # Tk 8.6 reads PNG itself
    _ICON_REFS.append(img)
    return img


def set_window_icon(win: tk.Misc, kind: str) -> None:
    try:
        imgs = [i for i in (_photo(win, kind, s) for s in (16, 32, 48, 256)) if i]
        if imgs:
            win.iconphoto(True, *imgs)
    except tk.TclError as e:  # pragma: no cover
        log.debug("no window icon: %s", e)


def header(parent, kind: str, title: str) -> Tuple[ttk.Frame, ttk.Label, ttk.Frame]:
    """Title bar inside the window: icon, title, subtitle; returns (frame, subtitle, right side)."""
    frm = ttk.Frame(parent, padding=(18, 14, 18, 6))
    frm.pack(fill="x")
    img = _photo(frm, kind, 40)
    if img:
        ttk.Label(frm, image=img).pack(side="left", padx=(0, 12))
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
    """Run callables on the Tk thread (tray and sockets call from other threads)."""

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

    def __init__(self, port: int, wait: float = 0.0):
        """wait: keep trying for this many seconds (after an update the old instance is
        still shutting down)."""
        import time
        self.port = port
        self.sock: Optional[socket.socket] = None
        deadline = time.monotonic() + wait
        while True:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                if IS_WIN:
                    s.setsockopt(socket.SOL_SOCKET, getattr(socket, "SO_EXCLUSIVEADDRUSE", -5), 1)
                s.bind(("127.0.0.1", port))
                self.sock = s
                return
            except OSError:
                s.close()
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.3)

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

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
    """Icon in the Windows notification area (next to the clock).

    Talks to the Windows shell directly (Shell_NotifyIconW) from its own thread with a hidden
    message window – no extra libraries. On other systems it is simply not available."""

    WM_TRAY = 0x8000 + 1          # WM_APP + 1
    NIM_ADD, NIM_MODIFY, NIM_DELETE, NIM_SETVERSION = 0, 1, 2, 4
    NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10

    def __init__(self, kind: str, title: str, ui: UiQueue,
                 items: List[Tuple[str, Optional[Callable[[], None]], bool]]):
        """items: (label, callback or None for separator, is_default)."""
        self.kind, self.ui, self.items = kind, ui, items
        self.title = title
        self._badge = ""
        self._hwnd = None
        self._icons: Dict[str, int] = {}
        self._ready = threading.Event()
        self._ok = False
        if not IS_WIN:
            return
        threading.Thread(target=self._run, name="tray", daemon=True).start()
        self._ready.wait(5)

    @property
    def available(self) -> bool:
        return self._ok

    # -- Win32 plumbing -------------------------------------------------------------------
    def _api(self):
        import ctypes
        from ctypes import wintypes as W
        u, sh, k = ctypes.windll.user32, ctypes.windll.shell32, ctypes.windll.kernel32
        LRESULT = ctypes.c_ssize_t
        self.WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
        u.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.DefWindowProcW.restype = LRESULT
        u.CreateWindowExW.restype = W.HWND
        u.CreateWindowExW.argtypes = [W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HWND, W.HMENU,
                                      W.HINSTANCE, W.LPVOID]
        u.LoadImageW.restype = W.HANDLE
        u.LoadImageW.argtypes = [W.HINSTANCE, W.LPCWSTR, W.UINT, ctypes.c_int, ctypes.c_int,
                                 W.UINT]
        u.CreatePopupMenu.restype = W.HMENU
        u.AppendMenuW.argtypes = [W.HMENU, W.UINT, ctypes.c_size_t, W.LPCWSTR]
        u.TrackPopupMenu.argtypes = [W.HMENU, W.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                     W.HWND, W.LPVOID]
        u.SetMenuDefaultItem.argtypes = [W.HMENU, W.UINT, W.UINT]
        u.PostMessageW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
        u.SetForegroundWindow.argtypes = [W.HWND]
        u.DestroyMenu.argtypes = [W.HMENU]
        u.RegisterWindowMessageW.argtypes = [W.LPCWSTR]
        k.GetModuleHandleW.restype = W.HMODULE

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", W.UINT), ("lpfnWndProc", self.WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", W.HINSTANCE), ("hIcon", W.HICON),
                        ("hCursor", W.HANDLE), ("hbrBackground", W.HBRUSH),
                        ("lpszMenuName", W.LPCWSTR), ("lpszClassName", W.LPCWSTR)]

        class GUID(ctypes.Structure):
            _fields_ = [("a", W.DWORD), ("b", W.WORD), ("c", W.WORD), ("d", ctypes.c_ubyte * 8)]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [("cbSize", W.DWORD), ("hWnd", W.HWND), ("uID", W.UINT), ("uFlags", W.UINT),
                        ("uCallbackMessage", W.UINT), ("hIcon", W.HICON), ("szTip", W.WCHAR * 128),
                        ("dwState", W.DWORD), ("dwStateMask", W.DWORD), ("szInfo", W.WCHAR * 256),
                        ("uVersion", W.UINT), ("szInfoTitle", W.WCHAR * 64),
                        ("dwInfoFlags", W.DWORD), ("guidItem", GUID), ("hBalloonIcon", W.HICON)]

        class MSG(ctypes.Structure):
            _fields_ = [("hwnd", W.HWND), ("message", W.UINT), ("wParam", W.WPARAM),
                        ("lParam", W.LPARAM), ("time", W.DWORD), ("pt", W.POINT)]
        return ctypes, W, u, sh, k, WNDCLASSW, NOTIFYICONDATAW, MSG

    def _icon(self, badge: str) -> int:
        if badge not in self._icons:
            name = f"{self.kind}{'-' + badge if badge else ''}.ico"
            # IMAGE_ICON = 1, LR_LOADFROMFILE = 0x10, 0/0 = system small icon size
            self._icons[badge] = self._u.LoadImageW(None, asset(name), 1, 0, 0, 0x10 | 0x40) or 0
        return self._icons[badge]

    def _nid(self, flags: int, tip: str = "", info: str = "", info_title: str = ""):
        nid = self._NID()
        nid.cbSize = self._ctypes.sizeof(self._NID)
        nid.hWnd, nid.uID, nid.uFlags = self._hwnd, 1, flags
        nid.uCallbackMessage = self.WM_TRAY
        nid.hIcon = self._icon(self._badge)
        nid.szTip = (tip or self.title)[:127]
        if flags & self.NIF_INFO:
            nid.szInfo, nid.szInfoTitle, nid.dwInfoFlags = info[:255], info_title[:63], 0x1
        return nid

    def _shell(self, msg: int, nid) -> bool:
        return bool(self._sh.Shell_NotifyIconW(msg, self._ctypes.byref(nid)))

    def _run(self) -> None:
        try:
            (ctypes, W, u, sh, k, WNDCLASSW, NID, MSG) = self._api()
            self._ctypes, self._u, self._sh, self._NID = ctypes, u, sh, NID
            taskbar_created = u.RegisterWindowMessageW("TaskbarCreated")

            def wndproc(hwnd, msg, wp, lp):
                if msg == self.WM_TRAY:
                    event = lp & 0xFFFF
                    if event == 0x0203:                       # WM_LBUTTONDBLCLK
                        self._activate_default()
                    elif event in (0x0205, 0x007B):           # WM_RBUTTONUP, WM_CONTEXTMENU
                        self._menu(hwnd)
                    return 0
                if msg == taskbar_created:                    # Explorer restarted
                    self._shell(self.NIM_ADD, self._nid(self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP,
                                                        self._tip))
                    return 0
                if msg == 0x0010:                             # WM_CLOSE
                    u.DestroyWindow(hwnd)
                    return 0
                if msg == 0x0002:                             # WM_DESTROY
                    u.PostQuitMessage(0)
                    return 0
                return u.DefWindowProcW(hwnd, msg, wp, lp)

            self._proc = self.WNDPROC(wndproc)               # keep a reference
            hinst = k.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc, wc.hInstance = self._proc, hinst
            wc.lpszClassName = f"usbshare-tray-{self.kind}"
            u.RegisterClassW(ctypes.byref(wc))
            self._hwnd = u.CreateWindowExW(0, wc.lpszClassName, self.title, 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
            self._tip = self.title
            self._ok = bool(self._hwnd) and self._shell(
                self.NIM_ADD, self._nid(self.NIF_MESSAGE | self.NIF_ICON | self.NIF_TIP))
        except Exception as e:
            log.warning("tray icon not available: %s", e)
            self._ok = False
        finally:
            self._ready.set()
        if not self._ok:
            return
        msg = MSG()
        while u.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            u.TranslateMessage(ctypes.byref(msg))
            u.DispatchMessageW(ctypes.byref(msg))

    def _activate_default(self) -> None:
        for _label, cb, default in self.items:
            if cb is not None and default:
                self.ui.call(cb)
                return

    def _menu(self, hwnd) -> None:
        u = self._u
        menu = u.CreatePopupMenu()
        ids = {}
        for i, (label, cb, default) in enumerate(self.items, start=1):
            if cb is None:
                u.AppendMenuW(menu, 0x800, 0, None)                 # MF_SEPARATOR
            else:
                u.AppendMenuW(menu, 0, i, label)
                ids[i] = cb
                if default:
                    u.SetMenuDefaultItem(menu, i, 0)
        pt = self._ctypes.wintypes.POINT()
        u.GetCursorPos(self._ctypes.byref(pt))
        u.SetForegroundWindow(hwnd)
        # TPM_RETURNCMD | TPM_NONOTIFY | TPM_RIGHTBUTTON
        choice = u.TrackPopupMenu(menu, 0x0100 | 0x0080 | 0x0002, pt.x, pt.y, 0, hwnd, None)
        u.PostMessageW(hwnd, 0, 0, 0)
        u.DestroyMenu(menu)
        if choice in ids:
            self.ui.call(ids[choice])

    # -- public -------------------------------------------------------------------------------
    def update(self, badge: str, tooltip: str) -> None:
        if not self._ok:
            return
        tooltip = tooltip[:120]
        if badge == self._badge and tooltip == self._tip:
            return
        self._badge, self._tip = badge, tooltip
        try:
            self._shell(self.NIM_MODIFY, self._nid(self.NIF_ICON | self.NIF_TIP, tooltip))
        except Exception:
            pass

    def notify(self, title: str, text: str) -> None:
        if not self._ok:
            return
        try:
            self._shell(self.NIM_MODIFY, self._nid(self.NIF_INFO, self._tip, text, title))
        except Exception:
            pass

    def stop(self) -> None:
        if not self._ok:
            return
        self._ok = False
        try:
            self._shell(self.NIM_DELETE, self._nid(0))
            self._u.PostMessageW(self._hwnd, 0x0010, 0, 0)      # WM_CLOSE -> ends the thread
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
