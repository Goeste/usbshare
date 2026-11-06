# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Jitter buffer for isochronous OUT streams (USB audio playback).

Without it, every isochronous URB from the client is sent to the device on its own. When a
URB arrives late over the network, the device's endpoint simply gets no packets for a while.
Many devices (especially wireless headset dongles) then mute or resynchronise – the user
hears dropouts or nothing at all.

The stream keeps the device fed continuously: a fixed number of transfers is always queued
on the endpoint ("device queue", = buffer time). Audio packets from the client go into a FIFO
and are handed to the device as transfers complete; when the FIFO is empty, silence is
inserted instead of leaving a gap. A client URB is completed when its last packet has been
handed to the device queue, so the client stays paced by the device's clock and the latency
stays bounded (buffer time + the client's own queue).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import Counter, deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

import usb1

from . import protocol as P
from .i18n import _
from .protocol import IsoPacket, SubmitCmd, UrbResult

log = logging.getLogger("usbshare.iso")
CompletionFn = Callable[[int, UrbResult], None]


class _Owner:
    __slots__ = ("cmd", "done", "remaining", "finished")

    def __init__(self, cmd: SubmitCmd, done: CompletionFn):
        self.cmd, self.done = cmd, done
        self.remaining = len(cmd.iso_packets or [])
        self.finished = False

    def result(self, status: int = 0) -> UrbResult:
        iso = self.cmd.iso_packets or []
        if status:
            pk = [IsoPacket(p.offset, p.length, 0, status) for p in iso]
            return UrbResult(status, 0, b"", pk, self.cmd.start_frame, len(pk))
        pk = [IsoPacket(p.offset, p.length, p.length, 0) for p in iso]
        return UrbResult(0, sum(p.length for p in iso), b"", pk, self.cmd.start_frame, 0)


class IsoOutStream:
    def __init__(self, handle, addr: int, packet_size: int, packets_per_transfer: int,
                 depth: int, name: str = ""):
        self.handle, self.addr = handle, addr
        self.packet_size = max(1, packet_size)
        self.ppt, self.depth = packets_per_transfer, depth
        self.name = name or f"0x{addr:02x}"
        self._lock = threading.Lock()
        self._fifo: Deque[Tuple[bytes, Optional[_Owner]]] = deque()
        self._owners: Dict[int, _Owner] = {}
        self._inflight: set = set()
        self._running = False
        self._stopping = False
        self._in_underrun = False
        self.max_fifo = packets_per_transfer * depth * 8
        # statistics
        self.urbs = 0
        self.packets_played = 0
        self.silence_packets = 0
        self.underruns = 0
        self.dropped_packets = 0
        self.restarts = 0
        self.device_errors: Counter = Counter()

    # -- client side ----------------------------------------------------------------------
    def feed(self, cmd: SubmitCmd, done: CompletionFn) -> None:
        owner = _Owner(cmd, done)
        finished: List[_Owner] = []
        start = False
        with self._lock:
            if self._stopping:
                finished_status = -P.ESHUTDOWN
            else:
                finished_status = 0
                self.urbs += 1
                self._owners[cmd.seqnum] = owner
                for p in cmd.iso_packets or []:
                    self._fifo.append((bytes(cmd.data[p.offset:p.offset + p.length]), owner))
                if owner.remaining == 0:
                    finished.append(owner)
                while len(self._fifo) > self.max_fifo:      # client faster than the device
                    _data, o = self._fifo.popleft()
                    self.dropped_packets += 1
                    if o is not None:
                        o.remaining -= 1
                        if o.remaining == 0:
                            finished.append(o)
                if not self._running:          # start right away (a little silence at
                    self._running = start = True   # the very beginning is harmless)
        if finished_status:
            done(cmd.seqnum, owner.result(finished_status))
            return
        self._finish(finished)
        if start:
            for _i in range(self.depth):
                if not self._submit_one():
                    break

    def cancel(self, seqnum: int) -> bool:
        """Client unlinked a URB. Returns False if this stream does not know it."""
        with self._lock:
            owner = self._owners.get(seqnum)
            if owner is None or owner.finished:
                return owner is not None
            self._fifo = deque(x for x in self._fifo if x[1] is not owner)
        self._finish([owner], -P.ECONNRESET)
        return True

    # -- device side ----------------------------------------------------------------------
    def _submit_one(self) -> bool:
        handed: List[_Owner] = []
        with self._lock:
            if self._stopping:
                return False
            packets: List[bytes] = []
            silent = 0
            for _i in range(self.ppt):
                if self._fifo:
                    data, owner = self._fifo.popleft()
                    if owner is not None:
                        owner.remaining -= 1
                        if owner.remaining == 0:
                            handed.append(owner)
                else:
                    data, silent = bytes(self.packet_size), silent + 1
                packets.append(data)
            if silent:
                self.silence_packets += silent
                if not self._in_underrun:
                    self._in_underrun = True
                    self.underruns += 1
            else:
                self._in_underrun = False
            self.packets_played += len(packets)
        try:
            t = self.handle.getTransfer(iso_packets=len(packets))
            t.setIsochronous(self.addr, b"".join(packets),
                             iso_transfer_length_list=[len(p) for p in packets])
            t.setCallback(self._device_done)
            with self._lock:
                self._inflight.add(t)
            t.submit()
        except usb1.USBError as e:
            with self._lock:
                self._inflight.discard(locals().get("t"))
            log.warning("%s: audio buffer: submit failed (%s)", self.name, e)
            gone = isinstance(e, (usb1.USBErrorNoDevice, usb1.USBErrorNotFound))
            self._finish(handed, -P.ESHUTDOWN if gone else 0)
            if gone:
                self.stop(wait=0)
            return False
        self._finish(handed)           # handed to the device queue -> client may send more
        return True

    def _device_done(self, t) -> None:
        status = t.getStatus()
        with self._lock:
            self._inflight.discard(t)
            stopping = self._stopping
        if status != usb1.TRANSFER_CANCELLED:        # cancelled on stop: not a device error
            try:
                for st in t.getISOSetupList():
                    if st["status"] != usb1.TRANSFER_COMPLETED:
                        self.device_errors[st["status"]] += 1
            except Exception:
                pass
        t.doom()
        if status == usb1.TRANSFER_NO_DEVICE:
            log.warning("%s: audio buffer: device gone", self.name)
            self._fail_all(-P.ESHUTDOWN)
            return
        if not stopping:
            if status != usb1.TRANSFER_COMPLETED:
                # cancelled/failed by the system (e.g. endpoint reset) while we still run:
                # keep the device fed instead of letting the stream die
                self.restarts += 1
            self._submit_one()

    # -- helpers ----------------------------------------------------------------------------
    def _finish(self, owners: List[_Owner], status: int = 0) -> None:
        for o in owners:
            with self._lock:
                if o.finished:
                    continue
                o.finished = True
                self._owners.pop(o.cmd.seqnum, None)
            o.done(o.cmd.seqnum, o.result(status))

    def _fail_all(self, status: int) -> None:
        with self._lock:
            self._stopping = True
            owners = list(self._owners.values())
            self._fifo.clear()
        self._finish(owners, status)

    def stop(self, wait: float = 1.0) -> None:
        """Stop the stream (interface switched off, session ends). Pending client URBs are
        completed as cancelled."""
        with self._lock:
            self._stopping = True
            inflight = list(self._inflight)
        for t in inflight:
            try:
                t.cancel()
            except usb1.USBError:
                pass
        deadline = time.monotonic() + wait
        while wait and time.monotonic() < deadline:
            with self._lock:
                if not self._inflight:
                    break
            time.sleep(0.005)
        self._fail_all(-P.ECONNRESET)

    @property
    def buffer_ms(self) -> float:
        return self.depth * self.ppt * self._packet_ms

    _packet_ms = 1.0

    def summary(self) -> str:
        s = _("Puffer {ms} ms, {urbs} URBs, mit Stille aufgefüllt {n}× ({packets} Pakete)",
              ms=f"{self.buffer_ms:.0f}", urbs=self.urbs, n=self.underruns,
              packets=self.silence_packets)
        if self.dropped_packets:
            s += ", " + _("verworfen {n} Pakete", n=self.dropped_packets)
        if self.restarts:
            s += ", " + _("vom System abgebrochen und neu gestartet {n}×", n=self.restarts)
        if self.device_errors:
            s += ", " + _("Gerätefehler") + " " + ", ".join(f"{k}: {n}" for k, n in self.device_errors.items())
        return s


def make_stream(handle, addr: int, cmd: SubmitCmd, high_speed: bool, buffer_ms: int,
                name: str) -> IsoOutStream:
    lengths = [p.length for p in cmd.iso_packets or []] or [0]
    nominal = Counter(lengths).most_common(1)[0][0] or max(lengths)
    packet_ms = 0.125 if high_speed else 1.0
    ppt = 32 if high_speed else 4                            # 4 ms per transfer
    depth = max(2, int(round(buffer_ms / (ppt * packet_ms))))
    s = IsoOutStream(handle, addr, nominal, ppt, depth, name)
    s._packet_ms = packet_ms
    return s
