# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Real USB access through libusb (Linux, Windows, macOS)."""
from __future__ import annotations

import logging
import struct
import sys
import threading
import time
from collections import Counter
from typing import Dict, List, Optional, Tuple

import usb1

from . import protocol as P
from .backend import Backend, BackendError, CompletionFn, DeviceSession
from .iso_buffer import IsoOutStream, make_stream
from .protocol import DeviceInfo, IsoPacket, SubmitCmd, UrbResult

log = logging.getLogger("usbshare.libusb")

_SPEED = {
    usb1.SPEED_LOW: P.SPEED_LOW,
    usb1.SPEED_FULL: P.SPEED_FULL,
    usb1.SPEED_HIGH: P.SPEED_HIGH,
    usb1.SPEED_SUPER: P.SPEED_SUPER,
    getattr(usb1, "SPEED_SUPER_PLUS", 5): P.SPEED_SUPER_PLUS,
}

_STATUS = {
    usb1.TRANSFER_COMPLETED: 0,
    usb1.TRANSFER_ERROR: -P.EPROTO,
    usb1.TRANSFER_TIMED_OUT: -P.ETIMEDOUT,
    usb1.TRANSFER_CANCELLED: -P.ECONNRESET,
    usb1.TRANSFER_STALL: -P.EPIPE,
    usb1.TRANSFER_NO_DEVICE: -P.ESHUTDOWN,
    usb1.TRANSFER_OVERFLOW: -P.EOVERFLOW,
}
# Per-packet status of isochronous transfers. libusb folds the kernel's -EXDEV ("packet not
# transferred in its frame", routine for audio) and real errors into TRANSFER_ERROR. Like the
# Linux usbip host, report such packets as -EXDEV: Windows' audio driver skips those packets,
# while -EPROTO in nearly every packet makes it stop the stream (microphone goes silent).
_ISO_PKT_STATUS = dict(_STATUS)
_ISO_PKT_STATUS[usb1.TRANSFER_ERROR] = -P.EXDEV
_LIBUSB_NAMES = {usb1.TRANSFER_ERROR: "nicht übertragen", usb1.TRANSFER_OVERFLOW: "zu lang",
                 usb1.TRANSFER_STALL: "stall", usb1.TRANSFER_TIMED_OUT: "timeout",
                 usb1.TRANSFER_CANCELLED: "abgebrochen", usb1.TRANSFER_NO_DEVICE: "Gerät weg"}
CONTROL_LOG_LIMIT = 300

XFER_CONTROL, XFER_ISO, XFER_BULK, XFER_INTERRUPT = 0, 1, 2, 3
_ERRNAME = {P.EPIPE: "stall", P.EPROTO: "protocol", P.ETIMEDOUT: "timeout",
            P.ESHUTDOWN: "device gone", P.EOVERFLOW: "overflow", P.ECONNRESET: "cancelled",
            P.EREMOTEIO: "short"}
CLASS_HUB = 9


def _errno_for(exc: usb1.USBError) -> int:
    code = getattr(exc, "value", None)
    if code == usb1.ERROR_NO_DEVICE:
        return -P.ESHUTDOWN
    if code == usb1.ERROR_PIPE:
        return -P.EPIPE
    if code == usb1.ERROR_TIMEOUT:
        return -P.ETIMEDOUT
    return -P.EPROTO


def _open_hint(exc: usb1.USBError) -> str:
    code = getattr(exc, "value", None)
    if sys.platform.startswith("win"):
        if code in (usb1.ERROR_NOT_SUPPORTED, usb1.ERROR_NOT_FOUND):
            return ("no WinUSB/libusbK driver bound to this device: install WinUSB for it "
                    "with Zadig, or install UsbDk and start the server with --usbdk")
        if code == usb1.ERROR_ACCESS:
            return "device is in use by another program"
    elif sys.platform == "darwin":
        if code == usb1.ERROR_ACCESS:
            return "macOS refused access; run the server with sudo"
    else:
        if code == usb1.ERROR_ACCESS:
            return "permission denied; run as root or install the udev rule (contrib/99-usbshare.rules)"
    if code == usb1.ERROR_BUSY:
        return "device is busy (claimed by another program)"
    return str(exc)


def _busid(dev: usb1.USBDevice) -> str:
    bus = dev.getBusNumber()
    try:
        ports = dev.getPortNumberList()
    except usb1.USBError:
        ports = []
    if ports:
        return f"{bus}-{'.'.join(str(p) for p in ports)}"
    return f"{bus}-a{dev.getDeviceAddress()}"


