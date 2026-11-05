# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""usbshare Server window: share devices, assign them to clients, manage allowed clients.

Talks to the running server service on this machine (admin requests are only accepted
from the machine itself), so it needs no administrator rights."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import List, Optional

from . import __author__, __url__, __version__
from . import protocol as P
from .client import admin_request
from .gui import Dialog
from .gui_model import device_label
from .ui_common import (IS_WIN, SingleInstance, Tray, UiQueue, apply_theme, header,
                        run_key_enabled, set_run_key, show_window)

RUN_NAME = "usbshare Server"
INSTANCE_PORT = 47322


def _client_text(ip: Optional[str], name: str = "") -> str:
    if not ip:
        return ""
    return f"{ip} ({name})" if name else ip


class ClientPicker(Dialog):
    """Choose clients: 'all' or a list of IPs/networks (known clients offered as switches)."""

    def __init__(self, parent, title: str, intro: str, all_label: str, selected: List[str],
                 known: List[dict]):
        super().__init__(parent, title)
        self.result: Optional[List[str]] = None
        b = self.body
        ttk.Label(b, text=title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(b, text=intro, wraplength=440, justify="left", style="Muted.TLabel").pack(
            anchor="w", pady=(2, 12))
        self.mode = tk.StringVar(value="some" if selected else "all")
        ttk.Radiobutton(b, text=all_label, value="all", variable=self.mode,
                        command=self._update).pack(anchor="w")
        ttk.Radiobutton(b, text="Nur diese Clients:", value="some", variable=self.mode,
                        command=self._update).pack(anchor="w", pady=(6, 4))
        box = ttk.Frame(b, padding=(28, 0, 0, 0))
        box.pack(fill="x")
        self.vars = []
        known_ips = []
        for c in known:
            v = tk.BooleanVar(value=c["ip"] in selected)
            cb = ttk.Checkbutton(box, text=_client_text(c["ip"], c.get("name", "")) +
                                 ("" if c.get("allowed", True) else "   (zurzeit abgelehnt)"),
                                 variable=v)
            cb.pack(anchor="w", pady=1)
            self.vars.append((c["ip"], v, cb))
            known_ips.append(c["ip"])
        if not known:
            ttk.Label(box, style="Muted.TLabel", text="Noch hat sich kein Client gemeldet.").pack(anchor="w")
        ttk.Label(box, text="Weitere IPs oder Netze, mit Komma getrennt (z. B. 192.168.1.60, 10.0.0.0/24)",
                  style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
        self.extra = tk.StringVar(value=", ".join(s for s in selected if s not in known_ips))
        self.e_extra = ttk.Entry(box, textvariable=self.extra, width=50)
        self.e_extra.pack(anchor="w", fill="x")
        btns = ttk.Frame(b)
        btns.pack(fill="x", pady=(20, 0))
        ttk.Button(btns, text="Abbrechen", command=self.destroy).pack(side="right")
        ttk.Button(btns, text="Speichern", style="Accent.TButton", command=self._ok).pack(side="right", padx=8)
        self._update()
        self.run()

    def _update(self):
        state = ["!disabled"] if self.mode.get() == "some" else ["disabled"]
        for _ip, _v, cb in self.vars:
            cb.state(state)
        self.e_extra.state(state)

    def _ok(self):
        if self.mode.get() == "all":
            self.result = []
        else:
            chosen = [ip for ip, v, _cb in self.vars if v.get()]
            chosen += [x.strip() for x in self.extra.get().replace(";", ",").split(",") if x.strip()]
            if not chosen:
                messagebox.showerror("Kein Client gewählt",
                                     "Bitte mindestens einen Client auswählen oder eintragen.", parent=self)
                return
            self.result = list(dict.fromkeys(chosen))
        self.destroy()


def _prefs_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "usbshare", "server-window.json")


def _load_prefs() -> dict:
    try:
        with open(_prefs_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_prefs(prefs: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_prefs_path()), exist_ok=True)
        with open(_prefs_path(), "w", encoding="utf-8") as f:
            json.dump(prefs, f)
    except OSError:
        pass


class ServerWindow:
    def __init__(self, root: tk.Tk, port: int):
        self.root, self.port = root, port
        self.status: Optional[dict] = None
        self.error = ""
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._dev_rows = None
        self._cli_rows = None
        self._prev_used: Optional[dict] = None
        self._quitting = False
        self._told_tray = False
        self.prefs = _load_prefs()

        root.title("usbshare Server")
        root.geometry("1060x700")
        root.minsize(820, 520)
        self.pal = apply_theme(root, "server")
        root._dark = self.pal.dark
        self.ui = UiQueue(root)

        _f, self.subtitle, right = header(root, "server", "usbshare Server")
        self.state_lbl = ttk.Label(right, style="Section.TLabel")
        self.state_lbl.pack(side="left", padx=(0, 14))
        ttk.Button(right, text="Nach Updates suchen", command=self.check_updates).pack(
            side="left", padx=(0, 8))
        mb = ttk.Menubutton(right, text="⋯", width=3)
        mb.pack(side="left")
        m = tk.Menu(mb, tearoff=False)
        self.autostart = tk.BooleanVar(value=run_key_enabled(RUN_NAME))
        self.to_tray = tk.BooleanVar(value=self.prefs.get("close_to_tray", True))
        m.add_command(label="Zugelassene Clients …", command=self.edit_allowed)
        m.add_command(label="Konfigurationsdatei öffnen", command=self.open_config)
        m.add_command(label="Nach Updates suchen …", command=self.check_updates)
        m.add_separator()
        m.add_checkbutton(label="Beim Anmelden im Infobereich starten", variable=self.autostart,
                          command=self._toggle_autostart,
                          state="normal" if IS_WIN and getattr(sys, "frozen", False) else "disabled")
        m.add_checkbutton(label="Beim Schließen im Infobereich bleiben", variable=self.to_tray,
                          command=self._toggle_tray)
        m.add_separator()
        m.add_command(label=f"Über usbshare {__version__}", command=lambda: messagebox.showinfo(
            "usbshare", f"usbshare Server {__version__}\n\nDieses Fenster steuert den usbshare-Dienst. "
            "Der Dienst läuft auch weiter, wenn das Fenster geschlossen ist.\n\n"
            f"© 2026 {__author__}\n{__url__}\nLizenz: GNU GPL v3 oder neuer – freie Software, "
            "ohne Gewährleistung."))
        m.add_command(label="Fenster beenden", command=self.quit)
        mb["menu"] = m

        body = ttk.Frame(root, padding=(18, 10, 18, 16))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=3)
        body.rowconfigure(1, weight=2)

        dev = ttk.Frame(body, style="Card.TFrame", padding=14)
        dev.grid(row=0, column=0, sticky="nsew")
        top = ttk.Frame(dev)
        top.pack(fill="x")
        ttk.Label(top, text="USB-Geräte an diesem Rechner", style="Section.TLabel").pack(side="left")
        ttk.Label(top, style="Muted.TLabel", text="Gerät auswählen, dann freigeben und Clients festlegen "
                  "(Doppelklick)").pack(side="left", padx=10)
        holder = ttk.Frame(dev)
        holder.pack(fill="both", expand=True, pady=(10, 0))
        cols = ("shared", "device", "access", "state", "vidpid", "busid")
        self.tv = ttk.Treeview(holder, columns=cols, show="headings", selectmode="browse", height=7)
        for col, text, w in (("shared", "Freigabe", 80), ("device", "Gerät", 210),
                             ("access", "Zugriff für", 200), ("state", "Status", 230),
                             ("vidpid", "VID:PID", 85), ("busid", "Bus-ID", 60)):
            self.tv.heading(col, text=text, anchor="center" if col == "shared" else "w")
            self.tv.column(col, width=w, anchor="center" if col == "shared" else "w",
                           stretch=col in ("device", "access", "state"))
        self.tv.tag_configure("off", foreground=self.pal.muted)
        self.tv.tag_configure("used", foreground=self.pal.ok)
        self.tv.tag_configure("nodrv", foreground=self.pal.warn)
        sb = ttk.Scrollbar(holder, orient="vertical", command=self.tv.yview)
        self.tv.configure(yscrollcommand=sb.set)
        self.tv.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tv.bind("<<TreeviewSelect>>", lambda e: self._update_buttons())
        self.tv.bind("<Button-1>", self._on_dev_click)
        self.tv.bind("<Double-1>", lambda e: self.assign())
        b = ttk.Frame(dev)
        b.pack(fill="x", pady=(10, 0))
        self.share_var = tk.BooleanVar()
        self.sw_share = ttk.Checkbutton(b, text="Freigegeben", style="Switch.TCheckbutton",
                                        variable=self.share_var, command=self._switch_share)
        self.sw_share.pack(side="left")
        self.b_assign = ttk.Button(b, text="Clients festlegen …", style="Accent.TButton", command=self.assign)
        self.b_assign.pack(side="left", padx=16)
        self.b_kick = ttk.Button(b, text="Vom Client trennen", command=self.kick)
        self.b_kick.pack(side="left")

        cli = ttk.Frame(body, style="Card.TFrame", padding=14)
        cli.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        top = ttk.Frame(cli)
        top.pack(fill="x")
        ttk.Label(top, text="Clients", style="Section.TLabel").pack(side="left")
        self.allowed_lbl = ttk.Label(top, style="Muted.TLabel")
        self.allowed_lbl.pack(side="left", padx=10)
        ttk.Button(top, text="Zugelassene Clients …", command=self.edit_allowed).pack(side="right")
        holder = ttk.Frame(cli)
        holder.pack(fill="both", expand=True, pady=(10, 0))
        ccols = ("name", "ip", "allowed", "using", "seen")
        self.tc = ttk.Treeview(holder, columns=ccols, show="headings", selectmode="none", height=4)
        for col, text, w in (("name", "Name", 160), ("ip", "IP-Adresse", 130), ("allowed", "Zugelassen", 100),
                             ("using", "Nutzt", 300), ("seen", "Zuletzt gesehen", 130)):
            self.tc.heading(col, text=text, anchor="w")
            self.tc.column(col, width=w, stretch=col == "using")
        self.tc.tag_configure("denied", foreground=self.pal.err)
        self.tc.pack(fill="both", expand=True)

        self.tray = Tray("server", "usbshare Server", self.ui, [
            ("usbshare Server öffnen", self.show, True),
            ("", None, False),
            ("Fenster beenden (Dienst läuft weiter)", self.quit, False)])
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        threading.Thread(target=self._poll, daemon=True).start()
        self._tick()

    # -- data --------------------------------------------------------------------
    def _poll(self):
        while True:
            try:
                r = admin_request({"action": "status"}, port=self.port)
                with self._lock:
                    self.status, self.error = r.get("status"), ""
            except OSError as e:
                with self._lock:
                    self.status, self.error = None, str(e) or e.__class__.__name__
            except Exception as e:  # pragma: no cover
                with self._lock:
                    self.error = str(e)
            self._wake.wait(2.0)
            self._wake.clear()

    def _do(self, req: dict):
        def work():
            try:
                r = admin_request(req, port=self.port)
            except OSError as e:
                r = {"ok": False, "error": str(e)}
            with self._lock:
                if r.get("status"):
                    self.status = r["status"]
            if not r.get("ok"):
                self.ui.call(lambda: messagebox.showerror("usbshare Server", r.get("error", "Fehler")))
            self._wake.set()
        threading.Thread(target=work, daemon=True).start()

    # -- refresh -------------------------------------------------------------------
    def _tick(self):
        try:
            self._render()
        finally:
            self.root.after(700, self._tick)

    def _render(self):
        with self._lock:
            st, err = self.status, self.error
        if st is None:
            self.state_lbl.configure(text="●  Dienst läuft nicht" if err else "…", style="Err.TLabel")
            self.subtitle.configure(text=(f"Keine Verbindung zum usbshare-Dienst (Port {self.port}). "
                                          "Ist der Server installiert und gestartet?") if err else "Verbinde …")
            rows, crows = [], []
            self.tray.update("warn", "usbshare Server – Dienst läuft nicht")
        else:
            self.state_lbl.configure(text="●  Server läuft", style="Ok.TLabel")
            note = f"{st['hostname']}  ·  Port {st['port']}"
            if not st.get("config_path"):
                note += "  ·  ohne Konfigurationsdatei (Änderungen nur bis zum Neustart)"
            elif not st.get("config_writable"):
                note += "  ·  Konfiguration nicht beschreibbar!"
            if not st.get("discovery"):
                note += "  ·  Serversuche aus"
            self.subtitle.configure(text=note)
            names = {c["ip"]: c.get("name", "") for c in st["clients"]}
            rows = []
            used = {}
            for d in st["devices"]:
                owners = [c[:-3] if c.endswith("/32") else c for c in d["assigned_to"]]
                access = "Alle Clients" if not owners else \
                    ", ".join(_client_text(c, names.get(c, "")) for c in owners)
                if not d["shared"]:
                    state, tag = "Nicht freigegeben", "off"
                elif d["used_by"]:
                    state, tag = "●  Verbunden mit " + (d.get("used_by_name") or d["used_by"]), "used"
                    used[d["busid"]] = (device_label(d), d.get("used_by_name") or d["used_by"])
                elif d.get("no_driver"):
                    state, tag = "⚠  Kein Treiber (UsbDk/WinUSB)", "nodrv"
                else:
                    state, tag = "○  Frei", ""
                rows.append((d["busid"], ("☑" if d["shared"] else "☐", device_label(d),
                                          access if d["shared"] else "–", state,
                                          d["vidpid"], d["busid"]), (tag,)))
            self._notify_changes(used)
            now = st.get("now", time.time())
            dev_names = {d["busid"]: device_label(d) for d in st["devices"]}
            crows = []
            for c in st["clients"]:
                ago = int(now - c["last_seen"])
                seen = "gerade eben" if ago < 60 else f"vor {ago // 60} min" if ago < 3600 else \
                    time.strftime("%d.%m. %H:%M", time.localtime(c["last_seen"]))
                crows.append((c["ip"], (c.get("name") or "–", c["ip"], "ja" if c["allowed"] else "nein",
                                        ", ".join(dev_names.get(b, b) for b in c["using"]), seen),
                               ("" if c["allowed"] else "denied",)))
            allowed = st.get("allow_clients") or []
            self.allowed_lbl.configure(text="alle Clients im Netzwerk zugelassen" if not allowed
                                       else "zugelassen: " + ", ".join(allowed))
            n = len(used)
            self.tray.update("ok" if n else "", f"usbshare Server – {n} Gerät{'e' if n != 1 else ''} verbunden")
        if rows != self._dev_rows:
            sel = self.tv.selection()
            self.tv.delete(*self.tv.get_children())
            for iid, values, tags in rows:
                self.tv.insert("", "end", iid=iid, values=values, tags=tags)
            self._dev_rows = rows
            if sel and self.tv.exists(sel[0]):
                self.tv.selection_set(sel[0])
        if crows != self._cli_rows:
            self.tc.delete(*self.tc.get_children())
            for iid, values, tags in crows:
                self.tc.insert("", "end", iid=iid, values=values, tags=tags)
            self._cli_rows = crows
        self._update_buttons()

    def _notify_changes(self, used: dict):
        prev, self._prev_used = self._prev_used, used
        if prev is None or self.root.state() not in ("withdrawn", "iconic"):
            return
        for busid, (dev, who) in used.items():
            if prev.get(busid) != (dev, who):
                self.tray.notify("Gerät verbunden", f"{who} nutzt jetzt {dev}")
        for busid, (dev, who) in prev.items():
            if busid not in used:
                self.tray.notify("Gerät getrennt", f"{dev} ist wieder frei")

    # -- actions ----------------------------------------------------------------------
    def _selected(self) -> Optional[dict]:
        sel = self.tv.selection()
        with self._lock:
            st = self.status
        if not sel or not st:
            return None
        return next((d for d in st["devices"] if d["busid"] == sel[0]), None)

    def _update_buttons(self):
        d = self._selected()
        self.sw_share.state(["!disabled"] if d else ["disabled"])
        self.share_var.set(bool(d and d["shared"]))
        self.b_assign.state(["!disabled"] if d and d["shared"] else ["disabled"])
        self.b_kick.state(["!disabled"] if d and d["used_by"] else ["disabled"])

    def _on_dev_click(self, e):
        if self.tv.identify_column(e.x) == "#1" and self.tv.identify_row(e.y):
            self.tv.selection_set(self.tv.identify_row(e.y))
            self.root.after(10, self.toggle_share)

    def _switch_share(self):
        d = self._selected()
        if d and bool(d["shared"]) != self.share_var.get():
            self.toggle_share()

    def toggle_share(self):
        d = self._selected()
        if not d:
            return
        if d["shared"] and d["used_by"] and not messagebox.askyesno(
                "Gerät wird benutzt", f"{device_label(d)} wird gerade von {d['used_by']} benutzt.\n"
                "Nicht mehr freigeben und dort trennen?"):
            self.share_var.set(True)
            return
        self._do({"action": "share", "busid": d["busid"], "shared": not d["shared"]})

    def assign(self):
        d = self._selected()
        if not d or not d["shared"]:
            return
        with self._lock:
            known = list(self.status["clients"]) if self.status else []
        current = [c[:-3] if c.endswith("/32") else c for c in d["assigned_to"]]
        dlg = ClientPicker(self.root, "Clients festlegen",
                           f"Welche Clients dürfen „{device_label(d)}“ ({d['vidpid']}) benutzen?",
                           "Alle zugelassenen Clients", current, known)
        if dlg.result is not None:
            self._do({"action": "assign", "busid": d["busid"], "clients": dlg.result})

    def kick(self):
        d = self._selected()
        if d and d["used_by"] and messagebox.askyesno(
                "Vom Client trennen", f"{device_label(d)} von {d['used_by']} trennen?\n"
                "Für den Client sieht das aus, als würde das Gerät abgesteckt."):
            self._do({"action": "release", "busid": d["busid"]})

    def edit_allowed(self):
        with self._lock:
            st = self.status
        if not st:
            return
        current = [c[:-3] if c.endswith("/32") else c for c in st.get("allow_clients") or []]
        dlg = ClientPicker(self.root, "Zugelassene Clients",
                           "Welche Rechner/VMs dürfen sich überhaupt mit diesem Server verbinden? "
                           "Nicht zugelassene Clients finden den Server auch bei der Suche nicht.",
                           "Alle Clients im Netzwerk", current, st["clients"])
        if dlg.result is not None:
            self._do({"action": "allow_clients", "clients": dlg.result})

    def open_config(self):
        with self._lock:
            path = (self.status or {}).get("config_path")
        if not path:
            messagebox.showinfo("Konfiguration", "Der Server läuft ohne Konfigurationsdatei.")
            return
        try:
            if IS_WIN:
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            messagebox.showerror("Konfiguration", f"{path}\n\n{e}")

    def check_updates(self):
        from .update_ui import UpdateDialog
        UpdateDialog(self.root, "server", "usbshare Server", on_installed=self._restart_after_update)

    def _restart_after_update(self):
        """Linux: the service was updated and restarted; reopen this window with the new binary."""
        from .updater import frozen_executable
        exe = frozen_executable()
        self.tray.stop()
        if exe and not IS_WIN:
            self.root.destroy()
            os.execv(exe, [exe, "gui", "--port", str(self.port)])
        messagebox.showinfo("usbshare Server", "Update installiert.")

    # -- window / tray -----------------------------------------------------------------
    def show(self):
        show_window(self.root)

    def quit(self):
        if self._quitting:
            return
        self._quitting = True
        self.tray.stop()
        self.root.destroy()

    def _on_close(self):
        if self.to_tray.get() and self.tray.available:
            self.root.withdraw()
            if not self._told_tray:
                self._told_tray = True
                self.tray.notify("usbshare Server", "Das Fenster ist im Infobereich. Der Server-Dienst "
                                 "läuft unabhängig davon immer weiter.")
            return
        self.quit()

    def _toggle_autostart(self):
        cmd = f'"{sys.executable}" --minimized' if self.autostart.get() else None
        if not set_run_key(RUN_NAME, cmd):
            messagebox.showerror("Autostart", "Autostart konnte nicht geändert werden.")
            self.autostart.set(run_key_enabled(RUN_NAME))

    def _toggle_tray(self):
        self.prefs["close_to_tray"] = self.to_tray.get()
        _save_prefs(self.prefs)


def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="usbshare-server-gui")
    p.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    p.add_argument("--minimized", action="store_true")
    a = p.parse_args(sys.argv[1:] if argv is None else argv)
    if IS_WIN:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    inst = SingleInstance(INSTANCE_PORT)
    if not inst.first:
        inst.notify_running()
        return 0
    root = tk.Tk()
    win = ServerWindow(root, a.port)
    inst.listen(lambda: win.ui.call(win.show))
    if a.minimized and win.tray.available:
        root.withdraw()
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
