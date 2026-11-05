# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""usbshare Client – Windows GUI (tkinter + Windows 11 style theme, tray icon)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from . import __author__, __url__, __version__
from .client import discover_servers, parse_server
from .gui_model import CREATE_NO_WINDOW, Controller, Server, Settings, device_label
from .ui_common import (IS_WIN, SingleInstance, Tray, UiQueue, apply_theme, dark_title_bar,
                        header, show_window)

ALL = "__all__"
TASK_NAME = "usbshare-client"
INSTANCE_PORT = 47321

STATE_TEXT = {
    "mine": "●  Verbunden",
    "available": "○  Frei",
    "other": "●  Belegt von {used_by}",
    "reserved": "⊘  Reserviert für {assigned}",
    "no_driver": "⚠  Server hat keinen Treiber",
    "working": "…  Bitte warten",
}


# -- Windows autostart (scheduled task: starts elevated at logon without UAC prompt) ------
def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          creationflags=CREATE_NO_WINDOW)


def autostart_supported() -> bool:
    return IS_WIN and getattr(sys, "frozen", False)


def autostart_enabled() -> bool:
    return autostart_supported() and _schtasks("/Query", "/TN", TASK_NAME).returncode == 0


def set_autostart(on: bool) -> bool:
    if on:
        cmd = f'"{sys.executable}" --minimized'
        r = _schtasks("/Create", "/TN", TASK_NAME, "/TR", cmd, "/SC", "ONLOGON", "/RL", "HIGHEST", "/F")
    else:
        r = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    return r.returncode == 0


class Dialog(tk.Toplevel):
    def __init__(self, parent, title: str):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.body = ttk.Frame(self, padding=20)
        self.body.pack(fill="both", expand=True)
        self.bind("<Escape>", lambda e: self.destroy())

    def run(self):
        self.update_idletasks()
        p = self.master
        x = p.winfo_rootx() + (p.winfo_width() - self.winfo_reqwidth()) // 2
        y = p.winfo_rooty() + (p.winfo_height() - self.winfo_reqheight()) // 3
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.deiconify()
        dark_title_bar(self, getattr(p, "_dark", False))
        self.grab_set()
        self.wait_window()


