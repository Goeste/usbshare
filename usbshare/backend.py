# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Abstract interface between the network server and a USB implementation."""
from __future__ import annotations

from typing import Callable, List

from .protocol import DeviceInfo, SubmitCmd, UrbResult

CompletionFn = Callable[[int, UrbResult], None]   # (seqnum, result)


class BackendError(Exception):
    """Raised when a device cannot be opened/exported. `status` is an ST_* code."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


class DeviceSession:
    """An exported (claimed) USB device."""

    info: DeviceInfo

    def submit(self, cmd: SubmitCmd, done: CompletionFn) -> None:
        """Start the URB. `done` must be called exactly once, from any thread."""
        raise NotImplementedError

    def cancel(self, seqnum: int) -> None:
        """Best-effort cancel. `done` for that seqnum is still called afterwards."""
        raise NotImplementedError

    def close(self) -> None:
        """Cancel everything outstanding and give the device back to the host OS."""
        raise NotImplementedError


class Backend:
    def list_devices(self, with_names: bool = False, skip=()) -> List[DeviceInfo]:
        """with_names: fill DeviceInfo.description (may open devices; never opens
        busids in `skip`, which are currently exported)."""
        raise NotImplementedError

    def open(self, busid: str) -> DeviceSession:
        raise NotImplementedError

    def shutdown(self) -> None:
        pass
