# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Assemble the download packages from built binaries.

    python packaging/make_packages.py --version 0.7.0 --bins <dir> --out <dir> [--usbip <dir>]

<dir> must contain (whatever is missing is skipped with a warning):
    usbshare-linux-x86_64           Linux server (+ server window)
    usbshare.exe                    Windows server / command line
    usbshare-server-gui.exe         Windows server window
    usbshare-client.exe             Windows client window

--usbip <dir>: usbip-win2 installer (USBip-<v>-x64-Release.exe) and its LICENSE.txt,
bundled with the client.

Result:
    usbshare-server-<version>-linux.zip     Linux server (+ server window)
    usbshare-windows-server-<version>.zip   Windows server + server window
    usbshare-client-<version>-windows.zip   Windows client (+ usbip-win2 installer)
"""
import argparse
import glob
import hashlib
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
    ap.add_argument("--usbip", help="Ordner mit dem usbip-win2-Installationsprogramm")
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

    def package(folder_name, readme):
        folder = os.path.join(work, folder_name)
        for src, dst in common:
            _copy(os.path.join(ROOT, src), os.path.join(folder, dst))
        _copy(readme, os.path.join(folder, "LIESMICH.md"))
        return folder

    def scripts(sub, dst, newline):
        for f in os.listdir(os.path.join(HERE, "server", sub)):
            _copy(os.path.join(HERE, "server", sub, f), os.path.join(dst, f),
                  0o755 if f.endswith(".sh") else None)
        with open(os.path.join(dst, "usbshare.toml"), "w", encoding="utf-8", newline=newline) as fh:
            fh.write(config)

    # Linux server – the folder layout (usbshare-server/linux/usbshare) is what the
    # updater of every version looks for
    srv = package("usbshare-server", os.path.join(HERE, "server", "LIESMICH.md"))
    scripts("linux", os.path.join(srv, "linux"), "\n")
    p = binary("usbshare-linux-x86_64")
    if p:
        _copy(p, os.path.join(srv, "linux", "usbshare"), 0o755)
    _zip(srv, os.path.join(a.out, f"usbshare-server-{a.version}-linux.zip"))

    # Windows server
    win = package("usbshare-windows-server", os.path.join(HERE, "server", "LIESMICH.md"))
    scripts("windows", win, "\r\n")
    for name in ("usbshare.exe", "usbshare-server-gui.exe"):
        p = binary(name)
        if p:
            _copy(p, os.path.join(win, name))
    _zip(win, os.path.join(a.out, f"usbshare-windows-server-{a.version}.zip"))

    # Windows client (+ usbip-win2)
    cli = package("usbshare-client", os.path.join(HERE, "client", "LIESMICH.md"))
    p = binary("usbshare-client.exe")
    if p:
        _copy(p, os.path.join(cli, "usbshare-client.exe"))
    found = sorted(glob.glob(os.path.join(a.usbip, "USBip-*-x64*.exe"))) if a.usbip else []
    if found:
        dst = os.path.join(cli, "usbip-win2")
        lines = []
        for inst in found:
            _copy(inst, os.path.join(dst, os.path.basename(inst)))
            with open(inst, "rb") as fh:
                lines.append(f"{hashlib.sha256(fh.read()).hexdigest()}  {os.path.basename(inst)}")
        with open(os.path.join(dst, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write("\n".join(lines) + "\n")
        lic = os.path.join(a.usbip, "LICENSE.txt")
        if os.path.exists(lic):
            _copy(lic, os.path.join(dst, "LICENSE.txt"))
        else:
            print("WARNUNG: LICENSE.txt von usbip-win2 fehlt")
    else:
        print("WARNUNG: usbip-win2-Installationsprogramm fehlt – Client-Paket ohne Treiber")
    _zip(cli, os.path.join(a.out, f"usbshare-client-{a.version}-windows.zip"))
    shutil.rmtree(work)
    print("Pakete erstellt in", a.out)


if __name__ == "__main__":
    main()
