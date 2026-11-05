# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Build a single-file executable for the current OS with PyInstaller.

    pip install pyinstaller libusb1 sv-ttk pillow
    python build.py            ->  dist/usbshare(.exe)            (Server + CLI; Linux: + Server-Fenster)
    python build.py gui        ->  dist/usbshare-client.exe       (Windows-Client mit Fenster)
    python build.py server-gui ->  dist/usbshare-server-gui.exe   (Windows-Server-Fenster)

Pillow is only needed at build time (program icons); the programs themselves use the
pre-rendered images in usbshare/assets and the native Windows tray.

Windows: the libusb1 wheel ships libusb-1.0.dll and it is bundled automatically.
macOS:   brew install libusb  (the dylib is bundled from Homebrew).
Linux:   uses the system libusb-1.0 (apt install libusb-1.0-0).
"""
import glob
import os
import sys

import PyInstaller.__main__

from usbshare.icons import save_ico, write_assets

IS_WIN = sys.platform.startswith("win")
os.makedirs("build", exist_ok=True)
for kind in ("client", "server"):
    save_ico(kind, os.path.join("build", f"{kind}.ico"))
write_assets(os.path.join("usbshare", "assets"))        # keep the shipped images in sync

# Standard library parts usbshare never uses – leaving them out keeps the programs small.
# (email/http/ssl stay: urllib needs them for the update check.)
UNUSED = ["unittest", "pydoc", "doctest", "pdb", "xmlrpc", "xml", "lzma", "bz2", "sqlite3",
          "multiprocessing", "asyncio", "concurrent", "curses", "readline", "lib2to3",
          "pydoc_data", "test", "tkinter.test", "idlelib", "turtle", "turtledemo",
          "distutils", "setuptools", "pkg_resources", "ftplib", "imaplib", "poplib",
          "smtplib", "nntplib", "telnetlib", "mailbox", "cgi", "wsgiref", "decimal",
          "PIL", "pystray", "darkdetect"] + (["certifi"] if IS_WIN else [])
EXCLUDE = [x for m in UNUSED for x in ("--exclude-module", m)]
NO_TK = ["--exclude-module", "tkinter", "--exclude-module", "_tkinter", "--exclude-module", "sv_ttk"]
COMMON = ["--onefile", "--clean", "--noupx"] + EXCLUDE
UI = ["--collect-data", "sv_ttk", "--add-data", f"usbshare/assets{os.pathsep}usbshare/assets",
      "--hidden-import", "usbshare.update_ui", "--hidden-import", "usbshare.updater"]
GUI_ONLY = ["--exclude-module", "usb1", "--exclude-module", "usbshare.libusb_backend",
            "--exclude-module", "usbshare.iso_buffer"]

if "gui" in sys.argv[1:]:
    # Windows client window: no console, asks for admin rights (usbip-win2 needs them)
    args = ["client_gui.py", "--windowed", "--uac-admin", "--name", "usbshare-client",
            "--icon", "build/client.ico", "--hidden-import", "usbshare.driver"] + COMMON + UI + GUI_ONLY
elif "server-gui" in sys.argv[1:]:
    # Windows server window (talks to the running service, no admin rights needed)
    args = ["server_gui.py", "--windowed", "--name", "usbshare-server-gui",
            "--icon", "build/server.ico"] + COMMON + UI + GUI_ONLY
else:
    args = ["usbshare/__main__.py", "--name", "usbshare", "--collect-all", "usb1",
            "--icon", "build/server.ico"] + COMMON
    if IS_WIN:
        # Windows: console server only – the window is usbshare-server-gui.exe
        args += NO_TK + ["--exclude-module", "usbshare.server_gui", "--exclude-module",
                         "usbshare.gui", "--exclude-module", "usbshare.update_ui"]
    else:
        args += UI + ["--hidden-import", "usbshare.server_gui"]
    if sys.platform == "darwin":
        for pattern in ("/opt/homebrew/lib/libusb-1.0*.dylib", "/usr/local/lib/libusb-1.0*.dylib"):
            for lib in glob.glob(pattern):
                args += ["--add-binary", f"{lib}{os.pathsep}."]
                break

PyInstaller.__main__.run(args)
