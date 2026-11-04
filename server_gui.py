# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""PyInstaller entry point for the Windows server window (usbshare-server-gui.exe)."""
import sys

from usbshare.server_gui import main

if __name__ == "__main__":
    sys.exit(main())