def _strings(h: usb1.USBDeviceHandle) -> str:
    parts = []
    for getter in (h.getManufacturer, h.getProduct):
        try:
            s = getter()
            if s and s.strip() not in parts:
                parts.append(s.strip())
        except usb1.USBError:
            pass
    return " ".join(parts)


def _configs(dev: usb1.USBDevice):
    try:
        return list(dev.iterConfigurations())
    except usb1.USBError:
        return []


class LibusbBackend(Backend):
    def __init__(self, use_usbdk: bool = False, reset: bool = True, audio_buffer_ms: int = 40):
        self.reset = reset
        self.audio_buffer_ms = audio_buffer_ms
        self.ctx = usb1.USBContext(use_usbdk=use_usbdk) if use_usbdk else usb1.USBContext()
        self.ctx.open()
        self._stop = threading.Event()
        self._events = threading.Thread(target=self._event_loop, name="libusb-events", daemon=True)
        self._events.start()
        self._names: Dict[Tuple[str, int, int], str] = {}   # (busid, vid, pid) -> name

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.ctx.handleEventsTimeout(0.2)
            except usb1.USBErrorInterrupted:
                continue
            except Exception:  # pragma: no cover - keep pumping no matter what
                log.exception("libusb event loop error")
                time.sleep(0.2)

    def shutdown(self) -> None:
        self._stop.set()
        self._events.join(timeout=2)
        self.ctx.close()

    # -- enumeration --------------------------------------------------------
    def _iter(self):
        for dev in self.ctx.getDeviceList(skip_on_error=True):
            try:
                if dev.getDeviceClass() == CLASS_HUB:
                    continue
            except usb1.USBError:
                continue
            yield dev

    def _info(self, dev: usb1.USBDevice, config_value: Optional[int] = None) -> DeviceInfo:
        cfgs = _configs(dev)
        cfg = None
        if cfgs:
            cfg = next((c for c in cfgs if c.getConfigurationValue() == config_value), cfgs[0])
        interfaces: List[Tuple[int, int, int]] = []
        if cfg is not None:
            for iface in cfg:
                for setting in iface:
                    interfaces.append((setting.getClass(), setting.getSubClass(),
                                       setting.getProtocol()))
                    break
        return DeviceInfo(
            busid=_busid(dev), busnum=dev.getBusNumber(), devnum=dev.getDeviceAddress(),
            speed=_SPEED.get(dev.getDeviceSpeed(), P.SPEED_UNKNOWN),
            id_vendor=dev.getVendorID(), id_product=dev.getProductID(),
            bcd_device=dev.getbcdDevice(), device_class=dev.getDeviceClass(),
            device_subclass=dev.getDeviceSubClass(), device_protocol=dev.getDeviceProtocol(),
            configuration_value=cfg.getConfigurationValue() if cfg else 1,
            num_configurations=dev.getNumConfigurations(), interfaces=interfaces,
        )

    def list_devices(self, with_names: bool = False, skip=()) -> List[DeviceInfo]:
        out = []
        for dev in self._iter():
            try:
                info = self._info(dev)
            except usb1.USBError:
                continue
            key = (info.busid, info.id_vendor, info.id_product)
            cached = self._names.get(key)
            if cached is not None:
                info.description = cached
            elif with_names and info.busid not in skip:
                text, ok = self._describe(dev)
                if ok:
                    self._names[key] = text
                info.description = text
            out.append(info)
        return sorted(out, key=lambda d: d.busid)

    def _describe(self, dev: usb1.USBDevice) -> Tuple[str, bool]:
        try:
            h = dev.open()
        except usb1.USBError as e:
            return f"[not accessible: {_open_hint(e)}]", False
        try:
            return _strings(h), True
        finally:
            h.close()

    def open(self, busid: str) -> DeviceSession:
        dev = next((d for d in self._iter() if _busid(d) == busid), None)
        if dev is None:
            raise BackendError(f"no device with busid {busid}", P.ST_NODEV)
        return LibusbSession(self, dev, reset=self.reset,
                             audio_buffer_ms=getattr(self, "audio_buffer_ms", 40))

    def find(self, busid: str) -> Optional[usb1.USBDevice]:
        return next((d for d in self._iter() if _busid(d) == busid), None)


