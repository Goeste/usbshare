# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""usbip-win2 (the Windows USB/IP driver) for the client: find, install, update.

The client package ships the usbip-win2 installer (folder ``usbip-win2`` next to
usbshare-client.exe). If it is missing, or GitHub has a newer version, the installer is
downloaded from the usbip-win2 releases and checked against its SHA-256 from GitHub. The
installer runs silently; Windows must be restarted afterwards.

usbip-win2: https://github.com/vadimgrn/usbip-win2 – © Vadym Hrynchyshyn, BSD 2-Clause.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable, List, Optional

from . import updater as U
from .i18n import _
from .client import find_usbip_tool

API_LATEST = "https://api.github.com/repos/vadimgrn/usbip-win2/releases/latest"
RELEASES_PAGE = "https://github.com/vadimgrn/usbip-win2/releases"
APP_GUID = "{199505b0-b93d-4521-a8c7-897818e0205a}"          # AppId in usbip-win2's setup.iss
BUNDLE_DIR = "usbip-win2"
NAME_RE = re.compile(r"^USBip-(\d+(?:\.\d+)+)-x64(?:-Release)?\.exe$", re.I)
# Inno Setup: no wizard, no message boxes, no automatic reboot (we ask the user)
SILENT = ["/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-", "/TASKS=vcredist"]
CREATE_NO_WINDOW = 0x08000000


@dataclass
class Installer:
    version: str
    source: str                  # "bundled" or "github"
    path: str = ""               # bundled: local file
    url: str = ""                # github: download URL
    sha256: str = ""
    size: int = 0


def installed_version() -> Optional[str]:
    """Installed usbip-win2 version, '' if installed but the version is unknown, None if
    not installed."""
    if sys.platform.startswith("win"):
        import winreg
        key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{APP_GUID}_is1"
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0,
                                    winreg.KEY_READ | view) as k:
                    return str(winreg.QueryValueEx(k, "DisplayVersion")[0])
            except OSError:
                pass
    return "" if find_usbip_tool() else None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundled(folder: Optional[str] = None) -> Optional[Installer]:
    """The installer shipped in the client package (newest one if there are several)."""
    if folder is None:
        exe = U.frozen_executable()
        if not exe:
            return None
        folder = os.path.join(os.path.dirname(exe), BUNDLE_DIR)
    found: List[Installer] = []
    for p in glob.glob(os.path.join(folder, "*.exe")):
        m = NAME_RE.match(os.path.basename(p))
        if m:
            found.append(Installer(m.group(1), "bundled", path=p, size=os.path.getsize(p)))
    if not found:
        return None
    best = max(found, key=lambda i: U.parse_version(i.version))
    sums = os.path.join(folder, "SHA256SUMS.txt")       # written when the package is built
    if os.path.exists(sums):
        for line in open(sums, encoding="utf-8", errors="replace"):
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == os.path.basename(best.path):
                best.sha256 = parts[0].lower()
    return best


def latest_online() -> Optional[Installer]:
    """Newest x64 installer on GitHub (None if it cannot be checked – offline is fine)."""
    try:
        data = json.loads(U._get(API_LATEST, timeout=10, accept="application/vnd.github+json"))
    except (U.UpdateError, ValueError):
        return None
    for a in data.get("assets", []):
        m = NAME_RE.match(a.get("name", ""))
        digest = a.get("digest") or ""
        if m and digest.startswith("sha256:"):
            return Installer(m.group(1), "github", url=a["browser_download_url"],
                             sha256=digest.split(":", 1)[1].lower(), size=a.get("size", 0))
    return None


def choose(installed: Optional[str], local: Optional[Installer],
           online: Optional[Installer]) -> Optional[Installer]:
    """What to install: the newest available version, preferring the bundled file when it
    is just as new. None if the installed version is already up to date."""
    cands = [i for i in (local, online) if i]
    if not cands:
        return None
    best = max(cands, key=lambda i: (U.parse_version(i.version), i.source == "bundled"))
    if installed is None or (installed and U.is_newer(best.version, installed)):
        return best
    return None


def fetch(inst: Installer, progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Local path of the verified installer (downloads it if needed)."""
    if inst.source == "bundled":
        if inst.sha256 and _sha256_file(inst.path) != inst.sha256:
            raise U.UpdateError(_("Das mitgelieferte usbip-win2-Installationsprogramm ist "
                                  "beschädigt – bitte das Client-Paket neu herunterladen."))
        return inst.path
    data = U.verified_download(inst.url, inst.sha256, timeout=300, progress=progress)
    path = os.path.join(tempfile.mkdtemp(prefix="usbshare-"), f"USBip-{inst.version}-x64.exe")
    with open(path, "wb") as f:
        f.write(data)
    return path


def run_installer(path: str) -> None:
    """Install silently. The client already runs with administrator rights."""
    log = os.path.join(tempfile.gettempdir(), "usbshare-usbip-win2-setup.log")
    try:
        r = subprocess.run([path, *SILENT, f"/LOG={log}"], creationflags=CREATE_NO_WINDOW
                           if sys.platform.startswith("win") else 0)
    except OSError as e:
        raise U.UpdateError(_("Installationsprogramm konnte nicht gestartet werden: {error}", error=e)) from e
    if r.returncode != 0:
        # Inno Setup exit codes: 2 = cancelled before install, 5 = cancelled during install,
        # 7 = preparation failed (e.g. old version could not be removed)
        raise U.UpdateError(_("Das Setup von usbip-win2 endete mit Code {code}. Protokoll: {log}",
                              code=r.returncode, log=log))


def reboot() -> None:
    subprocess.Popen(["shutdown", "/r", "/t", "0"], creationflags=CREATE_NO_WINDOW
                     if sys.platform.startswith("win") else 0)