class ServerDialog(Dialog):
    def __init__(self, parent, title: str, server: Optional[Server] = None):
        super().__init__(parent, title)
        self.result: Optional[tuple] = None
        b = self.body
        ttk.Label(b, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        self.name = tk.StringVar(value=server.name if server else "")
        self.addr = tk.StringVar(value=server.address if server else "")
        self.enabled = tk.BooleanVar(value=server.enabled if server else True)
        ttk.Label(b, text="Name").grid(row=1, column=0, sticky="w", pady=(14, 2))
        e_name = ttk.Entry(b, textvariable=self.name, width=40)
        e_name.grid(row=2, column=0, columnspan=2, sticky="we")
        ttk.Label(b, text="Adresse").grid(row=3, column=0, sticky="w", pady=(10, 2))
        ttk.Entry(b, textvariable=self.addr, width=40).grid(row=4, column=0, columnspan=2, sticky="we")
        ttk.Label(b, text="IP oder Hostname, optional mit :Port (Standard 3240)",
                  style="Muted.TLabel").grid(row=5, column=0, columnspan=2, sticky="w", pady=(2, 0))
        ttk.Checkbutton(b, text="Diesen Server verwenden", variable=self.enabled,
                        style="Switch.TCheckbutton").grid(row=6, column=0, columnspan=2, sticky="w", pady=(14, 0))
        btns = ttk.Frame(b)
        btns.grid(row=7, column=0, columnspan=2, sticky="e", pady=(20, 0))
        ttk.Button(btns, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Speichern", style="Accent.TButton", command=self._ok).pack(side="right", padx=8)
        self.bind("<Return>", lambda e: self._ok())
        e_name.focus_set()
        self.run()

    def _ok(self):
        addr = self.addr.get().strip()
        if not addr:
            messagebox.showerror("Adresse fehlt", "Bitte die IP-Adresse oder den Hostnamen des Servers eingeben.", parent=self)
            return
        try:
            host, port = parse_server(addr)
            if not host or not 0 < port < 65536:
                raise ValueError
        except ValueError:
            messagebox.showerror("Ungültige Adresse", f"„{addr}“ ist keine gültige Adresse.", parent=self)
            return
        self.result = (self.name.get().strip() or host, host, port, self.enabled.get())
        self.destroy()


class SearchDialog(Dialog):
    """Searches the local network for usbshare servers that accept this PC."""

    def __init__(self, parent, existing: list):
        super().__init__(parent, "Server suchen")
        self.result: list = []
        self.existing = {(s.host, s.port) for s in existing}
        b = self.body
        ttk.Label(b, text="Server im Netzwerk", style="Section.TLabel").pack(anchor="w")
        self.lbl = ttk.Label(b, text="Suche …", style="Muted.TLabel")
        self.lbl.pack(anchor="w", pady=(2, 10))
        self.tv = ttk.Treeview(b, columns=("name", "addr", "info"), show="headings",
                               selectmode="extended", height=6)
        for col, text, w in (("name", "Name", 190), ("addr", "Adresse", 150), ("info", "", 150)):
            self.tv.heading(col, text=text, anchor="w")
            self.tv.column(col, width=w)
        self.tv.pack(fill="both", expand=True)
        ttk.Label(b, style="Muted.TLabel", wraplength=490, justify="left",
                  text="Es antworten nur Server im selben Netzwerk, die diesen PC zulassen. Nicht "
                       "gefunden? Dann über „Server hinzufügen …“ die IP von Hand eintragen.").pack(
            anchor="w", pady=(10, 0))
        btns = ttk.Frame(b)
        btns.pack(fill="x", pady=(18, 0))
        self.b_again = ttk.Button(btns, text="Erneut suchen", command=self.search)
        self.b_again.pack(side="left")
        ttk.Button(btns, text="Schließen", command=self.destroy).pack(side="right")
        self.b_add = ttk.Button(btns, text="Hinzufügen", style="Accent.TButton", command=self._ok)
        self.b_add.pack(side="right", padx=8)
        self.found: dict = {}
        self.search()
        self.run()

    def search(self):
        self.lbl.configure(text="Suche im Netzwerk …")
        self.b_again.state(["disabled"])
        self.b_add.state(["disabled"])

        def work():
            try:
                found = discover_servers()
            except OSError:
                found = []
            try:
                self.after(0, self._show, found)
            except (tk.TclError, RuntimeError):
                pass
        threading.Thread(target=work, daemon=True).start()

    def _show(self, found):
        self.tv.delete(*self.tv.get_children())
        self.found = {}
        for f in found:
            iid = f"{f['host']}:{f['port']}"
            known = (f["host"], f["port"]) in self.existing
            self.found[iid] = f
            addr = f["host"] if f["port"] == 3240 else iid
            self.tv.insert("", "end", iid=iid, values=(f.get("name", ""), addr,
                                                       "bereits eingetragen" if known else ""))
            if not known:
                self.tv.selection_add(iid)
        n = len(found)
        self.lbl.configure(text=f"{n} Server gefunden" if n else "Kein Server gefunden")
        self.b_again.state(["!disabled"])
        self.b_add.state(["!disabled"] if n else ["disabled"])

    def _ok(self):
        for iid in self.tv.selection():
            f = self.found[iid]
            if (f["host"], f["port"]) not in self.existing:
                self.result.append(Server(f.get("name") or f["host"], f["host"], int(f["port"]), True))
        self.destroy()


class LogDialog(Dialog):
    def __init__(self, parent, messages):
        super().__init__(parent, "Meldungen")
        self.resizable(True, True)
        ttk.Label(self.body, text="Meldungen", style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        holder = ttk.Frame(self.body)
        holder.pack(fill="both", expand=True)
        txt = tk.Text(holder, width=90, height=18, relief="flat", wrap="word",
                      font=("Consolas", 9), padx=8, pady=6)
        sb = ttk.Scrollbar(holder, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("end", "\n".join(messages) or "Noch keine Meldungen.")
        txt.see("end")
        txt.configure(state="disabled")
        ttk.Button(self.body, text="Schließen", command=self.destroy).pack(anchor="e", pady=(12, 0))
        self.run()


class App:
    def __init__(self, root: tk.Tk, controller: Controller):
        self.root, self.c = root, controller
        root.title("usbshare Client")
        root.geometry("1160x720")
        root.minsize(860, 500)
        self.pal = apply_theme(root, "client")
        root._dark = self.pal.dark
        self.ui = UiQueue(root)
        self._quitting = False
        self._told_tray = False

        self._build_header()
        self._build_body()
        self._build_statusbar()
        self._server_rows: list = []
        self._device_rows: list = []
        self._shown_notes = 0

        self.tray = Tray("client", "usbshare Client", self.ui, [
            ("usbshare Client öffnen", self.show, True),
            ("Jetzt aktualisieren", self.c.refresh_now, False),
            ("", None, False),
            ("Beenden", self.quit, False)])

        self.c.selected = ALL
        self._fill_servers(select=ALL)
        threading.Thread(target=self.c.run, daemon=True).start()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._tick()

    # -- layout ---------------------------------------------------------------------
    def _build_header(self):
        _f, self.subtitle, right = header(self.root, "client", "usbshare")
        ttk.Button(right, text="Server suchen", style="Accent.TButton",
                   command=self.search_servers).pack(side="left")
        ttk.Button(right, text="Server hinzufügen", command=self.add_server).pack(side="left", padx=8)
        mb = ttk.Menubutton(right, text="⋯", width=3)
        mb.pack(side="left")
        m = tk.Menu(mb, tearoff=False)
        self.autostart = tk.BooleanVar(value=autostart_enabled())
        self.to_tray = tk.BooleanVar(value=self.c.settings.minimize_on_close)
        m.add_command(label="Jetzt aktualisieren", command=self.c.refresh_now)
        m.add_command(label="Meldungen anzeigen …", command=self.show_log)
        m.add_separator()
        m.add_checkbutton(label="Mit Windows starten (im Infobereich)", variable=self.autostart,
                          command=self._toggle_autostart,
                          state="normal" if autostart_supported() else "disabled")
        m.add_checkbutton(label="Beim Schließen im Infobereich weiterlaufen", variable=self.to_tray,
                          command=self._toggle_tray)
        m.add_separator()
        m.add_command(label="Nach Updates suchen …", command=self.check_updates)
        m.add_command(label="usbip-win2 installieren / aktualisieren …", command=self.install_driver)
        m.add_command(label=f"Über usbshare {__version__}", command=lambda: messagebox.showinfo(
            "usbshare", f"usbshare Client {__version__}\nUSB-Geräte über das Netzwerk nutzen.\n\n"
            f"© 2026 {__author__}\n{__url__}\nLizenz: GNU GPL v3 oder neuer – freie Software, "
            f"ohne Gewährleistung.\n\nEinstellungen: {self.c.settings.path}"))
        m.add_separator()
        m.add_command(label="Beenden", command=self.quit)
        mb["menu"] = m

        self.banner = None
        if not self.c.usbip:
            ban = self.banner = ttk.Frame(self.root, style="Banner.TFrame", padding=(14, 10))
            ban.pack(fill="x", padx=18, pady=(4, 0))
            ttk.Label(ban, style="Banner.TLabel",
                      text="⚠  usbip-win2 ist nicht installiert – Geräte werden angezeigt, können aber "
                           "nicht verbunden werden.").pack(side="left")
            ttk.Button(ban, text="Installieren", command=self.install_driver).pack(side="right")

    def _build_body(self):
        body = ttk.Frame(self.root, padding=(18, 10, 18, 0))
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        # servers card
        left = ttk.Frame(body, style="Card.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        top = ttk.Frame(left)
        top.pack(fill="x")
        ttk.Label(top, text="Server", style="Section.TLabel").pack(side="left")
        self.b_del = ttk.Button(top, text="Entfernen", command=self.remove_server)
        self.b_del.pack(side="right")
        self.b_edit = ttk.Button(top, text="Bearbeiten", command=self.edit_server)
        self.b_edit.pack(side="right", padx=6)
        self.tv_srv = ttk.Treeview(left, columns=("use", "name", "status"), show="headings",
                                   selectmode="browse", height=10)
        for col, text, w, anchor in (("use", "", 34, "center"), ("name", "Name", 150, "w"),
                                     ("status", "Status", 110, "w")):
            self.tv_srv.heading(col, text=text, anchor=anchor)
            self.tv_srv.column(col, width=w, anchor=anchor, stretch=col == "name")
        self.tv_srv.tag_configure("off", foreground=self.pal.muted)
        self.tv_srv.tag_configure("offline", foreground=self.pal.err)
        self.tv_srv.tag_configure("online", foreground=self.pal.ok)
        self.tv_srv.pack(fill="both", expand=True, pady=(10, 0))
        self.tv_srv.bind("<<TreeviewSelect>>", self._on_server_select)
        self.tv_srv.bind("<Button-1>", self._on_server_click)
        self.tv_srv.bind("<Double-1>", lambda e: self.edit_server())
        ttk.Label(left, style="Muted.TLabel", wraplength=290, justify="left",
                  text="☑ = Server wird verwendet (laufend abgefragt und automatisch verbunden). "
                       "Anklicken schaltet um.").pack(fill="x", pady=(8, 0))

        # devices card
        right = ttk.Frame(body, style="Card.TFrame", padding=14)
        right.grid(row=0, column=1, sticky="nsew")
        top = ttk.Frame(right)
        top.pack(fill="x")
        ttk.Label(top, text="Geräte", style="Section.TLabel").pack(side="left")
        self.dev_sub = ttk.Label(top, style="Muted.TLabel")
        self.dev_sub.pack(side="left", padx=10)
        holder = ttk.Frame(right)
        holder.pack(fill="both", expand=True, pady=(10, 0))
        cols = ("device", "status", "server", "auto", "vidpid", "busid")
        self.tv_dev = ttk.Treeview(holder, columns=cols, show="headings", selectmode="browse")
        for col, text, w in (("device", "Gerät", 190), ("status", "Status", 210), ("server", "Server", 130),
                             ("auto", "Auto", 50), ("vidpid", "VID:PID", 85), ("busid", "Bus-ID", 60)):
            self.tv_dev.heading(col, text=text, anchor="center" if col == "auto" else "w")
            self.tv_dev.column(col, width=w, anchor="center" if col == "auto" else "w",
                               stretch=col in ("device", "status"))
        self.tv_dev.tag_configure("mine", foreground=self.pal.ok)
        self.tv_dev.tag_configure("other", foreground=self.pal.muted)
        self.tv_dev.tag_configure("reserved", foreground=self.pal.muted)
        self.tv_dev.tag_configure("no_driver", foreground=self.pal.warn)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.tv_dev.yview)
        self.tv_dev.configure(yscrollcommand=sb.set)
        self.tv_dev.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv_dev.bind("<<TreeviewSelect>>", lambda e: self._update_buttons())
        self.tv_dev.bind("<Double-1>", lambda e: self._toggle_connection())
        self.info = ttk.Label(right, style="Muted.TLabel", wraplength=700, justify="left")
        self.info.pack(fill="x", pady=(8, 0))
        btns = ttk.Frame(right)
        btns.pack(fill="x", pady=(10, 0))
        self.b_conn = ttk.Button(btns, text="Verbinden", style="Accent.TButton", width=14, command=self.connect)
        self.b_conn.pack(side="left")
        self.b_disc = ttk.Button(btns, text="Trennen", width=14, command=self.disconnect)
        self.b_disc.pack(side="left", padx=8)
        self.auto_var = tk.BooleanVar()
        self.cb_auto = ttk.Checkbutton(btns, text="Automatisch verbinden, sobald verfügbar",
                                       style="Switch.TCheckbutton", variable=self.auto_var,
                                       command=self._toggle_auto)
        self.cb_auto.pack(side="left", padx=16)

    def _build_statusbar(self):
        bar = ttk.Frame(self.root, padding=(18, 8, 18, 12))
        bar.pack(fill="x")
        self.last_msg = ttk.Label(bar, style="Muted.TLabel")
        self.last_msg.pack(side="left")
        ttk.Button(bar, text="Meldungen", command=self.show_log).pack(side="right")
        # settings visible at a glance (same variables as the entries in the ⋯ menu)
        ttk.Checkbutton(bar, text="Im Infobereich weiterlaufen", style="Switch.TCheckbutton",
                        variable=self.to_tray, command=self._toggle_tray).pack(side="right", padx=(0, 16))
        sw = ttk.Checkbutton(bar, text="Mit Windows starten", style="Switch.TCheckbutton",
                             variable=self.autostart, command=self._toggle_autostart)
        sw.pack(side="right", padx=(0, 16))
        if not autostart_supported():
            sw.state(["disabled"])

    # -- periodic refresh -------------------------------------------------------------
    def _tick(self):
        try:
            self._fill_servers()
            self._fill_devices()
            self._update_buttons()
            with self.c._lock:
                last = self.c.messages[-1] if self.c.messages else ""
                notes = self.c.notifications[self._shown_notes:]
                self._shown_notes = len(self.c.notifications)
            self.last_msg.configure(text=last)
            hidden = self.root.state() in ("withdrawn", "iconic")
            for n in notes:
                if hidden:
                    self.tray.notify("usbshare", n)
            self._update_tray()
        finally:
            self.root.after(700, self._tick)

    def _summary(self):
        mine = online = offline = 0
        for s in self.c.settings.servers:
            st = self.c.status.get(s.id)
            if not s.enabled or not st or st.online is None:
                continue
            if st.online:
                online += 1
                mine += sum(1 for d in st.devices if self.c.state_of(s.id, d) == "mine")
            else:
                offline += 1
        return mine, online, offline

    def _update_tray(self):
        mine, online, offline = self._summary()
        parts = [f"{mine} Gerät{'e' if mine != 1 else ''} verbunden"]
        if offline:
            parts.append(f"{offline} Server offline")
        self.subtitle.configure(text="Client  ·  " + "  ·  ".join(parts) +
                                (f"  ·  {online} Server online" if online else ""))
        self.tray.update("ok" if mine else ("warn" if offline else ""),
                         "usbshare Client – " + ", ".join(parts))

    def _fill_servers(self, select: Optional[str] = None):
        enabled = [s for s in self.c.settings.servers if s.enabled]
        rows = [(ALL, ("", "Alle Server", f"{len(enabled)} verwendet"), ())]
        for s in self.c.settings.servers:
            st = self.c.status.get(s.id)
            if not s.enabled and not (st and st.online is not None):
                status, tag = "aus", "off"
            elif not st or st.online is None:
                status, tag = "…", ""
            elif st.online:
                n = len(st.devices)
                status, tag = f"● online · {n}", "online" if s.enabled else "off"
            else:
                status, tag = "● offline", "offline" if s.enabled else "off"
            rows.append((s.id, ("☑" if s.enabled else "☐", s.name, status), (tag,)))
        if rows != self._server_rows:
            sel = select or self._sel(self.tv_srv)
            self.tv_srv.delete(*self.tv_srv.get_children())
            for iid, values, tags in rows:
                self.tv_srv.insert("", "end", iid=iid, values=values, tags=tags)
            self._server_rows = rows
            if sel and self.tv_srv.exists(sel):
                self.tv_srv.selection_set(sel)
        can = self._sel(self.tv_srv) not in (None, ALL)
        self.b_edit.state(["!disabled"] if can else ["disabled"])
        self.b_del.state(["!disabled"] if can else ["disabled"])

    def _visible_servers(self):
        sel = self.c.selected
        if sel == ALL:
            return [s for s in self.c.settings.servers if s.enabled]
        s = self.c.settings.server(sel) if sel else None
        return [s] if s else []

    def _fill_devices(self):
        rows, notes = [], []
        servers = self._visible_servers()
        for s in servers:
            st = self.c.status.get(s.id)
            if not st or st.online is None:
                notes.append(f"{s.name}: wird abgefragt …")
                continue
            if not st.online:
                notes.append(f"{s.name} ({s.address}) ist nicht erreichbar.")
                continue
            if not st.ext:
                notes.append(f"{s.name} ist ein Standard-USB/IP-Server: belegte Geräte und Namen "
                             f"werden nicht angezeigt.")
            for d in st.devices:
                state = self.c.state_of(s.id, d)
                text = STATE_TEXT.get(state, state).format(
                    used_by=d.get("used_by") or "?",
                    assigned=", ".join(a[:-3] if a.endswith("/32") else a
                                       for a in d.get("assigned_to") or []) or "andere Clients")
                rows.append((f"{s.id}|{d['busid']}",
                             (device_label(d), text, s.name, "✔" if self.c.is_auto(s.id, d) else "",
                              d["vidpid"], d["busid"]), (state,)))
        if not servers:
            notes.append("Noch kein Server eingetragen – „Server suchen“ findet Server im Netzwerk."
                         if not self.c.settings.servers else "Kein Server ist auf „verwenden“ gestellt.")
        elif not rows and not notes:
            notes.append("Zurzeit werden keine Geräte angeboten.")
        if rows != self._device_rows:
            sel = self._sel(self.tv_dev)
            self.tv_dev.delete(*self.tv_dev.get_children())
            for iid, values, tags in rows:
                self.tv_dev.insert("", "end", iid=iid, values=values, tags=tags)
            self._device_rows = rows
            if sel and self.tv_dev.exists(sel):
                self.tv_dev.selection_set(sel)
        self.info.configure(text="\n".join(notes))
        srv = self.c.settings.server(self.c.selected) if self.c.selected != ALL else None
        self.dev_sub.configure(text=f"{srv.name}  ·  {srv.address}" if srv else "alle verwendeten Server")

    # -- helpers ------------------------------------------------------------------------
    @staticmethod
    def _sel(tv) -> Optional[str]:
        s = tv.selection()
        return s[0] if s else None

    def _selected_device(self):
        iid = self._sel(self.tv_dev)
        if not iid:
            return None, None
        sid, busid = iid.split("|", 1)
        st = self.c.status.get(sid)
        dev = next((d for d in (st.devices if st else []) if d["busid"] == busid), None)
        return sid, dev

    def _update_buttons(self):
        sid, dev = self._selected_device()
        state = self.c.state_of(sid, dev) if dev else None
        self.b_conn.state(["!disabled"] if state == "available" and self.c.usbip else ["disabled"])
        self.b_disc.state(["!disabled"] if state == "mine" else ["disabled"])
        if dev:
            self.cb_auto.state(["!disabled"])
            self.auto_var.set(self.c.is_auto(sid, dev))
        else:
            self.cb_auto.state(["disabled"])
            self.auto_var.set(False)

    def _bg(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    # -- window / tray ----------------------------------------------------------------
    def show(self):
        show_window(self.root)

    def install_driver(self):
        from .update_ui import DriverDialog
        DriverDialog(self.root, on_installed=self._driver_installed)

    def _driver_installed(self):
        from .client import find_usbip_tool
        self.c.usbip = self.c.usbip or find_usbip_tool()
        if self.c.usbip and self.banner is not None:
            self.banner.destroy()
            self.banner = None

    def check_updates(self):
        from .update_ui import UpdateDialog
        UpdateDialog(self.root, "client", "usbshare Client", on_installed=self.quit)

    def show_log(self):
        with self.c._lock:
            msgs = list(self.c.messages)
        LogDialog(self.root, msgs)

    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        self.c.stop()
        self.tray.stop()
        self.root.destroy()

    def _on_close(self):
        if self.c.settings.minimize_on_close and self.tray.available:
            self.root.withdraw()
            if not self._told_tray:
                self._told_tray = True
                self.tray.notify("usbshare läuft weiter",
                                 "Der Client verbindet Geräte im Hintergrund. Öffnen und Beenden "
                                 "über das Symbol im Infobereich.")
            return
        if self.c.settings.auto and not self.tray.available:
            if messagebox.askyesno("usbshare beenden?",
                                   "Für „Automatisch verbinden“ muss usbshare weiterlaufen.\n"
                                   "Stattdessen minimieren?"):
                self.root.iconify()
                return
        self.quit()

    # -- events -------------------------------------------------------------------------
    def _on_server_select(self, _e=None):
        self.c.selected = self._sel(self.tv_srv)
        self._device_rows = None
        self.c.refresh_now()

    def _on_server_click(self, e):
        if self.tv_srv.identify_column(e.x) != "#1":
            return
        iid = self.tv_srv.identify_row(e.y)
        srv = self.c.settings.server(iid) if iid else None
        if srv:
            srv.enabled = not srv.enabled
            self.c.update_server(srv)

    def add_server(self):
        d = ServerDialog(self.root, "Server hinzufügen")
        if d.result:
            name, host, port, enabled = d.result
            srv = Server(name, host, port, enabled)
            self.c.add_server(srv)
            self._fill_servers(select=srv.id)
            self.tv_srv.selection_set(srv.id)

    def search_servers(self):
        d = SearchDialog(self.root, self.c.settings.servers)
        for srv in d.result:
            self.c.add_server(srv)
        if d.result:
            self._fill_servers(select=ALL)

    def edit_server(self):
        srv = self.c.settings.server(self._sel(self.tv_srv) or "")
        if not srv:
            return
        d = ServerDialog(self.root, "Server bearbeiten", srv)
        if d.result:
            srv.name, srv.host, srv.port, srv.enabled = d.result
            self.c.update_server(srv)

    def remove_server(self):
        srv = self.c.settings.server(self._sel(self.tv_srv) or "")
        if srv and messagebox.askyesno("Server entfernen", f"Server „{srv.name}“ entfernen?\n"
                                       "Bereits verbundene Geräte bleiben verbunden."):
            self.c.remove_server(srv.id)
            self._fill_servers(select=ALL)
            self.tv_srv.selection_set(ALL)

    def connect(self):
        sid, dev = self._selected_device()
        if dev:
            self._bg(self.c.attach, sid, dev["busid"])

    def disconnect(self):
        sid, dev = self._selected_device()
        if not dev:
            return
        if self.c.is_auto(sid, dev):
            if not messagebox.askyesno(
                    "Automatisch verbinden ist aktiv",
                    "Für dieses Gerät ist „Automatisch verbinden“ eingeschaltet.\n"
                    "Damit es nicht sofort wieder verbunden wird, wird das abgeschaltet. Fortfahren?"):
                return
            self.c.set_auto(sid, dev, False)
        self._bg(self.c.detach, sid, dev["busid"])

    def _toggle_connection(self):
        sid, dev = self._selected_device()
        if dev:
            state = self.c.state_of(sid, dev)
            if state == "available":
                self.connect()
            elif state == "mine":
                self.disconnect()

    def _toggle_auto(self):
        sid, dev = self._selected_device()
        if dev:
            self.c.set_auto(sid, dev, self.auto_var.get())

    def _toggle_autostart(self):
        if not set_autostart(self.autostart.get()):
            messagebox.showerror("Autostart", "Autostart konnte nicht geändert werden.")
            self.autostart.set(autostart_enabled())

    def _toggle_tray(self):
        self.c.settings.minimize_on_close = self.to_tray.get()
        self.c.settings.save()


def main(argv=None) -> int:
    import logging
    argv = sys.argv[1:] if argv is None else argv
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    after_update = "--after-update" in argv
    inst = SingleInstance(INSTANCE_PORT, wait=15.0 if after_update else 0.0)
    if not inst.first:
        inst.notify_running()          # bring the running client to the front
        return 0
    if after_update:
        from .updater import cleanup_old_exe
        cleanup_old_exe()
    settings = Settings(os.environ.get("USBSHARE_CLIENT_CONFIG"))
    root = tk.Tk()
    app = App(root, Controller(settings))
    inst.listen(lambda: app.ui.call(app.show))
    if "--minimized" in argv:
        if app.tray.available:
            root.withdraw()
        else:
            root.iconify()
    elif not settings.servers:
        root.after(500, app.search_servers)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
