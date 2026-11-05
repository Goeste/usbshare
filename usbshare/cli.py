# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Command line interface: usbshare serve | list | remote | attach"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from . import __author__, __url__, __version__
from . import protocol as P
from .server import DeviceFilter, UsbIpServer

SPEED_NAMES = {1: "1.5M", 2: "12M", 3: "480M", 5: "5G", 6: "10G"}


def _print_devices(devs, in_use=None) -> None:
    if not devs:
        print("(keine Geräte)")
        return
    in_use = in_use or {}
    print(f"{'BUSID':<12} {'VID:PID':<10} {'SPEED':<6} BESCHREIBUNG")
    for d in devs:
        extra = f"  [benutzt von {in_use[d.busid]}]" if d.busid in in_use else ""
        print(f"{d.busid:<12} {d.vidpid:<10} {SPEED_NAMES.get(d.speed, '?'):<6} "
              f"{d.description}{extra}")


def _assign_rules(cli, cfg):
    rules = {k: list(v) for k, v in cfg.items()}
    for item in cli or []:
        dev, _, clients = item.partition("=")
        if not clients:
            raise SystemExit(f"--assign erwartet GERÄT=CLIENT[,CLIENT], nicht {item!r}")
        rules.setdefault(dev.strip(), []).extend(c.strip() for c in clients.split(","))
    return rules


