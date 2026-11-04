# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Server window backend: admin commands, config persistence, discovery."""
import os
import tempfile
import threading
import time
import unittest

from usbshare import protocol as P
from usbshare.client import admin_request, discover_servers, remote_devlist
from usbshare.config import ServerConfig
from usbshare.server import UsbIpServer, recv_exact
from tests.test_e2e import FakeBackend, VhciLikeClient, fake_peer


class AdminTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "usbshare.toml")
        cfg = ServerConfig.load(self.path)
        cfg.host, cfg.port = "127.0.0.1", 0
        self.srv = UsbIpServer(FakeBackend(), "127.0.0.1", 0, config=cfg)
        self.srv.bind()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.port = self.srv.port

    def tearDown(self):
        self.srv.stop()

    def a(self, **req):
        return admin_request(req, port=self.port)

    def test_status_lists_everything(self):
        st = self.a(action="status")["status"]
        self.assertEqual([d["busid"] for d in st["devices"]], ["1-1", "1-2"])
        self.assertTrue(all(d["shared"] for d in st["devices"]))
        self.assertEqual(st["config_path"], self.path)

    def test_share_unshare_persists(self):
        r = self.a(action="share", busid="1-2", shared=False)
        self.assertTrue(r["ok"])
        self.assertEqual([d.busid for d in remote_devlist("127.0.0.1", self.port)], ["1-1"])
        self.assertEqual(ServerConfig.load(self.path).exclude, ["046d:c52b"])
        # unshared devices still appear in the window, marked as not shared
        st = {d["busid"]: d for d in r["status"]["devices"]}
        self.assertFalse(st["1-2"]["shared"])
        self.a(action="share", busid="1-2", shared=True)
        self.assertEqual(ServerConfig.load(self.path).exclude, [])
        self.assertEqual(len(remote_devlist("127.0.0.1", self.port)), 2)

    def test_unshare_kicks_current_user(self):
        holder = VhciLikeClient(self.port, "1-1", source="127.0.0.2")
        self.assertEqual(holder.status, 0)
        self.a(action="share", busid="1-1", shared=False)
        time.sleep(0.2)
        self.assertTrue(self.srv.backend.sessions[0].closed)

    def test_assign_and_kick_other_client(self):
        holder = VhciLikeClient(self.port, "1-1", source="127.0.0.2")
        self.assertEqual(holder.status, 0)
        r = self.a(action="assign", busid="1-1", clients=["192.168.1.50"])
        self.assertTrue(r["ok"])
        self.assertEqual(ServerConfig.load(self.path).assign, {"1234:5678": ["192.168.1.50"]})
        time.sleep(0.2)
        # the current user is not in the assignment -> it gets disconnected
        self.assertTrue(self.srv.backend.sessions[0].closed)
        # another client is refused, the assigned one would be accepted
        c = fake_peer(self.srv, "192.168.1.99")
        c.sendall(P.pack_op(P.OP_REQ_IMPORT) + b"1-1".ljust(32, b"\0"))
        self.assertEqual(P.OP_HEADER.unpack(recv_exact(c, 8))[2], P.ST_NA)
        # remove assignment again
        self.a(action="assign", busid="1-1", clients=[])
        self.assertEqual(ServerConfig.load(self.path).assign, {})

    def test_allow_clients_and_seen_clients(self):
        c = fake_peer(self.srv, "192.168.1.77")
        c.sendall(P.pack_op(P.OP_REQ_DEVLIST))
        recv_exact(c, 8)
        r = self.a(action="allow_clients", clients=["192.168.1.50", "10.0.0.0/8"])
        self.assertEqual(ServerConfig.load(self.path).allow_clients, ["192.168.1.50", "10.0.0.0/8"])
        ips = {cl["ip"]: cl for cl in r["status"]["clients"]}
        self.assertTrue(ips["192.168.1.77"]["allowed"])
        c = fake_peer(self.srv, "192.168.1.77")        # now rejected
        time.sleep(0.2)
        ips = {cl["ip"]: cl for cl in self.a(action="status")["status"]["clients"]}
        self.assertFalse(ips["192.168.1.77"]["allowed"])
        # the window on the server machine itself still works
        self.assertTrue(self.a(action="status")["ok"])
        bad = self.a(action="allow_clients", clients=["kein.netz/99"])
        self.assertFalse(bad["ok"])

    def test_admin_refused_from_network(self):
        import json
        c = fake_peer(self.srv, "192.168.1.66")
        body = json.dumps({"action": "share", "busid": "1-1", "shared": False}).encode()
        c.sendall(P.pack_op(P.OP_REQ_ADMIN) + len(body).to_bytes(4, "big") + body)
        self.assertEqual(P.OP_HEADER.unpack(recv_exact(c, 8))[2], P.ST_NA)
        self.assertEqual(ServerConfig.load(self.path).exclude, [])

    def test_manual_edit_is_reloaded(self):
        cfg = ServerConfig.load(self.path)
        cfg.exclude = ["1234:5678"]
        time.sleep(1.1)                    # make sure the mtime changes
        cfg.save()
        for _ in range(30):
            if len(remote_devlist("127.0.0.1", self.port)) == 1:
                break
            time.sleep(0.2)
        self.assertEqual([d.busid for d in remote_devlist("127.0.0.1", self.port)], ["1-2"])

    def test_discovery(self):
        found = discover_servers(self.port, timeout=1.0, targets=["127.0.0.1"])
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["host"], found[0]["port"]), ("127.0.0.1", self.port))
        self.srv.discovery = False
        self.assertEqual(discover_servers(self.port, timeout=0.5, targets=["127.0.0.1"]), [])

    def test_config_roundtrip(self):
        cfg = ServerConfig(path=os.path.join(self.dir, "x.toml"), allow_clients=["1.2.3.4"],
                           exclude=["aaaa:bbbb"], assign={"1-4": ["10.0.0.1", "10.0.0.2"]},
                           usbdk=True, discovery=False)
        cfg.save()
        back = ServerConfig.load(cfg.path)
        for f in ("allow_clients", "exclude", "assign", "usbdk", "discovery", "port", "host"):
            self.assertEqual(getattr(back, f), getattr(cfg, f), f)


if __name__ == "__main__":
    unittest.main()
