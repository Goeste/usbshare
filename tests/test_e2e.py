# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""End-to-end tests: real TCP server + simulated USB device + a minimal USB/IP client
that behaves like the Linux vhci-hcd driver on the wire."""
import socket
import struct
import threading
import time
import unittest

from usbshare import protocol as P
from usbshare.backend import Backend, BackendError, DeviceSession
from usbshare.client import remote_devlist
from usbshare.protocol import DeviceInfo, IsoPacket, SubmitCmd, UrbResult
from usbshare.server import ClientFilter, DeviceFilter, UsbIpServer, recv_exact

DEVICE_DESC = bytes([18, 1, 0x00, 0x02, 0, 0, 0, 64, 0x34, 0x12, 0x78, 0x56, 0x00, 0x01, 1, 2, 3, 1])


class FakeSession(DeviceSession):
    """Bulk loopback device: OUT ep1 queues data, IN ep 0x81 returns it.
    ISO IN ep 0x82 returns 3 bytes per packet. IN on 0x81 waits until data exists."""

    def __init__(self, info):
        self.info = info
        self.buffer = bytearray()
        self.waiting = {}          # seqnum -> (cmd, done)
        self.lock = threading.Lock()
        self.closed = False

    def submit(self, cmd, done):
        if cmd.ep == 0:
            rt, req, val, idx, ln = struct.unpack("<BBHHH", cmd.setup)
            if rt == 0x80 and req == 6 and val >> 8 == 1:
                data = DEVICE_DESC[:ln]
                return self._later(done, cmd.seqnum, UrbResult(0, len(data), data))
            return self._later(done, cmd.seqnum, UrbResult(-P.EPIPE))   # stall
        if cmd.endpoint_address == 0x01:
            with self.lock:
                self.buffer += cmd.data
                self._serve_waiting()
            return self._later(done, cmd.seqnum, UrbResult(0, len(cmd.data)))
        if cmd.endpoint_address == 0x81:
            with self.lock:
                self.waiting[cmd.seqnum] = (cmd, done)
                self._serve_waiting()
            return
        if cmd.endpoint_address == 0x84:                 # device vanished on the server
            return self._later(done, cmd.seqnum, UrbResult(-P.ESHUTDOWN))
        if cmd.endpoint_address == 0x82 and cmd.iso_packets:
            pk = [IsoPacket(p.offset, p.length, 3, 0) for p in cmd.iso_packets]
            data = b"".join(bytes([i]) * 3 for i in range(len(pk)))
            return self._later(done, cmd.seqnum, UrbResult(0, len(data), data, pk))
        self._later(done, cmd.seqnum, UrbResult(-P.EPIPE))

    def _serve_waiting(self):
        for seq, (cmd, done) in list(self.waiting.items()):
            if not self.buffer:
                return
            n = min(len(self.buffer), cmd.transfer_buffer_length)
            data = bytes(self.buffer[:n])
            del self.buffer[:n]
            del self.waiting[seq]
            self._later(done, seq, UrbResult(0, n, data))

    def _later(self, done, seq, res):
        threading.Timer(0.01, done, args=(seq, res)).start()

    def cancel(self, seqnum):
        with self.lock:
            item = self.waiting.pop(seqnum, None)
        if item:
            self._later(item[1], seqnum, UrbResult(-P.ECONNRESET))

    def close(self):
        self.closed = True


class FakeBackend(Backend):
    def __init__(self):
        self.sessions = []

    def list_devices(self, with_names=False, skip=()):
        return [
            DeviceInfo("1-1", 1, 2, P.SPEED_HIGH, 0x1234, 0x5678, 0x0100, 0, 0, 0, 1, 1,
                       [(0xFF, 0, 0)], "Fake Loopback"),
            DeviceInfo("1-2", 1, 3, P.SPEED_FULL, 0x046D, 0xC52B, 0x1201, 0, 0, 0, 1, 1,
                       [(3, 1, 1), (3, 1, 2)], "Fake Receiver"),
        ]

    def open(self, busid):
        if busid == "1-2":
            raise BackendError("driver missing", P.ST_DEV_ERR)
        s = FakeSession(self.list_devices()[0])
        self.sessions.append(s)
        return s


class VhciLikeClient:
    def __init__(self, port, busid, source="127.0.0.1"):
        self.s = socket.create_connection(("127.0.0.1", port), timeout=5,
                                          source_address=(source, 0))
        self.s.sendall(P.pack_op(P.OP_REQ_IMPORT) + busid.encode().ljust(32, b"\0"))
        _v, code, status = P.OP_HEADER.unpack(recv_exact(self.s, 8))
        self.status = status
        if status == 0:
            self.info = DeviceInfo.unpack(recv_exact(self.s, 312))
        self.seq = 0

    def submit(self, ep, direction, length=0, data=b"", setup=b"", iso=None):
        self.seq += 1
        cmd = SubmitCmd(self.seq, 0x10002, direction, ep, 0, length or len(data), 0,
                        len(iso) if iso else 0, 0, setup, data, iso)
        self.s.sendall(P.pack_cmd_submit(cmd))
        return self.seq

    def unlink(self, target):
        self.seq += 1
        self.s.sendall(P.pack_cmd_unlink(self.seq, 0x10002, target))
        return self.seq

    def recv(self):
        hdr = recv_exact(self.s, 48)
        cmd, seq, _d, _dir, _ep = P.BASIC_HEADER.unpack_from(hdr)
        if cmd == P.USBIP_RET_UNLINK:
            (status,) = P.RET_UNLINK.unpack_from(hdr, 20)
            return ("unlink", seq, status, b"", None)
        status, actual, _sf, npk, _err = P.RET_SUBMIT.unpack_from(hdr, 20)
        return cmd, seq, status, actual, npk

    def recv_data(self, n):
        return recv_exact(self.s, n)


def fake_peer(server, peer_ip):
    """Hand a real TCP connection to the server as if it came from peer_ip."""
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen(1)
    client = socket.create_connection(lst.getsockname())
    conn, _ = lst.accept()
    lst.close()
    threading.Thread(target=server._handle, args=(conn, peer_ip), daemon=True).start()
    client.settimeout(5)
    return client


class E2E(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.server = UsbIpServer(self.backend, "127.0.0.1", 0,
                                  device_filter=DeviceFilter(deny=["dead:beef"]))
        self.server.bind()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.stop()

    def test_devlist(self):
        devs = remote_devlist("127.0.0.1", self.server.port)
        self.assertEqual([d.busid for d in devs], ["1-1", "1-2"])
        self.assertEqual(devs[1].interfaces, [(3, 1, 1), (3, 1, 2)])
        self.assertEqual(devs[0].vidpid, "1234:5678")

    def test_filter(self):
        self.server.device_filter = DeviceFilter(allow=["046d:*"])
        self.assertEqual([d.busid for d in remote_devlist("127.0.0.1", self.server.port)], ["1-2"])
        c = VhciLikeClient(self.server.port, "1-1")
        self.assertEqual(c.status, P.ST_NODEV)

    def test_import_error_status(self):
        c = VhciLikeClient(self.server.port, "1-2")
        self.assertEqual(c.status, P.ST_DEV_ERR)

    def test_control_bulk_and_busy(self):
        c = VhciLikeClient(self.server.port, "1-1")
        self.assertEqual(c.status, 0)
        self.assertEqual(c.info.busid, "1-1")
        # busy: second import refused, and device vanishes from devlist
        self.assertEqual(VhciLikeClient(self.server.port, "1-1").status, P.ST_DEV_BUSY)
        self.assertEqual([d.busid for d in remote_devlist("127.0.0.1", self.server.port)], ["1-2"])

        setup = struct.pack("<BBHHH", 0x80, 6, 0x0100, 0, 18)
        seq = c.submit(0, P.USBIP_DIR_IN, 18, setup=setup)
        cmd, rseq, status, actual, _ = c.recv()
        self.assertEqual((cmd, rseq, status, actual), (P.USBIP_RET_SUBMIT, seq, 0, 18))
        self.assertEqual(c.recv_data(18), DEVICE_DESC)

        # stall
        c.submit(0, P.USBIP_DIR_IN, 4, setup=struct.pack("<BBHHH", 0xC0, 0x42, 0, 0, 4))
        self.assertEqual(c.recv()[2], -P.EPIPE)

        # bulk loopback
        seq_out = c.submit(1, P.USBIP_DIR_OUT, data=b"hello usb")
        r = c.recv()
        self.assertEqual((r[1], r[2], r[3]), (seq_out, 0, 9))
        c.submit(1, P.USBIP_DIR_IN, 512)
        r = c.recv()
        self.assertEqual((r[2], r[3]), (0, 9))
        self.assertEqual(c.recv_data(9), b"hello usb")

        # disconnect frees the device
        c.s.close()
        time.sleep(0.2)
        self.assertTrue(self.backend.sessions[0].closed)
        self.assertEqual(len(remote_devlist("127.0.0.1", self.server.port)), 2)

    def test_unlink_pending_and_completed(self):
        c = VhciLikeClient(self.server.port, "1-1")
        seq_in = c.submit(1, P.USBIP_DIR_IN, 64)      # waits forever (no data)
        useq = c.unlink(seq_in)
        r = c.recv()
        self.assertEqual(r, ("unlink", useq, -P.ECONNRESET, b"", None))
        # unlink of something already finished -> status 0
        c.submit(1, P.USBIP_DIR_OUT, data=b"x")
        done_seq = c.recv()[1]
        useq = c.unlink(done_seq)
        self.assertEqual(c.recv(), ("unlink", useq, 0, b"", None))

    def test_iso_in(self):
        c = VhciLikeClient(self.server.port, "1-1")
        iso = [IsoPacket(i * 8, 8) for i in range(4)]
        c.submit(2, P.USBIP_DIR_IN, 32, iso=iso)
        cmd, _seq, status, actual, npk = c.recv()
        self.assertEqual((status, actual, npk), (0, 12, 4))
        self.assertEqual(c.recv_data(12), b"\0\0\0\1\1\1\2\2\2\3\3\3")
        descs = [P.ISO_DESC.unpack(c.recv_data(16)) for _ in range(4)]
        self.assertEqual(descs[2], (16, 8, 3, 0))

    def test_client_filter(self):
        self.assertTrue(ClientFilter(["192.168.1.0/24"]).permits("192.168.1.7"))
        self.assertFalse(ClientFilter(["192.168.1.0/24"]).permits("10.0.0.1"))
        self.assertTrue(ClientFilter(["192.168.1.0/24"]).permits("::ffff:192.168.1.7"))
        self.server.client_filter = ClientFilter(["10.0.0.0/8"])
        c = fake_peer(self.server, "192.168.5.5")          # not allowed: closed at once
        time.sleep(0.1)
        try:
            c.sendall(P.pack_op(P.OP_REQ_DEVLIST))
            self.assertEqual(c.recv(10), b"")
        except ConnectionResetError:
            pass
        c = fake_peer(self.server, "10.1.2.3")             # allowed
        c.sendall(P.pack_op(P.OP_REQ_DEVLIST))
        self.assertEqual(len(c.recv(8)), 8)
        # the server machine itself is always allowed (for the server window)
        self.assertEqual(len(remote_devlist("127.0.0.1", self.server.port)), 2)


if __name__ == "__main__":
    unittest.main()


class SpecCompliance(unittest.TestCase):
    """Replies must be accepted by strict clients (usbip-win2) and by Linux vhci."""

    def setUp(self):
        self.server = UsbIpServer(FakeBackend(), "127.0.0.1", 0)
        self.server.bind()
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.stop()

    def _get_device_descriptor(self, wire_npk):
        c = VhciLikeClient(self.server.port, "1-1")
        self.assertEqual(c.status, 0)
        setup = struct.pack("<BBHHH", 0x80, 6, 0x0100, 0, 18)
        hdr = P.BASIC_HEADER.pack(P.USBIP_CMD_SUBMIT, 7, 0x10002, P.USBIP_DIR_IN, 0)
        hdr += struct.pack(">IiiIi8s", 0x200, 18, 0, wire_npk, 0, setup)
        c.s.sendall(hdr)
        raw = recv_exact(c.s, 48)
        command, seq, devid, direction, ep = P.BASIC_HEADER.unpack_from(raw)
        status, actual, start_frame, npk, err = struct.unpack_from(">iiiIi", raw, 20)
        self.assertEqual(raw[40:48], b"\0" * 8)                     # padding
        self.assertEqual((command, seq, devid, direction, ep), (P.USBIP_RET_SUBMIT, 7, 0, 0, 0))
        self.assertEqual((status, actual, start_frame, err), (0, 18, 0, 0))
        self.assertEqual(recv_exact(c.s, 18), DEVICE_DESC)          # original descriptor
        return npk

    def test_usbip_win2_style_client_gets_0xffffffff(self):
        self.assertEqual(self._get_device_descriptor(0xFFFFFFFF), 0xFFFFFFFF)

    def test_linux_style_client_gets_0(self):
        self.assertEqual(self._get_device_descriptor(0), 0)


class DeviceGone(unittest.TestCase):
    def test_client_is_disconnected_when_device_disappears(self):
        backend = FakeBackend()
        server = UsbIpServer(backend, "127.0.0.1", 0)
        server.bind()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            c = VhciLikeClient(server.port, "1-1")
            c.submit(4, P.USBIP_DIR_IN, 8)
            self.assertEqual(c.recv()[2], -P.ESHUTDOWN)        # error reply still arrives
            c.s.settimeout(3)
            self.assertEqual(c.s.recv(10), b"")                # then the server hangs up
            time.sleep(0.2)
            self.assertTrue(backend.sessions[0].closed)
            self.assertEqual(server.in_use(), {})              # free for a new attach
        finally:
            server.stop()
