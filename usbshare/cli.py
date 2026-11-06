# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Command line interface: usbshare serve | list | remote | gui | update | release"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import threading
import sys

from . import __author__, __url__, __version__
from . import protocol as P
from .i18n import _
from .server import UsbIpServer

IS_WIN = sys.platform.startswith("win")

SPEED_NAMES = {1: "1.5M", 2: "12M", 3: "480M", 5: "5G", 6: "10G"}


def _print_devices(devs, in_use=None) -> None:
    if not devs:
        print(_("(keine Geräte)"))
        return
    in_use = in_use or {}
    print(f"{'BUSID':<12} {'VID:PID':<10} {'SPEED':<6} " + _("BESCHREIBUNG"))
    for d in devs:
        extra = "  [" + _("benutzt von {client}", client=in_use[d.busid]) + "]" if d.busid in in_use else ""
        print(f"{d.busid:<12} {d.vidpid:<10} {SPEED_NAMES.get(d.speed, '?'):<6} "
              f"{d.description}{extra}")


def _assign_rules(cli, cfg):
    rules = {k: list(v) for k, v in cfg.items()}
    for item in cli or []:
        dev, _sep, clients = item.partition("=")
        if not clients:
            raise SystemExit(_("--assign erwartet GERÄT=CLIENT[,CLIENT], nicht {value}", value=repr(item)))
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
    if IS_WIN:
        from .updater import cleanup_old_exe
        cleanup_old_exe()                  # left over from a self-update
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
            print(_("Konnte {file} nicht anlegen: {error}", file=a.config, error=e), file=sys.stderr)
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
        print(_("Fehler: Port {port} kann nicht geöffnet werden ({error}). Läuft usbshare schon?",
                port=cfg.port, error=e), file=sys.stderr)
        return 1

    def stop(*_):
        server.stop()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    devs = [d for d in backend.list_devices(with_names=True) if server.device_filter.permits(d)]
    print(_("usbshare {v} – Server läuft auf {host}:{port}", v=__version__, host=server.host,
            port=server.port))
    print(_("Freigegebene Geräte:"))
    _print_devices(devs)
    for rule, flt in server.assignments.rules:
        print(_("Zuweisung: {device} -> nur {clients}", device=rule,
                clients=", ".join(str(n) for n in flt.networks)))
    if not a.config:
        print(_("Hinweis: ohne --config werden Änderungen aus dem Server-Fenster nicht gespeichert."))
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


STATE_TEXT = {"available": _("frei"), "in_use": _("belegt von {used_by}"),
              "reserved": _("reserviert für {assigned}"), "no_driver": _("kein Treiber")}


def cmd_remote(a) -> int:
    from .client import parse_server, remote_devlist_ext
    host, port = parse_server(a.host, a.port)
    data = remote_devlist_ext(host, port)
    if not data["devices"]:
        print(_("(keine Geräte)"))
        return 0
    print(f"{'BUSID':<12} {'VID:PID':<10} {'STATUS':<28} " + _("BESCHREIBUNG"))
    for d in data["devices"]:
        state = STATE_TEXT.get(d["state"], d["state"]).format(
            used_by=d.get("used_by"), assigned=", ".join(d.get("assigned_to") or []))
        print(f"{d['busid']:<12} {d['vidpid']:<10} {state:<28} {d.get('description', '')}")
    return 0


def cmd_release(a) -> int:
    from .client import parse_server, remote_release
    host, port = parse_server(a.server, a.port)
    status = remote_release(host, port, a.busid)
    msg = {P.ST_OK: _("{busid} freigegeben.", busid=a.busid),
           P.ST_NODEV: _("{busid} ist nicht belegt.", busid=a.busid),
           P.ST_NA: _("Nicht erlaubt: nur der Client, der das Gerät nutzt, oder der Server-Rechner "
                      "selbst darf freigeben.")}.get(status, _("Fehler (Status {status})", status=status))
    print(msg)
    return 0 if status == P.ST_OK else 1


def cmd_gui(a) -> int:
    try:
        from .server_gui import main
    except ImportError:
        # Windows: the console server is built without Tk, the window is its own program
        import subprocess
        from .updater import frozen_executable
        exe = frozen_executable()
        gui = os.path.join(os.path.dirname(exe), "usbshare-server-gui.exe") if exe else ""
        if not os.path.exists(gui):
            print(_("Das Server-Fenster ist nicht enthalten (usbshare-server-gui.exe fehlt)."))
            return 2
        subprocess.Popen([gui, "--port", str(a.port)], close_fds=True)
        return 0
    return main(["--port", str(a.port)])


def _is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _starts(path: str, version: str) -> bool:
    import subprocess
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60,
                             creationflags=0x08000000 if IS_WIN else 0)       # CREATE_NO_WINDOW
        return version in out.stdout + out.stderr
    except (OSError, subprocess.SubprocessError):
        return False


