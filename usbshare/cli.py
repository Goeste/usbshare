# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Command line interface: usbshare serve | list | remote | attach"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
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
    if a.audio_buffer is not None:
        cfg.audio_buffer_ms = max(0, min(500, a.audio_buffer))
    backend = LibusbBackend(use_usbdk=cfg.usbdk, reset=cfg.reset_on_attach,
                            audio_buffer_ms=cfg.audio_buffer_ms)
    server = UsbIpServer(backend, host=cfg.host, port=cfg.port, config=cfg)
    try:
        server.bind()
    except OSError as e:
        backend.shutdown()
        print(f"Fehler: Port {cfg.port} kann nicht geöffnet werden ({e}). Läuft usbshare "
              "schon?", file=sys.stderr)
        return 1

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
        server.close_sessions(timeout=3.0)
        t = threading.Thread(target=backend.shutdown, daemon=True)
        t.start()
        t.join(timeout=3.0)            # never hang on exit (systemd would have to kill us)
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


def cmd_update(a) -> int:
    """Update the installed server binary from the latest GitHub release (Linux)."""
    import shutil
    import subprocess
    import tempfile
    from . import updater as U
    print(f"Installiert: usbshare {__version__}")
    try:
        rel = U.latest_release()
    except U.UpdateError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    print(f"Neueste Version: {rel.version}")
    if not U.is_newer(rel.version) and not a.force:
        print("usbshare ist aktuell.")
        return 0
    print()
    print(U.short_notes(rel.notes))
    print()
    if a.check:
        print(f"Update verfügbar: {rel.version} – installieren mit: sudo usbshare update")
        return 10
    if sys.platform.startswith("win"):
        print(f"Unter Windows bitte das Paket herunterladen: {rel.page}")
        return 1
    target = U.frozen_executable()
    if not target:
        print("Diese usbshare-Version läuft aus dem Quellcode – bitte mit git aktualisieren.")
        return 1
    if os.geteuid() != 0:
        print("Für das Update werden Root-Rechte gebraucht: sudo usbshare update", file=sys.stderr)
        return 1
    if not a.yes:
        try:
            if input(f"Auf {rel.version} aktualisieren? [J/n] ").strip().lower() not in ("", "j", "ja", "y", "yes"):
                print("Abgebrochen.")
                return 1
        except EOFError:
            print("Keine Eingabe möglich – mit --yes bestätigen.", file=sys.stderr)
            return 1

    shown = [-1]

    def progress(done, total):
        pct = done * 100 // total if total else -1
        if pct != shown[0] and (pct % 5 == 0 or pct == 100):
            shown[0] = pct
            print(f"\rLade herunter … {pct:3d} %", end="", flush=True)
    try:
        zf = U.download_package(rel, "server", progress)
        print("\rDownload geprüft (SHA-256 stimmt).      ")
        binary = U.extract_member(zf, "/linux/usbshare")
    except U.UpdateError as e:
        print(f"\nFehler: {e}", file=sys.stderr)
        return 2
    new = target + ".new"
    with open(new, "wb") as f:
        f.write(binary)
    os.chmod(new, 0o755)
    try:
        out = subprocess.run([new, "--version"], capture_output=True, text=True, timeout=60)
        if rel.version not in (out.stdout + out.stderr):
            raise RuntimeError((out.stdout + out.stderr).strip() or f"Exit-Code {out.returncode}")
    except Exception as e:
        os.unlink(new)
        print(f"Die neue Version startet auf diesem System nicht ({e}) – Update abgebrochen, "
              "die bisherige Version bleibt.", file=sys.stderr)
        return 2
    os.replace(new, target)                     # atomic; the running process keeps the old file
    print(f"{target} aktualisiert auf {rel.version}.")
    # service file shipped with the package (only if it is installed and changed)
    unit = "/etc/systemd/system/usbshare.service"
    try:
        new_unit = U.extract_member(zf, "/linux/usbshare.service")
        if os.path.exists(unit) and open(unit, "rb").read() != new_unit:
            with tempfile.NamedTemporaryFile(delete=False, dir="/etc/systemd/system") as t:
                t.write(new_unit)
            shutil.move(t.name, unit)
            os.chmod(unit, 0o644)
            subprocess.run(["systemctl", "daemon-reload"], check=False)
            print("Dienst-Datei aktualisiert.")
    except (U.UpdateError, OSError):
        pass
    if shutil.which("systemctl") and subprocess.run(
            ["systemctl", "is-enabled", "--quiet", "usbshare"], check=False).returncode == 0:
        subprocess.run(["systemctl", "restart", "usbshare"], check=False)
        print("Dienst usbshare neu gestartet. Verbundene Geräte werden kurz getrennt.")
    print("Fertig.")
    return 0


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
    s.add_argument("--audio-buffer", type=int, metavar="MS",
                   help="Puffer für Audio-Wiedergabe in ms (Standard 40, 0 = aus)")
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

    up = sub.add_parser("update", help="auf die neueste Version aktualisieren (Linux-Server)")
    up.add_argument("--check", action="store_true", help="nur prüfen, ob es ein Update gibt")
    up.add_argument("--yes", "-y", action="store_true", help="ohne Rückfrage installieren")
    up.add_argument("--force", action="store_true", help="auch installieren, wenn schon aktuell")
    up.set_defaults(fn=cmd_update)

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