def _raise_priority() -> None:
    """Audio (isochronous) needs data on time: run the server above normal priority.
    Done before any thread starts, so all threads inherit it. Best effort."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            k = ctypes.windll.kernel32
            k.SetPriorityClass(k.GetCurrentProcess(), 0x00000080)   # HIGH_PRIORITY_CLASS
        else:
            os.nice(-10)
    except Exception as e:
        logging.getLogger("usbshare").debug("could not raise priority: %s", e)


def cmd_serve(a) -> int:
    _raise_priority()
    from .config import ServerConfig
    cfg = ServerConfig.load(a.config)
    if a.host:
        cfg.host = a.host
    if a.port:
        cfg.port = a.port
    if a.only:
        cfg.only = a.only
    if a.exclude:
        cfg.exclude = a.exclude
    if a.allow_client:
        cfg.allow_clients = a.allow_client
    if a.usbdk:
        cfg.usbdk = True
    if a.no_discovery:
        cfg.discovery = False
    cfg.assign = _assign_rules(a.assign, cfg.assign)
    if a.config and not os.path.exists(a.config):
        try:
            cfg.save()          # first start: create a commented config file
        except OSError as e:
            print(f"Konnte {a.config} nicht anlegen: {e}", file=sys.stderr)
    from .libusb_backend import LibusbBackend
    if a.no_reset:
        cfg.reset_on_attach = False
    backend = LibusbBackend(use_usbdk=cfg.usbdk, reset=cfg.reset_on_attach)
    server = UsbIpServer(backend, host=cfg.host, port=cfg.port, config=cfg)
    server.bind()

    def stop(*_):
        server.stop()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    devs = [d for d in backend.list_devices(with_names=True) if server.device_filter.permits(d)]
    print(f"usbshare {__version__} – Server läuft auf {server.host}:{server.port}")
    print("Freigegebene Geräte:")
    _print_devices(devs)
    for rule, flt in server.assignments.rules:
        print(f"Zuweisung: {rule} -> nur {', '.join(str(n) for n in flt.networks)}")
    if not a.config:
        print("Hinweis: ohne --config werden Änderungen aus dem Server-Fenster nicht gespeichert.")
    sys.stdout.flush()
    try:
        server.serve_forever()
    finally:
        backend.shutdown()
    return 0


def cmd_list(a) -> int:
    from .libusb_backend import LibusbBackend
    backend = LibusbBackend(use_usbdk=a.usbdk)
    try:
        _print_devices(backend.list_devices(with_names=True))
    finally:
        backend.shutdown()
    return 0


STATE_DE = {"available": "frei", "in_use": "belegt von {used_by}",
            "reserved": "reserviert für {assigned}", "no_driver": "kein Treiber"}


def cmd_remote(a) -> int:
    from .client import parse_server, remote_devlist_ext
    host, port = parse_server(a.host, a.port)
    data = remote_devlist_ext(host, port)
    if not data["devices"]:
        print("(keine Geräte)")
        return 0
    print(f"{'BUSID':<12} {'VID:PID':<10} {'STATUS':<28} BESCHREIBUNG")
    for d in data["devices"]:
        state = STATE_DE.get(d["state"], d["state"]).format(
            used_by=d.get("used_by"), assigned=", ".join(d.get("assigned_to") or []))
        print(f"{d['busid']:<12} {d['vidpid']:<10} {state:<28} {d.get('description', '')}")
    return 0


def cmd_release(a) -> int:
    from .client import parse_server, remote_release
    host, port = parse_server(a.server, a.port)
    status = remote_release(host, port, a.busid)
    msg = {P.ST_OK: f"{a.busid} freigegeben.", P.ST_NODEV: f"{a.busid} ist nicht belegt.",
           P.ST_NA: "Nicht erlaubt: nur der Client, der das Gerät nutzt, oder der Server-Rechner "
                    "selbst darf freigeben."}.get(status, f"Fehler (Status {status})")
    print(msg)
    return 0 if status == P.ST_OK else 1


def cmd_attach(a) -> int:
    from .client import attach_loop
    return attach_loop(a.servers, a.port, DeviceFilter(allow=a.device or []), watch=a.watch,
                       interval=a.interval)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="usbshare",
                                description="USB-Geräte über das Netzwerk freigeben (USB/IP-kompatibel)")
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("--version", action="version",
                   version=f"usbshare {__version__} – © 2026 {__author__} – GPL-3.0-or-later – {__url__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Server starten (auf dem Rechner mit den USB-Geräten)")
    s.add_argument("--host", help="Bind-Adresse (Standard 0.0.0.0, '::' für IPv6)")
    s.add_argument("--port", type=int, help=f"TCP-Port (Standard {P.DEFAULT_PORT})")
    s.add_argument("--only", action="append", metavar="VID:PID|BUSID",
                   help="nur diese Geräte freigeben (mehrfach, '046d:*' erlaubt)")
    s.add_argument("--exclude", action="append", metavar="VID:PID|BUSID",
                   help="diese Geräte nie freigeben (z. B. eigene Tastatur)")
    s.add_argument("--allow-client", action="append", metavar="CIDR",
                   help="nur Clients aus diesen Netzen zulassen, z. B. 192.168.1.50/32")
    s.add_argument("--assign", action="append", metavar="GERÄT=CLIENT[,CLIENT]",
                   help="Gerät nur bestimmten Clients anbieten, z. B. 046d:c52b=192.168.1.50")
    s.add_argument("--usbdk", action="store_true",
                   help="Windows: UsbDk statt WinUSB verwenden (kein Zadig nötig)")
    s.add_argument("--config", help="TOML-Konfigurationsdatei (wird angelegt, falls nicht vorhanden)")
    s.add_argument("--no-reset", action="store_true",
                   help="Geräte beim Verbinden/Trennen nicht zurücksetzen")
    s.add_argument("--no-discovery", action="store_true",
                   help="nicht auf die automatische Serversuche der Clients antworten")
    s.set_defaults(fn=cmd_serve)

    l = sub.add_parser("list", help="lokale USB-Geräte anzeigen")
    l.add_argument("--usbdk", action="store_true")
    l.set_defaults(fn=cmd_list)

    r = sub.add_parser("remote", help="verfügbare Geräte eines Servers anzeigen")
    r.add_argument("host")
    r.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    r.set_defaults(fn=cmd_remote)

    g = sub.add_parser("gui", help="Server-Fenster öffnen (Geräte freigeben und zuweisen)")
    g.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    g.set_defaults(fn=lambda a: __import__("usbshare.server_gui", fromlist=["main"]).main(
        ["--port", str(a.port)]))

    rl = sub.add_parser("release", help="belegtes Gerät vom Client lösen (auf dem Server-Rechner)")
    rl.add_argument("busid")
    rl.add_argument("--server", default="127.0.0.1", help="Standard: dieser Rechner")
    rl.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    rl.set_defaults(fn=cmd_release)

    t = sub.add_parser("attach", help="in der VM: Geräte vom Server einbinden (nutzt usbip)")
    t.add_argument("servers", nargs="+", metavar="SERVER[:PORT]",
                   help="ein oder mehrere Server, z. B. 192.168.1.20 192.168.1.21:3241")
    t.add_argument("--port", type=int, default=P.DEFAULT_PORT,
                   help="Standard-Port für Server ohne :PORT")
    t.add_argument("--device", action="append", metavar="VID:PID|BUSID",
                   help="nur diese Geräte (Standard: alle)")
    t.add_argument("--watch", action="store_true",
                   help="dauerhaft laufen und Geräte automatisch (wieder) einbinden")
    t.add_argument("--interval", type=float, default=3.0)
    t.set_defaults(fn=cmd_attach)

    a = p.parse_args(argv)
    level = logging.WARNING - 10 * a.verbose if a.cmd != "serve" else logging.INFO - 10 * min(a.verbose, 1)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    return a.fn(a)