def _update_windows(rel, zf, target: str) -> int:
    """Windows server: replace usbshare.exe and usbshare-server-gui.exe next to it, then
    restart the 'usbshare' scheduled task. Running programs can be renamed (not deleted), so
    the old files become *.old and are removed on the next start."""
    import subprocess
    from . import updater as U
    folder = os.path.dirname(target)
    files = {"usbshare.exe": U.extract_member(zf, "/usbshare.exe")}
    try:
        files["usbshare-server-gui.exe"] = U.extract_member(zf, "/usbshare-server-gui.exe")
    except U.UpdateError:
        pass
    test = os.path.join(folder, "usbshare-new.exe")
    with open(test, "wb") as f:
        f.write(files["usbshare.exe"])
    if not _starts(test, rel.version):
        os.remove(test)
        print(_("Die neue Version startet auf diesem System nicht – Update abgebrochen, die "
                "bisherige Version bleibt."), file=sys.stderr)
        return 2
    os.remove(test)
    hide = {"creationflags": 0x08000000, "capture_output": True}
    task = subprocess.run(["schtasks", "/Query", "/TN", "usbshare"], **hide).returncode == 0
    if task:
        subprocess.run(["schtasks", "/End", "/TN", "usbshare"], **hide)
    # the server itself (one-file programs run as two processes – spare our own pair)
    subprocess.run(["taskkill", "/F", "/FI", "IMAGENAME eq usbshare.exe",
                    "/FI", f"PID ne {os.getpid()}", "/FI", f"PID ne {os.getppid()}"], **hide)
    replaced = []
    try:
        for name, data in files.items():
            path = os.path.join(folder, name)
            U.cleanup_old_exe(path)
            if os.path.exists(path):
                os.replace(path, path + ".old")
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
            replaced.append(path)
    except OSError as e:
        for path in replaced:                  # put the old version back
            try:
                os.replace(path + ".old", path)
            except OSError:
                pass
        print(_("Programmdatei konnte nicht ersetzt werden: {error}", error=e), file=sys.stderr)
        if task:
            subprocess.run(["schtasks", "/Run", "/TN", "usbshare"], **hide)
        return 2
    print(_("{path} aktualisiert auf {v}.", path=folder, v=rel.version))
    if task:
        subprocess.run(["schtasks", "/Run", "/TN", "usbshare"], **hide)
        print(_("Dienst usbshare neu gestartet. Verbundene Geräte werden kurz getrennt."))
    print(_("Fertig."))
    return 0


