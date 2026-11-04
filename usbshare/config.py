# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Server configuration: load, change at runtime and write back as commented TOML."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import protocol as P


def _toml_load(path: str) -> dict:
    try:
        import tomllib
    except ImportError:  # Python < 3.11
        import tomli as tomllib  # type: ignore
    with open(path, "rb") as f:
        return tomllib.load(f).get("server", {})


def _s(v: str) -> str:
    return json.dumps(v, ensure_ascii=False)       # JSON strings are valid TOML basic strings


def _list(items: List[str]) -> str:
    return "[" + ", ".join(_s(i) for i in items) + "]"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = P.DEFAULT_PORT
    allow_clients: List[str] = field(default_factory=list)
    only: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    usbdk: bool = False
    discovery: bool = True
    reset_on_attach: bool = True
    assign: Dict[str, List[str]] = field(default_factory=dict)
    path: Optional[str] = None

    @classmethod
    def load(cls, path: Optional[str]) -> "ServerConfig":
        cfg = cls(path=path)
        if path and os.path.exists(path):
            d = _toml_load(path)
            cfg.host = d.get("host", cfg.host)
            cfg.port = int(d.get("port", cfg.port))
            cfg.allow_clients = list(d.get("allow_clients", []))
            cfg.only = list(d.get("only", []))
            cfg.exclude = list(d.get("exclude", []))
            cfg.usbdk = bool(d.get("usbdk", False))
            cfg.discovery = bool(d.get("discovery", True))
            cfg.reset_on_attach = bool(d.get("reset_on_attach", True))
            cfg.assign = {k: list(v) if isinstance(v, (list, tuple)) else [v]
                          for k, v in d.get("assign", {}).items()}
        return cfg

    def mtime(self) -> float:
        try:
            return os.path.getmtime(self.path) if self.path else 0.0
        except OSError:
            return 0.0

    def writable(self) -> bool:
        if not self.path:
            return False
        target = self.path if os.path.exists(self.path) else os.path.dirname(self.path) or "."
        return os.access(target, os.W_OK)

    def render(self) -> str:
        assign = "\n".join(f"{_s(k)} = {_list(v)}" for k, v in sorted(self.assign.items()))
        return f"""# usbshare Server-Konfiguration
# Wird auch vom usbshare Server-Fenster geschrieben. Änderungen von Hand werden
# nach wenigen Sekunden automatisch übernommen (Adresse/Port/UsbDk erst nach Neustart).

[server]
host = {_s(self.host)}
port = {self.port}

# Welche Rechner/VMs sich überhaupt verbinden dürfen. Leer = alle im Netz.
# Beispiel: ["192.168.1.50", "192.168.1.0/24"]
allow_clients = {_list(self.allow_clients)}

# Nur diese Geräte freigeben. Leer = alle (außer "exclude").
# Format: "vid:pid", "vid:*" oder Bus-ID wie "1-4". Werte zeigt "usbshare list".
only = {_list(self.only)}

# Diese Geräte nie freigeben, z. B. eigene Tastatur und Maus.
exclude = {_list(self.exclude)}

# Nur Windows: UsbDk statt WinUSB/Zadig verwenden (UsbDk muss installiert sein)
usbdk = {str(self.usbdk).lower()}

# Auf die automatische Serversuche der Clients antworten (UDP-Port wie TCP-Port).
# Es wird nur Clients geantwortet, die laut allow_clients zugelassen sind.
discovery = {str(self.discovery).lower()}

# Gerät beim Verbinden und Trennen zurücksetzen (USB-Reset), damit der Client es in einem
# sauberen Zustand bekommt. Nötig z. B. für Logitech-Unifying/Bolt-Empfänger an Linux.
# Nur ausschalten, wenn ein Gerät den Reset nicht verträgt (Änderung erst nach Neustart).
reset_on_attach = {str(self.reset_on_attach).lower()}

# Geräte fest bestimmten Clients zuweisen: Ein zugewiesenes Gerät wird nur diesen
# Clients angeboten, andere sehen es als "Reserviert".
# Links: Gerät (vid:pid, vid:* oder Bus-ID), rechts: IP(s) bzw. Netz(e) der Clients.
# Beispiel: "046d:c52b" = ["192.168.1.50"]
[server.assign]
{assign}
"""

    def save(self) -> None:
        if not self.path:
            raise OSError("server was started without --config; changes are not saved")
        d = os.path.dirname(self.path)
        if d:
            os.makedirs(d, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.render())
        os.replace(tmp, self.path)
