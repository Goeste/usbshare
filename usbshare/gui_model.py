# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""GUI-independent logic of the Windows client: settings, polling, (auto-)attach, detach."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import protocol as P
from .client import find_usbip_tool, parse_server, remote_devlist_ext, remote_release
from .server import DeviceFilter
from .protocol import DeviceInfo

log = logging.getLogger("usbshare.gui")

CREATE_NO_WINDOW = 0x08000000 if sys.platform.startswith("win") else 0


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "usbshare", "client.json")


@dataclass
class Server:
    name: str
    host: str
    port: int = P.DEFAULT_PORT
    enabled: bool = True
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def address(self) -> str:
        return self.host if self.port == P.DEFAULT_PORT else f"{self.host}:{self.port}"

    @classmethod
    def from_address(cls, name: str, address: str, enabled: bool = True) -> "Server":
        host, port = parse_server(address.strip())
        return cls(name.strip() or host, host, port, enabled)


@dataclass
class ServerStatus:
    online: Optional[bool] = None        # None = noch nicht abgefragt
    error: str = ""
    ext: bool = False
    hostname: str = ""
    you: Optional[str] = None
    devices: List[dict] = field(default_factory=list)
    updated: float = 0.0


class Settings:
    def __init__(self, path: Optional[str] = None):
        self.path = path or config_path()
        self.servers: List[Server] = []
        self.auto: List[Tuple[str, str]] = []      # (server id, "vid:pid" or busid)
        self.ports: Dict[str, int] = {}            # "serverid/busid" -> local usbip port
        self.minimize_on_close = True      # close button -> keep running in the tray
        self.load()

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            return
        self.servers = [Server(**s) for s in d.get("servers", [])]
        self.auto = [tuple(a) for a in d.get("auto", [])]
        self.ports = dict(d.get("ports", {}))
        self.minimize_on_close = d.get("minimize_on_close", True)

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"servers": [s.__dict__ for s in self.servers],
                       "auto": [list(a) for a in self.auto], "ports": self.ports,
                       "minimize_on_close": self.minimize_on_close}, f, indent=2)
        os.replace(tmp, self.path)

    def server(self, sid: str) -> Optional[Server]:
        return next((s for s in self.servers if s.id == sid), None)


def _devinfo(dev: dict) -> DeviceInfo:
    return DeviceInfo(dev["busid"], 0, 0, 0, dev["vid"], dev["pid"], 0, 0, 0, 0, 0, 0)


def device_label(dev: dict) -> str:
    if dev.get("description") and not dev["description"].startswith("["):
        return dev["description"]
    classes = {1: "Audio", 2: "Kommunikation", 3: "Eingabegerät (HID)", 6: "Kamera/Scanner",
               7: "Drucker", 8: "Massenspeicher", 9: "Hub", 0x0A: "CDC-Daten",
               0x0B: "Smartcard", 0x0E: "Video", 0xE0: "Funk/Bluetooth", 0xFF: "Herstellerspezifisch"}
    cls = dev.get("class") or next((i[0] for i in dev.get("interfaces", [])), None)
    return f"USB-Gerät ({classes[cls]})" if cls in classes else "USB-Gerät"


