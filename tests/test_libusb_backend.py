# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""LibusbSession against a simulated libusb device (shaped like a Logitech Unifying receiver:
three HID interfaces with one interrupt-IN endpoint each)."""
import time
import unittest

import usb1

from usbshare import protocol as P
from usbshare.libusb_backend import LibusbBackend, LibusbSession
from usbshare.protocol import SubmitCmd


class EP:
    def __init__(self, addr, attr=3):
        self.addr, self.attr = addr, attr

    def getAddress(self):
        return self.addr

    def getAttributes(self):
        return self.attr


class Setting:
    def __init__(self, num, eps):
        self.num, self.eps = num, eps

    def getNumber(self):
        return self.num

    def getClass(self):
        return 3

    def getSubClass(self):
        return 1

    def getProtocol(self):
        return 1

    def __iter__(self):
        return iter(self.eps)


class Config:
    def __init__(self):
        self.ifaces = [[Setting(i, [EP(0x81 + i)])] for i in range(3)]

    def getConfigurationValue(self):
        return 1

    def __iter__(self):
        return iter(self.ifaces)


class Transfer:
    def __init__(self, log):
        self.log, self.cb, self.kind = log, None, None

    def setInterrupt(self, addr, buf):
        self.kind, self.addr, self.buf = "int", addr, buf

    def setBulk(self, addr, buf):
        self.kind, self.addr, self.buf = "bulk", addr, buf

    def setControl(self, *a):
        self.kind = "ctrl"

    def setAddZeroPacket(self, v):
        pass

    def setShortIsError(self, v):
        self.log.append(("short_is_error", v))

    def setCallback(self, cb):
        self.cb = cb

    def submit(self):
        self.log.append(("submit", self.kind, hex(self.addr)))

    # completion side
    def finish(self, data, status=usb1.TRANSFER_COMPLETED):
        self._data, self._status = data, status
        self.cb(self)

    def getStatus(self):
        return self._status

    def getActualLength(self):
        return len(self._data)

    def getBuffer(self):
        return self._data

    def doom(self):
        pass

    def cancel(self):
        pass


class Handle:
    def __init__(self, dev):
        self.dev, self.log = dev, dev.log
        self.transfers = []

    def setAutoDetachKernelDriver(self, v):
        self.log.append(("autodetach", v))

    def getConfiguration(self):
        return 1

    def claimInterface(self, n):
        self.log.append(("claim", n))

    def releaseInterface(self, n):
        self.log.append(("release", n))

    def resetDevice(self):
        self.log.append(("reset",))
        if self.dev.reset_raises:
            exc, self.dev.reset_raises = self.dev.reset_raises, None
            raise exc

    def close(self):
        self.log.append(("close",))

    def getManufacturer(self):
        return "Logitech"

    def getProduct(self):
        return "USB Receiver"

    def getTransfer(self, iso_packets=0):
        t = Transfer(self.log)
        self.transfers.append(t)
        return t


class Dev:
    def __init__(self, log, reset_raises=None):
        self.log, self.reset_raises = log, reset_raises
        self.handles = []

    def open(self):
        h = Handle(self)
        self.handles.append(h)
        self.log.append(("open",))
        return h

    def iterConfigurations(self):
        return [Config()]

    def getBusNumber(self):
        return 1

    def getPortNumberList(self):
        return [4]

    def getDeviceAddress(self):
        return 7

    def getDeviceSpeed(self):
        return usb1.SPEED_FULL

    def getVendorID(self):
        return 0x046D

    def getProductID(self):
        return 0xC52B

    def getbcdDevice(self):
        return 0x1201

    def getDeviceClass(self):
        return 0

    def getDeviceSubClass(self):
        return 0

    def getDeviceProtocol(self):
        return 0

    def getNumConfigurations(self):
        return 1


def backend_for(dev):
    b = object.__new__(LibusbBackend)
    b.reset, b._names = True, {}
    b.find = lambda busid: dev
    return b


class SessionTest(unittest.TestCase):
    def test_claim_then_reset_and_reset_before_release(self):
        log = []
        dev = Dev(log)
        s = LibusbSession(backend_for(dev), dev)
        names = [e[0] for e in log]
        self.assertEqual(names[:5], ["open", "autodetach", "claim", "claim", "claim"])
        self.assertEqual(names[5], "reset")            # reset after all interfaces are ours
        self.assertEqual(s.info.busid, "1-4")
        self.assertEqual(s.info.description, "Logitech USB Receiver")
        log.clear()
        s.close()
        names = [e[0] for e in log]
        self.assertEqual(names, ["reset", "release", "release", "release", "close"])

    def test_reopen_when_device_reenumerates(self):
        log = []
        dev = Dev(log, reset_raises=usb1.USBErrorNotFound())
        s = LibusbSession(backend_for(dev), dev)
        self.assertEqual(len(dev.handles), 2)          # reopened after re-enumeration
        self.assertEqual([e[0] for e in log].count("claim"), 6)
        self.assertIs(s.handle, dev.handles[1])

    def test_no_reset_option(self):
        log = []
        dev = Dev(log)
        s = LibusbSession(backend_for(dev), dev, reset=False)
        s.close()
        self.assertNotIn(("reset",), log)

    def _interrupt(self, s, flags, data, length=8):
        results = []
        cmd = SubmitCmd(1, 0, P.USBIP_DIR_IN, 1, flags, length, 0, 0, 10, b"\0" * 8)
        s.submit(cmd, lambda seq, res: results.append(res))
        t = s.handle.transfers[-1]
        self.assertEqual((t.kind, t.addr, t.buf), ("int", 0x81, length))
        t.finish(data)
        return results[0]

    def test_interrupt_in_short_packet(self):
        log = []
        dev = Dev(log)
        s = LibusbSession(backend_for(dev), dev)
        # normal keyboard report, shorter than the buffer: delivered as success
        res = self._interrupt(s, 0, b"\x00\x00\x04\x00", 8)
        self.assertEqual((res.status, res.actual_length, res.data), (0, 4, b"\x00\x00\x04\x00"))
        # libusb must never drop short data (no SHORT_NOT_OK given to libusb)
        self.assertNotIn(("short_is_error", True), log)
        # with URB_SHORT_NOT_OK the data still arrives, status like the Linux kernel
        res = self._interrupt(s, P.URB_SHORT_NOT_OK, b"\x01\x02", 8)
        self.assertEqual((res.status, res.data), (-P.EREMOTEIO, b"\x01\x02"))
        res = self._interrupt(s, P.URB_SHORT_NOT_OK, b"\x01" * 8, 8)
        self.assertEqual(res.status, 0)
        self.assertIn("0x81", s.summary())

    def test_stall_is_reported_and_counted(self):
        dev = Dev([])
        s = LibusbSession(backend_for(dev), dev)
        cmd = SubmitCmd(2, 0, P.USBIP_DIR_IN, 2, 0, 8, 0, 0, 10, b"\0" * 8)
        out = []
        s.submit(cmd, lambda seq, r: out.append(r))
        s.handle.transfers[-1].finish(b"", usb1.TRANSFER_STALL)
        self.assertEqual(out[0].status, -P.EPIPE)
        self.assertIn("stall", s.summary())


if __name__ == "__main__":
    unittest.main()


# -- real python-libusb1 transfer objects (no device needed) ------------------------------
import ctypes  # noqa: E402


class RealTransfer(usb1.USBTransfer):
    """The library's own USBTransfer, so buffer layout and getBuffer() are the real ones.
    Only submit() is replaced; complete() fills the buffer the way libusb does."""

    def __init__(self):
        super().__init__(None, None, 0, lambda t: None, lambda t: None,
                         lambda obj, func, **kw: (lambda: None), False, False)
        self.submitted = False

    def submit(self):
        self.submitted = True

    def complete(self, data: bytes, offset: int):
        buf = self._USBTransfer__transfer_buffer
        ctypes.memmove(ctypes.addressof(buf) + offset, data, len(data))
        tr = self._USBTransfer__transfer.contents
        tr.actual_length, tr.status = len(data), usb1.TRANSFER_COMPLETED
        self.getCallback()(self)


class RealHandle(Handle):
    def getTransfer(self, iso_packets=0):
        t = RealTransfer()
        self.transfers.append(t)
        return t


class RealDev(Dev):
    def open(self):
        h = RealHandle(self)
        self.handles.append(h)
        return h


class RealLibusbBuffers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            usb1.loadLibrary()
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"libusb-1.0 not available: {e}")

    def setUp(self):
        dev = RealDev([])
        self.s = LibusbSession(backend_for(dev), dev, reset=False)

    def _run(self, cmd, data, offset):
        out = []
        self.s.submit(cmd, lambda seq, r: out.append(r))
        t = self.s.handle.transfers[-1]
        self.assertTrue(t.submitted)
        t.complete(data, offset)
        return out[0]

    def test_get_device_descriptor_is_passed_through_unchanged(self):
        desc = bytes([18, 1, 0x00, 0x02, 0, 0, 0, 8, 0x6A, 0x04, 0x92, 0xC0, 0x01, 0x01, 1, 2, 0, 1])
        setup = bytes([0x80, 6, 0x00, 0x01, 0, 0, 18, 0])
        cmd = SubmitCmd(1, 0, P.USBIP_DIR_IN, 0, 0x200, 18, 0, 0, 0, setup)
        res = self._run(cmd, desc, offset=8)       # libusb writes after the setup packet
        self.assertEqual((res.status, res.actual_length), (0, 18))
        self.assertEqual(res.data, desc)            # the original 18 bytes, nothing cut off

    def test_interrupt_in_with_real_buffer(self):
        cmd = SubmitCmd(2, 0, P.USBIP_DIR_IN, 1, 0, 8, 0, 0, 10, b"\0" * 8)
        res = self._run(cmd, b"\x00\x00\x04\x00\x00\x00\x00\x00", offset=0)
        self.assertEqual(res.data, b"\x00\x00\x04\x00\x00\x00\x00\x00")

    def test_reply_length_always_matches_data(self):
        from usbshare.protocol import UrbResult, pack_ret_submit
        raw = pack_ret_submit(5, UrbResult(0, 18, b"x" * 10), P.USBIP_DIR_IN, -1)
        self.assertEqual(int.from_bytes(raw[24:28], "big"), 10)
        self.assertEqual(len(raw), 48 + 10)


class RealIsoTransfer(RealTransfer):
    def __init__(self, iso_packets):
        usb1.USBTransfer.__init__(self, None, None, iso_packets, lambda t: None, lambda t: None,
                                  lambda obj, func, **kw: (lambda: None), False, False)
        self.submitted = False

    def complete_iso(self, packets):
        """packets: list of (data_or_None, actual_length) in submit order."""
        from usb1 import _libusb1 as L
        tp = self._USBTransfer__transfer
        buf = self._USBTransfer__transfer_buffer
        offset = 0
        for desc, pkt in zip(L.get_iso_packet_list(tp), packets):
            data, actual = pkt[0], pkt[1]
            status = pkt[2] if len(pkt) > 2 else usb1.TRANSFER_COMPLETED
            if data is not None:
                ctypes.memmove(ctypes.addressof(buf) + offset, data, len(data))
            desc.actual_length, desc.status = actual, status
            offset += desc.length
        tr = tp.contents
        tr.status = usb1.TRANSFER_COMPLETED
        self.getCallback()(self)


class RealIsoHandle(Handle):
    def getTransfer(self, iso_packets=0):
        t = RealIsoTransfer(iso_packets) if iso_packets else RealTransfer()
        self.transfers.append(t)
        return t


class RealIsoDev(Dev):
    def open(self):
        h = RealIsoHandle(self)
        self.handles.append(h)
        return h


class RealLibusbIso(unittest.TestCase):
    """USB audio uses isochronous transfers (headset speaker = OUT, microphone = IN)."""

    @classmethod
    def setUpClass(cls):
        try:
            usb1.loadLibrary()
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"libusb-1.0 not available: {e}")

    def setUp(self):
        dev = RealIsoDev([])
        self.s = LibusbSession(backend_for(dev), dev, reset=False)

    def test_iso_in_microphone(self):
        from usbshare.protocol import IsoPacket, pack_ret_submit
        iso = [IsoPacket(i * 96, 96) for i in range(4)]
        cmd = SubmitCmd(9, 0, P.USBIP_DIR_IN, 2, P.URB_ISO_ASAP, 384, 0, 4, 1, b"\0" * 8, b"", iso)
        out = []
        self.s.submit(cmd, lambda seq, r: out.append(r))
        t = self.s.handle.transfers[-1]
        t.complete_iso([(b"A" * 96, 96), (b"B" * 90, 90), (b"C" * 96, 96), (None, 0)])
        r = out[0]
        self.assertEqual(r.status, 0)
        self.assertEqual(r.data, b"A" * 96 + b"B" * 90 + b"C" * 96)   # compacted like Linux
        self.assertEqual(r.actual_length, 282)
        self.assertEqual([(p.offset, p.length, p.actual_length) for p in r.iso_packets],
                         [(0, 96, 96), (96, 96, 90), (192, 96, 96), (288, 96, 0)])
        raw = pack_ret_submit(9, r, P.USBIP_DIR_IN, -1)
        self.assertEqual(int.from_bytes(raw[32:36], "big"), 4)           # number_of_packets
        self.assertEqual(len(raw), 48 + 282 + 4 * 16)

    def test_iso_out_speaker(self):
        from usbshare.protocol import IsoPacket, pack_ret_submit
        iso = [IsoPacket(i * 192, 192) for i in range(3)]
        data = bytes(range(256)) * 2 + bytes(64)
        cmd = SubmitCmd(10, 0, P.USBIP_DIR_OUT, 1, P.URB_ISO_ASAP, 576, 0, 3, 1, b"\0" * 8,
                        data[:576], iso)
        out = []
        self.s.submit(cmd, lambda seq, r: out.append(r))
        t = self.s.handle.transfers[-1]
        sent = bytes(t._USBTransfer__transfer_buffer)[:576]
        self.assertEqual(sent, data[:576])                                # audio reaches the device
        t.complete_iso([(None, 192)] * 3)
        r = out[0]
        self.assertEqual((r.status, r.actual_length, r.data), (0, 576, b""))
        raw = pack_ret_submit(10, r, P.USBIP_DIR_OUT, -1)
        self.assertEqual(len(raw), 48 + 3 * 16)                           # no data back for OUT


class PortResetTest(unittest.TestCase):
    def test_port_reset_resets_device_instead_of_forwarding(self):
        log = []
        dev = Dev(log)
        s = LibusbSession(backend_for(dev), dev, reset=False)
        log.clear()
        out = []
        setup = bytes([0x23, 0x03, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00])   # SetPortFeature(RESET)
        s.submit(SubmitCmd(3, 0, P.USBIP_DIR_OUT, 0, 0, 0, 0, 0, 0, setup), lambda q, r: out.append(r))
        self.assertEqual(out[0].status, 0)
        self.assertIn(("reset",), log)
        self.assertFalse(s.handle.transfers)                 # nothing sent to the device

    def test_port_reset_with_reenumeration(self):
        log = []
        dev = Dev(log)
        s = LibusbSession(backend_for(dev), dev, reset=False)
        dev.reset_raises = usb1.USBErrorNotFound()
        out = []
        setup = bytes([0x23, 0x03, 0x04, 0x00, 0x01, 0x00, 0x00, 0x00])
        s.submit(SubmitCmd(4, 0, P.USBIP_DIR_OUT, 0, 0, 0, 0, 0, 0, setup), lambda q, r: out.append(r))
        self.assertEqual(out[0].status, 0)
        self.assertIs(s.handle, dev.handles[-1])             # reopened after re-enumeration


class IsoStreamHealth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            usb1.loadLibrary()
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"libusb-1.0 not available: {e}")

    def _urb(self, s, seq):
        from usbshare.protocol import IsoPacket
        iso = [IsoPacket(0, 192)]
        cmd = SubmitCmd(seq, 0, P.USBIP_DIR_OUT, 1, P.URB_ISO_ASAP, 192, 0, 1, 1, b"\0" * 8,
                        b"\0" * 192, iso)
        s.submit(cmd, lambda q, r: None)
        return s.handle.transfers[-1]

    def test_detects_stream_running_dry(self):
        dev = RealIsoDev([])
        s = LibusbSession(backend_for(dev), dev, reset=False)
        # healthy: client keeps 2 URBs queued
        pending = [self._urb(s, 1), self._urb(s, 2)]
        for i in range(3, 20):
            pending.pop(0).complete_iso([(None, 192)])
            pending.append(self._urb(s, i))
        self.assertEqual(s._iso_dry[0x01], 0)
        # client too late: queue empties before the next URB arrives
        for t in pending:
            t.complete_iso([(None, 192)])
        time.sleep(0.02)
        self._urb(s, 99)
        self.assertEqual(s._iso_dry[0x01], 1)
        self.assertGreaterEqual(s._iso_maxgap[0x01], 15)
        self.assertIn("leergelaufen 1", s.summary())


class IsoPacketErrors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            usb1.loadLibrary()
        except Exception as e:  # pragma: no cover
            raise unittest.SkipTest(f"libusb-1.0 not available: {e}")

    def test_missed_packets_reported_like_linux(self):
        from usbshare.protocol import IsoPacket
        dev = RealIsoDev([])
        s = LibusbSession(backend_for(dev), dev, reset=False)
        iso = [IsoPacket(i * 96, 96) for i in range(3)]
        cmd = SubmitCmd(1, 0, P.USBIP_DIR_IN, 1, P.URB_ISO_ASAP, 288, 0, 3, 1, b"\0" * 8, b"", iso)
        out = []
        s.submit(cmd, lambda q, r: out.append(r))
        s.handle.transfers[-1].complete_iso([(b"A" * 96, 96), (None, 0, usb1.TRANSFER_ERROR),
                                             (b"C" * 96, 96)])
        r = out[0]
        self.assertEqual(r.status, 0)
        self.assertEqual([p.status for p in r.iso_packets], [0, -P.EXDEV, 0])
        self.assertEqual(r.error_count, 1)
        self.assertIn("nicht übertragen 1", s.summary())

    def test_silence_is_detected(self):
        from usbshare.protocol import IsoPacket
        dev = RealIsoDev([])
        s = LibusbSession(backend_for(dev), dev, reset=False)
        for seq, payload in ((1, bytes(192)), (2, b"\x01" * 192)):
            cmd = SubmitCmd(seq, 0, P.USBIP_DIR_OUT, 1, P.URB_ISO_ASAP, 192, 0, 1, 1, b"\0" * 8,
                            payload, [IsoPacket(0, 192)])
            s.submit(cmd, lambda q, r: None)
            s.handle.transfers[-1].complete_iso([(None, 192)])
        self.assertIn("nur Stille 1×", s.summary())