class LibusbSession(DeviceSession):
    def __init__(self, backend: LibusbBackend, dev: usb1.USBDevice, reset: bool = True,
                 audio_buffer_ms: int = 40):
        self.backend, self.dev, self.reset = backend, dev, reset
        self.audio_buffer_ms = audio_buffer_ms
        self._streams: Dict[int, IsoOutStream] = {}       # isochronous OUT addr -> buffer
        self._ep_iface: Dict[int, int] = {}
        self.busid = _busid(dev)
        self._lock = threading.Lock()
        self._transfers: Dict[int, usb1.USBTransfer] = {}
        self._claimed: List[int] = []
        self._epmap: Dict[int, int] = {}
        # diagnostics
        self._urbs = 0
        self._data_seen: set = set()
        self._errors: Counter = Counter()
        # isochronous (audio/video) stream health: a stream "runs dry" when the previous URB
        # has completed before the client's next one arrived – the device then has a gap.
        self._iso_inflight: Counter = Counter()
        self._iso_count: Counter = Counter()
        self._iso_dry: Counter = Counter()
        self._iso_maxgap: Dict[int, float] = {}
        self._iso_last_done: Dict[int, float] = {}
        self._iso_pkt_err: Counter = Counter()
        self._iso_pkt_kind: Counter = Counter()      # (addr, libusb status) -> packets
        self._iso_silent: Counter = Counter()        # OUT URBs that carried only zeros
        self._ctrl_logged = 0
        self._stream_stats: List[str] = []
        self._open_and_claim(dev)
        if reset:
            # Put the device into a clean, just-plugged-in state for the client. Some devices
            # keep a mode the host driver switched them into otherwise (e.g. Logitech
            # Unifying/Bolt receivers stay in "DJ mode" after Linux' hid-logitech-dj and then
            # send no normal keyboard/mouse reports). VirtualHere and QEMU reset as well.
            self._reset_device()
        cfg = self.cfg
        self.info = backend._info(self.dev, cfg)
        key = (self.info.busid, self.info.id_vendor, self.info.id_product)
        if key not in backend._names:
            try:
                backend._names[key] = _strings(self.handle)
            except Exception:
                pass
        self.info.description = backend._names.get(key, "")

    def _open_and_claim(self, dev: usb1.USBDevice) -> None:
        self.dev = dev
        try:
            self.handle = dev.open()
        except usb1.USBError as e:
            raise BackendError(_open_hint(e), P.ST_DEV_ERR) from e
        try:
            self.handle.setAutoDetachKernelDriver(True)
        except usb1.USBError:
            pass  # not supported on Windows; on macOS needs root
        try:
            cfg = self.handle.getConfiguration()
        except usb1.USBError:
            cfg = 0
        if not cfg:
            cfgs = _configs(dev)
            if cfgs:
                cfg = cfgs[0].getConfigurationValue()
                try:
                    self.handle.setConfiguration(cfg)
                except usb1.USBError as e:
                    log.warning("could not set configuration %d: %s", cfg, e)
        self.cfg = cfg
        self._claim_all()

    def _reset_device(self) -> None:
        try:
            self.handle.resetDevice()
            log.info("%s: device reset", self.busid)
            return
        except (usb1.USBErrorNotFound, usb1.USBErrorNoDevice):
            pass                     # device re-enumerated after the reset: open it again
        except usb1.USBError as e:
            log.warning("%s: device reset failed (%s), continuing without", self.busid, e)
            return
        log.info("%s: device re-enumerated after reset, reopening", self.busid)
        try:
            self.handle.close()
        except Exception:
            pass
        self._claimed.clear()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            dev = self.backend.find(self.busid)
            if dev is not None:
                try:
                    self._open_and_claim(dev)
                    return
                except BackendError:
                    pass
            time.sleep(0.2)
        raise BackendError("device did not come back after reset", P.ST_DEV_ERR)

    # -- interfaces ---------------------------------------------------------
    def _current_config(self):
        return next((c for c in _configs(self.dev) if c.getConfigurationValue() == self.cfg), None)

    def _claim_all(self) -> None:
        self._epmap.clear()
        self._ep_iface.clear()
        config = self._current_config()
        if config is None:
            return
        for iface in config:
            number = None
            for setting in iface:
                number = setting.getNumber()
                for ep in setting:
                    self._epmap[ep.getAddress()] = ep.getAttributes() & 0x03
                    self._ep_iface[ep.getAddress()] = number
            if number is None:
                continue
            try:
                self.handle.claimInterface(number)
                self._claimed.append(number)
            except usb1.USBError as e:
                log.warning("cannot claim interface %d: %s", number, _open_hint(e))

    def _release_all(self) -> None:
        for number in self._claimed:
            try:
                self.handle.releaseInterface(number)
            except usb1.USBError:
                pass
        self._claimed.clear()

    # -- URBs ---------------------------------------------------------------
    def submit(self, cmd: SubmitCmd, done: CompletionFn) -> None:
        if cmd.ep == 0:
            if self._special_control(cmd, done):
                return
        addr = cmd.endpoint_address
        is_in = cmd.direction == P.USBIP_DIR_IN
        iso = cmd.iso_packets
        if iso and not is_in:
            if cmd.data and cmd.data.count(0) == len(cmd.data):
                self._iso_silent[addr] += 1
            if self.audio_buffer_ms > 0:
                self._stream_for(addr, cmd).feed(cmd, done)
                return
        if iso:
            self._iso_submitted(addr)
        try:
            t = self.handle.getTransfer(iso_packets=len(iso) if iso else 0)
            if cmd.ep == 0:
                rt, req, val, idx, length = struct.unpack("<BBHHH", cmd.setup)
                t.setControl(rt, req, val, idx, length if rt & 0x80 else cmd.data[:length])
            elif iso:
                lengths = [p.length for p in iso]
                buf = sum(lengths) if is_in else b"".join(
                    cmd.data[p.offset:p.offset + p.length] for p in iso)
                t.setIsochronous(addr, buf, iso_transfer_length_list=lengths)
            else:
                kind = self._epmap.get(addr, XFER_BULK)
                buf = cmd.transfer_buffer_length if is_in else cmd.data
                if kind == XFER_INTERRUPT:
                    t.setInterrupt(addr, buf)
                else:
                    t.setBulk(addr, buf)
                # URB_SHORT_NOT_OK is evaluated on completion (like the Linux kernel: the data
                # is returned with -EREMOTEIO), not by libusb, which would drop the data.
                t.setAddZeroPacket(bool(cmd.transfer_flags & P.URB_ZERO_PACKET) and not is_in)
            t.setCallback(lambda tr, c=cmd, d=done: self._complete(tr, c, d))
            with self._lock:
                self._transfers[cmd.seqnum] = t
            t.submit()
        except usb1.USBError as e:
            with self._lock:
                self._transfers.pop(cmd.seqnum, None)
            log.debug("submit seq=%d ep=0x%02x failed: %s", cmd.seqnum, addr, e)
            done(cmd.seqnum, UrbResult(status=_errno_for(e)))

    def _complete(self, t: usb1.USBTransfer, cmd: SubmitCmd, done: CompletionFn) -> None:
        with self._lock:
            self._transfers.pop(cmd.seqnum, None)
            if cmd.iso_packets:
                a = cmd.endpoint_address
                self._iso_inflight[a] = max(0, self._iso_inflight[a] - 1)
                self._iso_last_done[a] = time.monotonic()
        try:
            status = _STATUS.get(t.getStatus(), -P.EPROTO)
            is_in = cmd.direction == P.USBIP_DIR_IN
            if cmd.iso_packets:
                setups = t.getISOSetupList()
                bufs = t.getISOBufferList() if is_in else [b""] * len(setups)
                packets, chunks, errors = [], [], 0
                for orig, st, buf in zip(cmd.iso_packets, setups, bufs):
                    pst = _ISO_PKT_STATUS.get(st["status"], -P.EPROTO)
                    if st["status"] != usb1.TRANSFER_COMPLETED:
                        self._iso_pkt_kind[(cmd.endpoint_address, st["status"])] += 1
                    errors += pst != 0
                    packets.append(IsoPacket(orig.offset, orig.length, st["actual_length"], pst))
                    if is_in:
                        chunks.append(bytes(buf[:st["actual_length"]]))
                if errors:
                    self._iso_pkt_err[cmd.endpoint_address] += errors
                data = b"".join(chunks)
                actual = len(data) if is_in else sum(p.actual_length for p in packets)
                res = UrbResult(status, actual, data, packets, cmd.start_frame, errors)
            else:
                actual = t.getActualLength()
                data = b""
                if is_in:
                    # python-libusb1's getBuffer() already excludes the 8-byte setup packet
                    # of control transfers, so the data always starts at offset 0.
                    data = bytes(t.getBuffer()[:actual])
                    if (status == 0 and cmd.ep != 0 and cmd.transfer_flags & P.URB_SHORT_NOT_OK
                            and actual < cmd.transfer_buffer_length):
                        status = -P.EREMOTEIO
                res = UrbResult(status, actual, data)
        except Exception:
            log.exception("completion handling failed")
            res = UrbResult(status=-P.EPROTO)
        t.doom()  # python-libusb1 frees it after this callback returns
        self._account(cmd, res)
        done(cmd.seqnum, res)

    # -- diagnostics ----------------------------------------------------------
    def _account(self, cmd: SubmitCmd, res: UrbResult) -> None:
        addr = cmd.endpoint_address
        self._urbs += 1
        if cmd.ep == 0:
            log.debug("%s: control %s -> status %d, %d bytes", self.busid, cmd.setup.hex(),
                      res.status, res.actual_length)
            rt, req, val, idx, _ln = struct.unpack("<BBHHH", cmd.setup)
            if rt & 0x60 == 0x20:                     # class requests (audio: rate, volume, mute)
                what = {0x01: "SET_CUR", 0x81: "GET_CUR", 0x82: "GET_MIN", 0x83: "GET_MAX",
                        0x84: "GET_RES"}.get(req, f"req 0x{req:02x}")
                target = {1: "Interface/Unit", 2: "Endpoint"}.get(rt & 0x1F, "Gerät")
                self._log_ctrl(f"Klassen-Anfrage {what} an {target} wIndex=0x{idx:04x} "
                               f"wValue=0x{val:04x} → " + ("ok" if res.status == 0 else
                                                           f"Fehler {res.status}"))
        if res.status == 0 and cmd.direction == P.USBIP_DIR_IN and res.actual_length and cmd.ep:
            if addr not in self._data_seen:
                self._data_seen.add(addr)
                log.info("%s: first data from endpoint 0x%02x (%d bytes) – device is sending",
                         self.busid, addr, res.actual_length)
        elif res.status not in (0, -P.ECONNRESET, -P.EREMOTEIO):
            key = (addr, res.status)
            self._errors[key] += 1
            if self._errors[key] == 1:
                log.warning("%s: endpoint 0x%02x: error %d (%s)", self.busid, addr, res.status,
                            _ERRNAME.get(-res.status, "?"))

    def _stream_for(self, addr: int, cmd: SubmitCmd) -> IsoOutStream:
        with self._lock:
            st = self._streams.get(addr)
            if st is None:
                hs = self.dev.getDeviceSpeed() >= usb1.SPEED_HIGH
                st = make_stream(self.handle, addr, cmd, hs, self.audio_buffer_ms,
                                 f"{self.busid} 0x{addr:02x}")
                self._streams[addr] = st
                log.info("%s: Audio-Puffer für Endpoint 0x%02x aktiv (%.0f ms)", self.busid, addr,
                         st.buffer_ms)
        return st

    def _stop_streams(self, interface: Optional[int] = None) -> None:
        with self._lock:
            victims = [(a, s) for a, s in self._streams.items()
                       if interface is None or self._ep_iface.get(a, interface) == interface]
            for a, _s in victims:
                del self._streams[a]
        for a, st in victims:
            st.stop()
            self._stream_stats.append(f"0x{a:02x}: {st.summary()}")
            log.info("%s: Audio-Puffer 0x%02x beendet – %s", self.busid, a, st.summary())

    def _log_ctrl(self, text: str) -> None:
        if self._ctrl_logged < CONTROL_LOG_LIMIT:
            self._ctrl_logged += 1
            log.info("%s: %s", self.busid, text)

    def _iso_submitted(self, addr: int) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._iso_last_done.get(addr)
            dry = (self._iso_count[addr] > 4 and self._iso_inflight[addr] == 0
                   and last is not None and now - last < 1.0)
            self._iso_count[addr] += 1
            self._iso_inflight[addr] += 1
            if not dry:
                return
            gap = (now - last) * 1000
            self._iso_dry[addr] += 1
            self._iso_maxgap[addr] = max(self._iso_maxgap.get(addr, 0.0), gap)
            n = self._iso_dry[addr]
        if n == 1 or n % 100 == 0:
            log.warning("%s: audio/video stream on endpoint 0x%02x ran dry %d× (gap %.1f ms) – "
                        "data from the client arrives too late (network delay or CPU load)",
                        self.busid, addr, n, gap)

    def iso_summary(self) -> str:
        parts = []
        for a in sorted(self._iso_count):
            parts.append(f"0x{a:02x}: {self._iso_count[a]} URBs, leergelaufen {self._iso_dry[a]}×"
                         + (f" (max. {self._iso_maxgap[a]:.0f} ms)" if self._iso_dry[a] else "")
                         + (f", Paketfehler {self._iso_pkt_err[a]} ("
                            + ", ".join(f"{_LIBUSB_NAMES.get(k, k)} {n}" for (ea, k), n
                                        in sorted(self._iso_pkt_kind.items()) if ea == a) + ")"
                            if self._iso_pkt_err[a] else "")
                         + (f", nur Stille {self._iso_silent[a]}×" if self._iso_silent[a] else ""))
        return "; ".join(parts)

    def summary(self) -> str:
        eps = ", ".join(f"0x{a:02x}" for a in sorted(self._data_seen)) or "keine"
        errs = ", ".join(f"0x{a:02x}:{_ERRNAME.get(-s, s)}x{n}"
                         for (a, s), n in sorted(self._errors.items())) or "keine"
        iso = self.iso_summary()
        buf = "; ".join(self._stream_stats)
        return (f"{self._urbs} URBs, Daten von Endpoints: {eps}, Fehler: {errs}"
                + (f", Audio/Video: {iso}" if iso else "")
                + (f", Audio-Puffer: {buf}" if buf else ""))

    def _special_control(self, cmd: SubmitCmd, done: CompletionFn) -> bool:
        """Requests that must go through the OS API instead of the wire (like usbip-host)."""
        rt, req, val, idx, _ = struct.unpack("<BBHHH", cmd.setup)
        try:
            if rt == 0x00 and req == 0x09:            # SET_CONFIGURATION
                if val != self.cfg:
                    self._stop_streams()
                    self._cancel_all()
                    self._release_all()
                    self.handle.setConfiguration(val)
                    self.cfg = val
                    self._claim_all()
            elif rt == 0x01 and req == 0x0B:          # SET_INTERFACE
                self._stop_streams(idx)
                try:
                    self.handle.setInterfaceAltSetting(idx, val)
                finally:
                    self._log_ctrl(f"SET_INTERFACE Interface {idx} → Alternative {val}")
            elif rt == 0x02 and req == 0x01 and val == 0:  # CLEAR_FEATURE(ENDPOINT_HALT)
                self.handle.clearHalt(idx & 0xFF)
            elif rt == 0x00 and req == 0x05:          # SET_ADDRESS: host already did this
                pass
            elif rt == 0x23 and req == 0x03 and val == 4:
                # SET_FEATURE(PORT_RESET): the client resets its virtual port (usbip-win2 does
                # this e.g. for audio devices). Like the Linux usbip host: reset the real
                # device here instead of sending a hub request to it (it would stall and
                # Windows would drop the device).
                log.info("%s: client requested port reset", self.busid)
                self._stop_streams()
                self._cancel_all()
                self._reset_device()
            elif rt == 0x23:
                # other hub-port requests for the virtual port: nothing to do on the device
                log.debug("%s: ignoring port request %02x/%04x", self.busid, req, val)
            else:
                return False
        except BackendError as e:
            log.warning("%s: %s", self.busid, e)
            done(cmd.seqnum, UrbResult(status=-P.ESHUTDOWN))
            return True
        except usb1.USBError as e:
            log.warning("%s: control request %02x/%02x failed: %s", self.busid, rt, req, e)
            done(cmd.seqnum, UrbResult(status=_errno_for(e)))
            return True
        done(cmd.seqnum, UrbResult(status=0))
        return True

    def cancel(self, seqnum: int) -> None:
        with self._lock:
            streams = list(self._streams.values())
        for st in streams:
            if st.cancel(seqnum):
                return
        with self._lock:
            t = self._transfers.get(seqnum)
        if t is not None:
            try:
                t.cancel()
            except usb1.USBError:
                pass  # already completing

    def _cancel_all(self, wait: float = 2.0) -> None:
        with self._lock:
            pending = list(self._transfers.values())
        for t in pending:
            try:
                t.cancel()
            except usb1.USBError:
                pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            with self._lock:
                if not self._transfers:
                    return
            time.sleep(0.01)

    def close(self) -> None:
        self._stop_streams()
        self._cancel_all()
        log.info("%s: session ended – %s", self.busid, self.summary())
        if self.reset:
            try:
                # hand the device back in a clean state; the host driver then re-initialises it
                self.handle.resetDevice()
            except usb1.USBError:
                pass
        self._release_all()   # auto-detach re-attaches the host kernel driver
        try:
            self.handle.close()
        except Exception:
            pass