class Controller:
    """Holds the state the window displays. All public methods are thread-safe."""

    POLL_INTERVAL = 3.0
    RETRY_AFTER = 20.0

    def __init__(self, settings: Settings, usbip: Optional[str] = None,
                 runner: Callable[..., subprocess.CompletedProcess] = subprocess.run):
        self.settings = settings
        self.usbip = usbip or find_usbip_tool()
        self._run = runner
        self.status: Dict[str, ServerStatus] = {}
        self.selected: Optional[str] = None
        self.messages: List[str] = []
        self.notifications: List[str] = []    # for tray balloons
        self._lock = threading.RLock()
        self._busy: set = set()                 # (sid, busid) currently being (de)attached
        self._failed: Dict[Tuple[str, str], float] = {}
        self._stop = threading.Event()
        self._wake = threading.Event()

    # -- messages ------------------------------------------------------------
    def say(self, text: str, notify: bool = False) -> None:
        log.info(text)
        with self._lock:
            self.messages.append(time.strftime("%H:%M:%S ") + text)
            del self.messages[:-200]
            if notify:
                self.notifications.append(text)

    def dev_name(self, sid: str, busid: str) -> str:
        st = self.status.get(sid)
        dev = next((d for d in (st.devices if st else []) if d["busid"] == busid), None)
        return device_label(dev) if dev else busid

    # -- device state as seen from this client -------------------------------
    def state_of(self, sid: str, dev: dict) -> str:
        """mine | available | other | reserved | no_driver | working"""
        st = self.status.get(sid)
        if (sid, dev["busid"]) in self._busy:
            return "working"
        if dev["state"] == "in_use":
            if st and st.you and dev.get("used_by") == st.you:
                return "mine"
            return "other"
        return dev["state"]

    def is_auto(self, sid: str, dev: dict) -> bool:
        info = _devinfo(dev)
        return any(a_sid == sid and DeviceFilter._match(rule, info)
                   for a_sid, rule in self.settings.auto)

    def set_auto(self, sid: str, dev: dict, on: bool) -> None:
        with self._lock:
            info = _devinfo(dev)
            self.settings.auto = [a for a in self.settings.auto
                                  if not (a[0] == sid and DeviceFilter._match(a[1], info))]
            if on:
                self.settings.auto.append((sid, dev["vidpid"]))
            self.settings.save()
        self._wake.set()

    # -- polling --------------------------------------------------------------
    def poll_once(self) -> None:
        with self._lock:
            targets = [s for s in self.settings.servers if s.enabled or s.id == self.selected]
        threads = [threading.Thread(target=self._poll_server, args=(s,), daemon=True)
                   for s in targets]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=8)
        self._auto_connect()

    def _poll_server(self, srv: Server) -> None:
        try:
            data = remote_devlist_ext(srv.host, srv.port)
            st = ServerStatus(True, "", data.get("ext", False), data.get("hostname", ""),
                              data.get("you"), data.get("devices", []), time.time())
        except OSError as e:
            st = ServerStatus(False, str(e) or e.__class__.__name__, updated=time.time())
        with self._lock:
            prev = self.status.get(srv.id)
            if prev is None or prev.online != st.online:
                self.say(f"Server {srv.name} " + ("ist erreichbar" if st.online else
                                                  f"ist nicht erreichbar ({st.error})"),
                         notify=prev is not None and prev.online is not None and srv.enabled)
            self.status[srv.id] = st

    def _auto_connect(self) -> None:
        if not self.usbip:
            return              # nothing can be attached without usbip-win2
        now = time.time()
        jobs = []
        with self._lock:
            for srv in self.settings.servers:
                st = self.status.get(srv.id)
                if not srv.enabled or not st or not st.online:
                    continue
                for dev in st.devices:
                    key = (srv.id, dev["busid"])
                    if (self.state_of(srv.id, dev) == "available" and self.is_auto(srv.id, dev)
                            and now - self._failed.get(key, 0) > self.RETRY_AFTER):
                        jobs.append((srv, dev))
        for srv, dev in jobs:
            self.attach(srv.id, dev["busid"], auto=True)

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:  # pragma: no cover
                log.exception("poll failed")
            self._wake.wait(self.POLL_INTERVAL)
            self._wake.clear()

    def refresh_now(self) -> None:
        self._wake.set()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    # -- actions ----------------------------------------------------------------
    def _exec(self, args: List[str]) -> subprocess.CompletedProcess:
        return self._run([self.usbip] + args, capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW, timeout=60)

    def attach(self, sid: str, busid: str, auto: bool = False) -> bool:
        srv = self.settings.server(sid)
        if srv is None:
            return False
        if not self.usbip:
            self.say("usbip-win2 ist nicht installiert – Verbinden nicht möglich.")
            return False
        key = (sid, busid)
        with self._lock:
            if key in self._busy:
                return False
            self._busy.add(key)
        try:
            args = []
            if srv.port != P.DEFAULT_PORT:
                args += ["--tcp-port", str(srv.port)]
            r = self._exec(args + ["attach", "-r", srv.host, "-b", busid])
            out = (r.stdout or "") + (r.stderr or "")
            if r.returncode != 0:
                self._failed[key] = time.time()
                self.say(f"Verbinden von {self.dev_name(sid, busid)} ({srv.name}) fehlgeschlagen: "
                         f"{out.strip() or 'Fehlercode %d' % r.returncode}", notify=auto)
                return False
            m = re.search(r"port\s+(\d+)", out, re.I)
            with self._lock:
                if m:
                    self.settings.ports[f"{sid}/{busid}"] = int(m.group(1))
                    self.settings.save()
            self._failed.pop(key, None)
            self.say(f"{self.dev_name(sid, busid)} von {srv.name} "
                     f"{'automatisch ' if auto else ''}verbunden", notify=auto)
            return True
        except (OSError, subprocess.SubprocessError) as e:
            self._failed[key] = time.time()
            self.say(f"Verbinden fehlgeschlagen: {e}")
            return False
        finally:
            with self._lock:
                self._busy.discard(key)
            self._wake.set()

    def detach(self, sid: str, busid: str) -> bool:
        srv = self.settings.server(sid)
        if srv is None:
            return False
        key = (sid, busid)
        with self._lock:
            if key in self._busy:
                return False
            self._busy.add(key)
            port = self.settings.ports.pop(f"{sid}/{busid}", None)
            self.settings.save()
        try:
            if port is not None and self.usbip:
                r = self._exec(["detach", "-p", str(port)])
                if r.returncode == 0:
                    self.say(f"{self.dev_name(sid, busid)} von {srv.name} getrennt")
                    return True
            # Fallback: ask the server to drop the connection (the device gets unplugged here)
            status = remote_release(srv.host, srv.port, busid)
            if status == P.ST_OK:
                self.say(f"{self.dev_name(sid, busid)} von {srv.name} getrennt")
                return True
            self.say(f"Trennen von {busid} fehlgeschlagen (Server-Status {status})")
            return False
        except (OSError, subprocess.SubprocessError) as e:
            self.say(f"Trennen fehlgeschlagen: {e}")
            return False
        finally:
            with self._lock:
                self._busy.discard(key)
            self._wake.set()

    # -- servers ----------------------------------------------------------------
    def add_server(self, srv: Server) -> None:
        with self._lock:
            self.settings.servers.append(srv)
            self.settings.save()
        self._wake.set()

    def update_server(self, srv: Server) -> None:
        with self._lock:
            self.status.pop(srv.id, None)
            self.settings.save()
        self._wake.set()

    def remove_server(self, sid: str) -> None:
        with self._lock:
            self.settings.servers = [s for s in self.settings.servers if s.id != sid]
            self.settings.auto = [a for a in self.settings.auto if a[0] != sid]
            self.status.pop(sid, None)
            self.settings.save()
