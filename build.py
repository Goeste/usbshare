# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Build a single-file executable for the current OS with PyInstaller.

    pip install pyinstaller libusb1 sv-ttk pillow pystray
    python build.py            ->  dist/usbshare(.exe)            (Server + CLI, Linux: + Server-Fenster)
    python build.py gui        ->  dist/usbshare-client.exe       (Windows-Client mit Fenster)
    python build.py server-gui ->  dist/usbshare-server-gui.exe   (Windows-Server-Fenster)

Windows: the libusb1 wheel ships libusb-1.0.dll and it is bundled automatically.
macOS:   brew install libusb  (the dylib is bundled from Homebrew).
Linux:   uses the system libusb-1.0 (apt install libusb-1.0-0).
"""
import glob
import os
import sys

import PyInstaller.__main__

from usbshare.icons import save_ico

os.makedirs("build", exist_ok=True)
for kind in ("client", "server"):
    save_ico(kind, os.path.join("build", f"{kind}.ico"))

UI = ["--collect-data", "sv_ttk", "--hidden-import", "PIL._tkinter_finder",
      "--hidden-import", "usbshare.update_ui", "--hidden-import", "usbshare.updater"]
if sys.platform.startswith("win"):
    UI += ["--hidden-import", "pystray._win32"]

args = ["usbshare/__main__.py", "--onefile", "--name", "usbshare", "--clean",
        "--collect-all", "usb1", "--hidden-import", "usbshare.server_gui",
        "--icon", "build/server.ico"] + UI

if sys.platform == "darwin":
    for pattern in ("/opt/homebrew/lib/libusb-1.0*.dylib", "/usr/local/lib/libusb-1.0*.dylib"):
        for lib in glob.glob(pattern):
            args += ["--add-binary", f"{lib}{os.pathsep}."]
            break

if "gui" in sys.argv[1:]:
    # Windows client window: no console, asks for admin rights (usbip-win2 needs them)
    args = ["client_gui.py", "--onefile", "--windowed", "--uac-admin", "--name", "usbshare-client",
            "--clean", "--exclude-module", "usb1", "--icon", "build/client.ico"] + UI

if "server-gui" in sys.argv[1:]:
    # Windows server window (talks to the running service, no admin rights needed)
    args = ["server_gui.py", "--onefile", "--windowed", "--name", "usbshare-server-gui",
            "--clean", "--exclude-module", "usb1", "--icon", "build/server.ico"] + UI

PyInstaller.__main__.run(args)
