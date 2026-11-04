# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Tests for the extended server protocol and the GUI controller (no GUI needed)."""
import os
import subprocess
import tempfile
import threading
import time
import unittest

from usbshare import protocol as P
from usbshare.client import remote_devlist_ext, remote_release
from usbshare.gui_model import Controller, Server, Settings, device_label
from usbshare.server import Assignments, UsbIpServer, recv_exact
from tests.test_e2e import FakeBackend, VhciLikeClient, fake_peer


def start(server):
    server.bind()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class PlainServer(UsbIpServer):
    """Behaves like Linux usbipd: unknown ops just close the connection."""
    def _op_devlist_ext(self, conn, peer):
        raise ConnectionError("unsupported")


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, args, **kw):
        self.calls.append(args[1:])
        out = "succesfully attached to port 3" if "attach" in args else ""
        return subprocess.CompletedProcess(args, 0, out, "")


class ExtProtocol(unittest.TestCase):
    def setUp(self):
        self.srv = start(UsbIpServer(FakeBackend(), "127.0.0.1", 0,
                                     assignments=Assignments({"046d:c52b": ["10.0.0.9"]})))

    def tearDown(self):
        self.srv.stop()

    def test_states_and_release(self):
        holder = VhciLikeClient(self.srv.port, "1-1", source="127.0.0.2")
        self.assertEqual(holder.status, 0)
        data = remote_devlist_ext("127.0.0.1", self.srv.port)
        self.assertTrue(data["ext"])
        self.assertEqual(data["you"], "127.0.0.1")
        by = {d["busid"]: d for d in data["devices"]}
        self.assertEqual((by["1-1"]["state"], by["1-1"]["used_by"]), ("in_use", "127.0.0.2"))
        self.assertEqual(by["1-2"]["state"], "reserved")
        self.assertEqual(by["1-2"]["assigned_to"], ["10.0.0.9/32"])
        self.assertEqual(by["1-1"]["description"], "Fake Loopback")
        # reserved device: hidden from the standard list and import refused
        self.assertEqual(VhciLikeClient(self.srv.port, "1-2").status, P.ST_NA)
        # a third, non-local client may not kick the holder
        s = fake_peer(self.srv, "192.168.7.7")
        s.sendall(P.pack_op(P.OP_REQ_RELEASE) + b"1-1".ljust(32, b"\0"))
        self.assertEqual(P.OP_HEADER.unpack(recv_exact(s, 8))[2], P.ST_NA)
        # local admin (127.0.0.1) may
        self.assertEqual(remote_release("127.0.0.1", self.srv.port, "1-1"), P.ST_OK)
        time.sleep(0.2)
        self.assertTrue(self.srv.backend.sessions[0].closed)
        states = {d["busid"]: d["state"] for d in remote_devlist_ext("127.0.0.1", self.srv.port)["devices"]}
        self.assertEqual(states["1-1"], "available")

    def test_fallback_for_plain_server(self):
        plain = start(PlainServer(FakeBackend(), "127.0.0.1", 0))
        data = remote_devlist_ext("127.0.0.1", plain.port)
        self.assertFalse(data["ext"])
        self.assertEqual([d["busid"] for d in data["devices"]], ["1-1", "1-2"])
        plain.stop()


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.a = start(UsbIpServer(FakeBackend(), "127.0.0.1", 0))
        self.b = start(UsbIpServer(FakeBackend(), "127.0.0.1", 0))
        self.tmp = tempfile.mkdtemp()
        self.settings = Settings(os.path.join(self.tmp, "client.json"))
        self.runner = FakeRunner()
        self.c = Controller(self.settings, usbip="usbip.exe", runner=self.runner)
        self.sa = Server("Werkstatt", "127.0.0.1", self.a.port)
        self.sb = Server("Büro", "127.0.0.1", self.b.port, enabled=False)
        self.c.add_server(self.sa)
        self.c.add_server(self.sb)

    def tearDown(self):
        self.a.stop()
        self.b.stop()

    def test_poll_only_enabled_and_selected(self):
        self.c.poll_once()
        self.assertTrue(self.c.status[self.sa.id].online)
        self.assertNotIn(self.sb.id, self.c.status)
        self.c.selected = self.sb.id         # selecting a server shows its devices
        self.c.poll_once()
        self.assertTrue(self.c.status[self.sb.id].online)
        dead = Server("Aus", "127.0.0.1", 1)
        self.c.add_server(dead)
        self.c.poll_once()
        self.assertFalse(self.c.status[dead.id].online)

    def test_mine_other_and_labels(self):
        mine = VhciLikeClient(self.a.port, "1-1", source="127.0.0.1")
        self.c.poll_once()
        dev = next(d for d in self.c.status[self.sa.id].devices if d["busid"] == "1-1")
        self.assertEqual(self.c.state_of(self.sa.id, dev), "mine")
        mine.s.close()
        time.sleep(0.2)
        other = VhciLikeClient(self.a.port, "1-1", source="127.0.0.2")
        self.c.poll_once()
        dev = next(d for d in self.c.status[self.sa.id].devices if d["busid"] == "1-1")
        self.assertEqual(self.c.state_of(self.sa.id, dev), "other")
        self.assertEqual(device_label(dev), "Fake Loopback")
        self.assertEqual(device_label({"description": "", "class": 0, "interfaces": [[8, 6, 80]]}),
                         "USB-Gerät (Massenspeicher)")
        other.s.close()

    def test_auto_connect_and_detach(self):
        self.c.poll_once()
        dev = next(d for d in self.c.status[self.sa.id].devices if d["busid"] == "1-2")
        self.c.set_auto(self.sa.id, dev, True)
        self.assertTrue(self.c.is_auto(self.sa.id, dev))
        self.c.poll_once()
        self.assertIn(["attach", "-r", "127.0.0.1", "-b", "1-2"],
                      [c[-5:] for c in self.runner.calls])
        self.assertEqual(self.settings.ports[f"{self.sa.id}/1-2"], 3)
        # settings persisted
        self.assertEqual(Settings(self.settings.path).auto, [(self.sa.id, "046d:c52b")])
        self.assertTrue(self.c.detach(self.sa.id, "1-2"))
        self.assertEqual(self.runner.calls[-1], ["detach", "-p", "3"])

    def test_detach_fallback_release(self):
        holder = VhciLikeClient(self.a.port, "1-1", source="127.0.0.1")
        self.assertEqual(holder.status, 0)
        self.assertTrue(self.c.detach(self.sa.id, "1-1"))   # no known port -> server release
        time.sleep(0.2)
        self.assertTrue(self.a.backend.sessions[0].closed)

    def test_remove_server_drops_auto(self):
        self.c.poll_once()
        dev = self.c.status[self.sa.id].devices[0]
        self.c.set_auto(self.sa.id, dev, True)
        self.c.remove_server(self.sa.id)
        self.assertEqual(self.settings.auto, [])
        self.assertEqual([s.name for s in Settings(self.settings.path).servers], ["Büro"])


if __name__ == "__main__":
    unittest.main()
