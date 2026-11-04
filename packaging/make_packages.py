# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Assemble the download packages from built binaries.

    python packaging/make_packages.py --version 0.4.3 --bins <dir> --out <dir>

<dir> must contain (whatever is missing is skipped with a warning):
    usbshare-linux-x86_64           Linux server (+ server window)
    usbshare.exe                    Windows server / command line
    usbshare-server-gui.exe         Windows server window
    usbshare-client.exe             Windows client window

Result:
    usbshare-server-<version>.zip          linux/ + windows/
    usbshare-client-<version>-windows.zip
"""
import argparse
import os
import shutil
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from usbshare.config import ServerConfig  # noqa: E402


def _copy(src, dst, mode=None):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    if mode:
        os.chmod(dst, mode)


def _zip(folder, target):
    base = os.path.dirname(folder)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, _dirs, files in os.walk(folder):
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                info = zipfile.ZipInfo.from_file(full, os.path.relpath(full, base))
                info.compress_type = zipfile.ZIP_DEFLATED
                with open(full, "rb") as fh:
                    z.writestr(info, fh.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--bins", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    work = tempfile.mkdtemp()
    common = [("LICENSE", "LICENSE"), ("NOTICE", "NOTICE"), ("CHANGELOG.md", "CHANGELOG.md")]

    def binary(name):
        p = os.path.join(a.bins, name)
        if not os.path.exists(p):
            print(f"WARNUNG: {name} fehlt – wird übersprungen")
            return None
        return p

    config = ServerConfig().render()

    # server package
    srv = os.path.join(work, "usbshare-server")
    for src, dst in common:
        _copy(os.path.join(ROOT, src), os.path.join(srv, dst))
    _copy(os.path.join(HERE, "server", "LIESMICH.md"), os.path.join(srv, "LIESMICH.md"))
    for f in os.listdir(os.path.join(HERE, "server", "linux")):
        _copy(os.path.join(HERE, "server", "linux", f), os.path.join(srv, "linux", f),
              0o755 if f.endswith(".sh") else None)
    for f in os.listdir(os.path.join(HERE, "server", "windows")):
        _copy(os.path.join(HERE, "server", "windows", f), os.path.join(srv, "windows", f))
    with open(os.path.join(srv, "linux", "usbshare.toml"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(config)
    with open(os.path.join(srv, "windows", "usbshare.toml"), "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(config)
    p = binary("usbshare-linux-x86_64")
    if p:
        _copy(p, os.path.join(srv, "linux", "usbshare"), 0o755)
    for name in ("usbshare.exe", "usbshare-server-gui.exe"):
        p = binary(name)
        if p:
            _copy(p, os.path.join(srv, "windows", name))
    _zip(srv, os.path.join(a.out, f"usbshare-server-{a.version}.zip"))

    # client package (Windows)
    cli = os.path.join(work, "usbshare-client")
    for src, dst in common:
        _copy(os.path.join(ROOT, src), os.path.join(cli, dst))
    _copy(os.path.join(HERE, "client", "LIESMICH.md"), os.path.join(cli, "LIESMICH.md"))
    p = binary("usbshare-client.exe")
    if p:
        _copy(p, os.path.join(cli, "usbshare-client.exe"))
    _zip(cli, os.path.join(a.out, f"usbshare-client-{a.version}-windows.zip"))
    shutil.rmtree(work)
    print("Pakete erstellt in", a.out)


if __name__ == "__main__":
    main()