def cmd_update(a) -> int:
    """Update the installed server from the latest GitHub release (Linux and Windows)."""
    if getattr(a, "report", None):          # elevated run from the server window: no console
        sys.stdout = sys.stderr = open(a.report, "w", encoding="utf-8", buffering=1)
    import shutil
    import subprocess
    import tempfile
    from . import updater as U
    print(_("Installiert: usbshare {v}", v=__version__))
    try:
        rel = U.latest_release()
    except U.UpdateError as e:
        print(_("Fehler: {error}", error=e), file=sys.stderr)
        return 2
    print(_("Neueste Version: {v}", v=rel.version))
    if not U.is_newer(rel.version) and not a.force:
        print(_("usbshare ist aktuell."))
        return 0
    print()
    print(U.short_notes(rel.notes))
    print()
    if a.check:
        print(_("Update verfügbar: {v} – installieren mit: {command}", v=rel.version,
                command=_("usbshare update (als Administrator)") if IS_WIN else "sudo usbshare update"))
        return 10
    target = U.frozen_executable()
    if not target:
        print(_("Diese usbshare-Version läuft aus dem Quellcode – bitte mit git aktualisieren."))
        return 1
    if IS_WIN:
        if not _is_admin():
            print(_("Für das Update werden Administratorrechte gebraucht "
                    "(Eingabeaufforderung „Als Administrator ausführen“)."), file=sys.stderr)
            return 1
    elif os.geteuid() != 0:
        print(_("Für das Update werden Root-Rechte gebraucht:") + " sudo usbshare update", file=sys.stderr)
        return 1
    if not a.yes:
        try:
            answer = input(_("Auf {v} aktualisieren? [J/n] ", v=rel.version)).strip().lower()
            if answer not in ("", "j", "ja", "y", "yes"):
                print(_("Abgebrochen."))
                return 1
        except EOFError:
            print(_("Keine Eingabe möglich – mit --yes bestätigen."), file=sys.stderr)
            return 1

    shown = [-1]

    def progress(done, total):
        pct = done * 100 // total if total else -1
        if pct != shown[0] and (pct % 5 == 0 or pct == 100):
            shown[0] = pct
            print("\r" + _("Lade herunter … {pct} %", pct=f"{pct:3d}"), end="", flush=True)
    try:
        zf = U.download_package(rel, "server", progress)
        print("\r" + _("Download geprüft (SHA-256 stimmt).") + "      ")
        if IS_WIN:
            return _update_windows(rel, zf, target)
        binary = U.extract_member(zf, "/linux/usbshare")
    except U.UpdateError as e:
        print("\n" + _("Fehler: {error}", error=e), file=sys.stderr)
        return 2
    new = target + ".new"
    with open(new, "wb") as f:
        f.write(binary)
    os.chmod(new, 0o755)
    if not _starts(new, rel.version):
        os.unlink(new)
        print(_("Die neue Version startet auf diesem System nicht – Update abgebrochen, die "
                "bisherige Version bleibt."), file=sys.stderr)
        return 2
    os.replace(new, target)                     # atomic; the running process keeps the old file
    print(_("{path} aktualisiert auf {v}.", path=target, v=rel.version))
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
            print(_("Dienst-Datei aktualisiert."))
    except (U.UpdateError, OSError):
        pass
    if shutil.which("systemctl") and subprocess.run(
            ["systemctl", "is-enabled", "--quiet", "usbshare"], check=False).returncode == 0:
        subprocess.run(["systemctl", "restart", "usbshare"], check=False)
        print(_("Dienst usbshare neu gestartet. Verbundene Geräte werden kurz getrennt."))
    print(_("Fertig."))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="usbshare",
                                description=_("USB-Geräte über das Netzwerk freigeben (USB/IP-kompatibel)"))
    p.add_argument("-v", "--verbose", action="count", default=0)
    p.add_argument("--version", action="version",
                   version=f"usbshare {__version__} – © 2026 {__author__} – GPL-3.0-or-later – {__url__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help=_("Server starten (auf dem Rechner mit den USB-Geräten)"))
    s.add_argument("--host", help=_("Bind-Adresse (Standard 0.0.0.0, '::' für IPv6)"))
    s.add_argument("--port", type=int, help=_("TCP-Port (Standard {port})", port=P.DEFAULT_PORT))
    s.add_argument("--only", action="append", metavar="VID:PID|BUSID",
                   help=_("nur diese Geräte freigeben (mehrfach, '046d:*' erlaubt)"))
    s.add_argument("--exclude", action="append", metavar="VID:PID|BUSID",
                   help=_("diese Geräte nie freigeben (z. B. eigene Tastatur)"))
    s.add_argument("--allow-client", action="append", metavar="CIDR",
                   help=_("nur Clients aus diesen Netzen zulassen, z. B. 192.168.1.50/32"))
    s.add_argument("--assign", action="append", metavar=_("GERÄT") + "=CLIENT[,CLIENT]",
                   help=_("Gerät nur bestimmten Clients anbieten, z. B. 046d:c52b=192.168.1.50"))
    s.add_argument("--usbdk", action="store_true",
                   help=_("Windows: UsbDk statt WinUSB verwenden (kein Zadig nötig)"))
    s.add_argument("--config", help=_("TOML-Konfigurationsdatei (wird angelegt, falls nicht vorhanden)"))
    s.add_argument("--audio-buffer", type=int, metavar="MS",
                   help=_("Puffer für Audio-Wiedergabe in ms (Standard 40, 0 = aus)"))
    s.add_argument("--no-reset", action="store_true",
                   help=_("Geräte beim Verbinden/Trennen nicht zurücksetzen"))
    s.add_argument("--no-discovery", action="store_true",
                   help=_("nicht auf die automatische Serversuche der Clients antworten"))
    s.set_defaults(fn=cmd_serve)

    l = sub.add_parser("list", help=_("lokale USB-Geräte anzeigen"))
    l.add_argument("--usbdk", action="store_true")
    l.set_defaults(fn=cmd_list)

    r = sub.add_parser("remote", help=_("verfügbare Geräte eines Servers anzeigen"))
    r.add_argument("host")
    r.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    r.set_defaults(fn=cmd_remote)

    g = sub.add_parser("gui", help=_("Server-Fenster öffnen (Geräte freigeben und zuweisen)"))
    g.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    g.set_defaults(fn=cmd_gui)

    up = sub.add_parser("update", help=_("auf die neueste Version aktualisieren"))
    up.add_argument("--check", action="store_true", help=_("nur prüfen, ob es ein Update gibt"))
    up.add_argument("--yes", "-y", action="store_true", help=_("ohne Rückfrage installieren"))
    up.add_argument("--force", action="store_true", help=_("auch installieren, wenn schon aktuell"))
    up.add_argument("--report", metavar="DATEI", help=argparse.SUPPRESS)
    up.set_defaults(fn=cmd_update)

    rl = sub.add_parser("release", help=_("belegtes Gerät vom Client lösen (auf dem Server-Rechner)"))
    rl.add_argument("busid")
    rl.add_argument("--server", default="127.0.0.1", help=_("Standard: dieser Rechner"))
    rl.add_argument("--port", type=int, default=P.DEFAULT_PORT)
    rl.set_defaults(fn=cmd_release)

    a = p.parse_args(argv)
    level = logging.WARNING - 10 * a.verbose if a.cmd != "serve" else logging.INFO - 10 * min(a.verbose, 1)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(message)s")
    return a.fn(a)
