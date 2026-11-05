# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Look for new releases on GitHub, download and verify them.

Only contacts GitHub when the user asks for it ("Nach Updates suchen" / `usbshare update`).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import ssl
import sys
import urllib.request
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

from . import __url__, __version__

API_LATEST = "https://api.github.com/repos/Goeste/usbshare/releases/latest"
RELEASES_PAGE = __url__.rstrip("/") + "/releases/latest"
SUMS_NAME = "SHA256SUMS.txt"


class UpdateError(Exception):
    pass


def parse_version(v: str) -> Tuple[int, ...]:
    nums = re.findall(r"\d+", v.split("-")[0].split("+")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(remote: str, local: str = __version__) -> bool:
    return parse_version(remote) > parse_version(local)


@dataclass
class Release:
    version: str
    notes: str
    page: str
    assets: Dict[str, dict] = field(default_factory=dict)   # name -> {url, size, digest}

    def asset(self, kind: str) -> Optional[str]:
        """kind: 'client', 'server' (this platform), 'server-linux' or 'server-windows'."""
        if kind == "server":
            kind = "server-windows" if IS_WIN else "server-linux"
        prefix = PACKAGES[kind]
        for name in self.assets:
            if name.startswith(prefix) and name.endswith(".zip"):
                return name
        return None


IS_WIN = sys.platform.startswith("win")
# Package name prefixes. 'usbshare-server-' is the Linux package (old versions shipped
# Linux + Windows together under that name – their updaters still find it).
PACKAGES = {"client": "usbshare-client-", "server-linux": "usbshare-server-",
            "server-windows": "usbshare-windows-server-"}


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if not sys.platform.startswith("win"):
        # frozen Linux builds may not know where the system certificates are
        for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt",
                     "/etc/ssl/ca-bundle.pem", "/etc/ssl/cert.pem", "/etc/pki/tls/cacert.pem"):
            if os.path.exists(path):
                try:
                    ctx.load_verify_locations(cafile=path)
                    break
                except (OSError, ssl.SSLError):
                    pass
        if not ctx.get_ca_certs():
            try:
                import certifi
                ctx.load_verify_locations(cafile=certifi.where())
            except Exception:
                pass
    return ctx


def _get(url: str, timeout: float = 20.0, accept: str = "application/octet-stream",
         progress: Optional[Callable[[int, int], None]] = None) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": f"usbshare-updater/{__version__}", "Accept": accept})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
            total = int(r.headers.get("Content-Length") or 0)
            buf = io.BytesIO()
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                buf.write(chunk)
                if progress:
                    progress(buf.tell(), total)
            return buf.getvalue()
    except OSError as e:
        raise UpdateError(f"Keine Verbindung zu GitHub: {e}") from e


def latest_release() -> Release:
    try:
        data = json.loads(_get(API_LATEST, accept="application/vnd.github+json").decode())
    except ValueError as e:
        raise UpdateError("Ungültige Antwort von GitHub") from e
    tag = data.get("tag_name") or ""
    if not tag:
        raise UpdateError("Kein Release gefunden")
    assets = {a["name"]: {"url": a["browser_download_url"], "size": a.get("size", 0),
                          "digest": a.get("digest") or ""}
              for a in data.get("assets", [])}
    return Release(tag.lstrip("v"), data.get("body") or "", data.get("html_url") or RELEASES_PAGE,
                   assets)


def _expected_sha256(rel: Release, name: str) -> Optional[str]:
    digest = rel.assets[name].get("digest", "")
    if digest.startswith("sha256:"):
        return digest.split(":", 1)[1].lower()
    if SUMS_NAME in rel.assets:
        sums = _get(rel.assets[SUMS_NAME]["url"]).decode(errors="replace")
        for line in sums.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == name:
                return parts[0].lower()
    return None


def verified_download(url: str, sha256: str, timeout: float = 120,
                      progress: Optional[Callable[[int, int], None]] = None) -> bytes:
    data = _get(url, timeout=timeout, progress=progress)
    if hashlib.sha256(data).hexdigest() != sha256.lower():
        raise UpdateError("Prüfsumme stimmt nicht – Download beschädigt, Update abgebrochen")
    return data


def download_package(rel: Release, kind: str,
                     progress: Optional[Callable[[int, int], None]] = None) -> zipfile.ZipFile:
    """Download and verify the release package; returns the opened zip."""
    name = rel.asset(kind)
    if not name:
        raise UpdateError(f"Das Release {rel.version} enthält kein {kind}-Paket")
    expected = _expected_sha256(rel, name)
    if not expected:
        raise UpdateError("Für das Paket gibt es keine Prüfsumme – Update abgebrochen")
    data = verified_download(rel.assets[name]["url"], expected, progress=progress)
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise UpdateError("Das heruntergeladene Paket ist beschädigt") from e


def extract_member(zf: zipfile.ZipFile, suffix: str) -> bytes:
    for n in zf.namelist():
        if n.replace("\\", "/").endswith(suffix):
            return zf.read(n)
    raise UpdateError(f"{suffix} fehlt im Paket")


def short_notes(notes: str, limit: int = 1800) -> str:
    text = notes.split("**Downloads:**")[0].strip()
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^### ", "", text, flags=re.M)
    text = re.sub(r"\n[ \t]{2,}", " ", text)          # re-join wrapped list items
    return text if len(text) <= limit else text[:limit].rsplit("\n", 1)[0] + "\n…"


def frozen_executable() -> Optional[str]:
    """Path of the running program if it is a built binary (not run from source)."""
    if getattr(sys, "frozen", False):
        return os.path.realpath(sys.executable)
    return None


def clean_environment() -> dict:
    """Environment for starting the *new* program version from the running one.

    A PyInstaller one-file program passes variables to its children so that they reuse its
    unpacked files (_MEI… temp folder). The new version must not do that: the old one deletes
    that folder when it exits ("Can't find a usable tk.tcl")."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("_PYI", "_MEI")) and k not in ("TCL_LIBRARY", "TK_LIBRARY")}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    return env


def cleanup_old_exe(*paths: str) -> None:
    """Remove the '.old' files a Windows self-update leaves behind (running files can be
    renamed but not deleted)."""
    exe = frozen_executable()
    for p in (paths or ((exe,) if exe else ())):
        if p and os.path.exists(p + ".old"):
            try:
                os.remove(p + ".old")
            except OSError:
                pass
