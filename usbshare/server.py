# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""TCP server speaking USB/IP. One TCP connection == one exported device."""
from __future__ import annotations

import ipaddress
import json
import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from . import __version__
from . import protocol as P
from .i18n import _
from .backend import Backend, BackendError, DeviceSession
from .config import ServerConfig
from .protocol import DeviceInfo, IsoPacket, SubmitCmd, UrbResult

log = logging.getLogger("usbshare.server")


class ProtocolError(Exception):
    pass


def recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("peer closed connection")
        buf += chunk
    return bytes(buf)


@dataclass
class DeviceFilter:
    """Decides which devices are exported. Entries: 'vid:pid', 'vid:*', or a busid."""
    allow: List[str] = field(default_factory=list)   # empty = everything
    deny: List[str] = field(default_factory=list)

    @staticmethod
    def _match(rule: str, dev: DeviceInfo) -> bool:
        rule = rule.strip().lower()
        if ":" in rule:
            vid, _sep, pid = rule.partition(":")
            return (vid in ("*", f"{dev.id_vendor:04x}")
                    and pid in ("*", f"{dev.id_product:04x}"))
        return rule == dev.busid.lower()

    def permits(self, dev: DeviceInfo) -> bool:
        if any(self._match(r, dev) for r in self.deny):
            return False
        return not self.allow or any(self._match(r, dev) for r in self.allow)


class ClientFilter:
    def __init__(self, networks: Sequence[str] = ()):
        self.networks = [ipaddress.ip_network(n, strict=False) for n in networks]

    def permits(self, addr: str) -> bool:
        if not self.networks:
            return True
        ip = ipaddress.ip_address(addr.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return any(ip in n for n in self.networks)


def _norm_ip(addr: str) -> str:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
        if ip.version == 6 and ip.ipv4_mapped:
            ip = ip.ipv4_mapped
        return str(ip)
    except ValueError:
        return addr


def is_loopback(addr: str) -> bool:
    try:
        return ipaddress.ip_address(_norm_ip(addr)).is_loopback
    except ValueError:
        return False


def device_key(dev: DeviceInfo, all_devs: Sequence[DeviceInfo]) -> str:
    """Rule key for one device: vid:pid, or the busid if another device has the same vid:pid."""
    same = [d for d in all_devs if d.vidpid == dev.vidpid]
    return dev.busid if len(same) > 1 else dev.vidpid


class Assignments:
    """Server-side assignment of devices to clients.

    rules: {"046d:c52b": ["192.168.1.50"], "1-4": ["192.168.1.51/32", ...]}
    A device matched by a rule is only offered to the listed clients."""

    def __init__(self, rules: Optional[Dict[str, Sequence[str]]] = None):
        self.rules = [(r, ClientFilter(list(nets))) for r, nets in (rules or {}).items()]

    def owners(self, dev: DeviceInfo) -> List[str]:
        out: List[str] = []
        for rule, flt in self.rules:
            if DeviceFilter._match(rule, dev):
                out += [str(n) for n in flt.networks]
        return out

    def permits(self, dev: DeviceInfo, peer: str) -> bool:
        matching = [flt for rule, flt in self.rules if DeviceFilter._match(rule, dev)]
        return not matching or any(f.permits(peer) for f in matching)


class UsbIpServer:
    def __init__(self, backend: Backend, host: str = "0.0.0.0", port: int = P.DEFAULT_PORT,
                 device_filter: Optional[DeviceFilter] = None,
                 client_filter: Optional[ClientFilter] = None,
                 assignments: Optional[Assignments] = None,
                 config: Optional[ServerConfig] = None, discovery: bool = False):
        self.backend = backend
        self.host, self.port = host, port
        self.device_filter = device_filter or DeviceFilter()
        self.client_filter = client_filter or ClientFilter()
        self.assignments = assignments or Assignments()
        self._busy: Dict[str, str] = {}       # busid -> client address
        self._conns: Dict[str, socket.socket] = {}   # busid -> URB connection
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._stopping = threading.Event()
        self.on_change: Optional[Callable[[], None]] = None
        self.discovery = discovery
        self._udp: Optional[socket.socket] = None
        self._seen: Dict[str, dict] = {}      # client ip -> {"last": ts, "allowed": bool}
        self._names: Dict[str, str] = {}      # client ip -> reverse DNS name
        self._cfg_lock = threading.Lock()
        self.config = config
        if config is not None:
            self.apply_config(config)

    # -- configuration --------------------------------------------------------
    def apply_config(self, cfg: ServerConfig) -> None:
        self.config = cfg
        self.device_filter = DeviceFilter(allow=list(cfg.only), deny=list(cfg.exclude))
        self.client_filter = ClientFilter(cfg.allow_clients)
        self.assignments = Assignments(cfg.assign)
        self.discovery = cfg.discovery

    def _watch_config(self) -> None:
        last = self.config.mtime() if self.config else 0.0
        while not self._stopping.wait(2.0):
            if not self.config or not self.config.path:
                continue
            m = self.config.mtime()
            if m and m != last:
                last = m
                try:
                    with self._cfg_lock:
                        new = ServerConfig.load(self.config.path)
                        self.apply_config(new)
                    log.info("configuration reloaded from %s", new.path)
                except Exception as e:
                    log.error("cannot reload configuration: %s", e)

    def _change_config(self, fn) -> None:
        """Apply fn(config) and persist it (used by the server window)."""
        with self._cfg_lock:
            cfg = self.config or ServerConfig()
            fn(cfg)
            self.apply_config(cfg)
            if cfg.path:
                cfg.save()

    # -- clients ----------------------------------------------------------------
    def _note_client(self, ip: str, allowed: bool) -> None:
        if is_loopback(ip):
            return
        with self._lock:
            self._seen[ip] = {"last": time.time(), "allowed": allowed}
            unknown = ip not in self._names
            if unknown:
                self._names[ip] = ""
        if unknown:
            threading.Thread(target=self._resolve, args=(ip,), daemon=True).start()

    def _resolve(self, ip: str) -> None:
        try:
            name = socket.gethostbyaddr(ip)[0].split(".")[0]
        except OSError:
            name = ""
        with self._lock:
            self._names[ip] = name

    def client_name(self, ip: Optional[str]) -> str:
        with self._lock:
            return self._names.get(ip or "", "")

    # -- device bookkeeping -------------------------------------------------
    def exportable_devices(self, with_names: bool = False) -> List[DeviceInfo]:
        if with_names:
            with self._lock:
                skip = set(self._busy)
            devs = self.backend.list_devices(with_names=True, skip=skip)
        else:
            devs = self.backend.list_devices()
        return [d for d in devs if self.device_filter.permits(d)]

    def available_devices(self, peer: Optional[str] = None) -> List[DeviceInfo]:
        with self._lock:
            busy = set(self._busy)
        return [d for d in self.exportable_devices() if d.busid not in busy
                and (peer is None or self.assignments.permits(d, peer))]

    def in_use(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._busy)

    def release(self, busid: str) -> bool:
        """Disconnect the client currently using `busid` (the client sees an unplug)."""
        with self._lock:
            conn = self._conns.get(busid)
        if conn is None:
            return False
        try:
            conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        return True

    # -- listening ----------------------------------------------------------
    def bind(self) -> None:
        family = socket.AF_INET6 if ":" in self.host else socket.AF_INET
        s = socket.socket(family, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if family == socket.AF_INET6:
            try:
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
        s.bind((self.host, self.port))
        s.listen(16)
        self._sock = s
        self.port = s.getsockname()[1]
        try:
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            u.bind(("" if ":" in self.host else self.host, self.port))
            u.settimeout(1.0)
            self._udp = u
        except OSError as e:
            log.warning("server discovery disabled (UDP port %d: %s)", self.port, e)

    def serve_forever(self) -> None:
        if self._sock is None:
            self.bind()
        log.info("listening on %s:%d", self.host, self.port)
        threading.Thread(target=self._discovery_loop, name="discovery", daemon=True).start()
        if self.config and self.config.path:
            threading.Thread(target=self._watch_config, name="config-watch", daemon=True).start()
        while not self._stopping.is_set():
            try:
                conn, addr = self._sock.accept()
            except OSError:
                if self._stopping.is_set():
                    break
                raise
            threading.Thread(target=self._handle, args=(conn, _norm_ip(addr[0])),
                             name=f"conn-{addr[0]}", daemon=True).start()

    def _discovery_loop(self) -> None:
        u = self._udp
        if u is None:
            return
        while not self._stopping.is_set():
            try:
                data, addr = u.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                if self._stopping.is_set():
                    return
                continue
            if not data.startswith(P.DISCOVER_MAGIC) or not self.discovery:
                continue
            peer = _norm_ip(addr[0])
            allowed = is_loopback(peer) or self.client_filter.permits(peer)
            self._note_client(peer, allowed)
            if not allowed:
                continue
            reply = json.dumps({"usbshare": 1, "name": socket.gethostname(), "port": self.port,
                                "version": __version__}).encode()
            try:
                u.sendto(reply, addr)
            except OSError:
                pass

    def close_sessions(self, timeout: float = 3.0) -> None:
        """Disconnect all clients and wait until their devices are given back."""
        with self._lock:
            conns = list(self._conns.values())
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if not self._busy:
                    return
            time.sleep(0.05)

    def stop(self) -> None:
        self._stopping.set()
        if self._udp:
            try:
                self._udp.close()
            except OSError:
                pass
        if self._sock:
            try:
                self._sock.close()
            except OSError:
                pass

    # -- connection handling ------------------------------------------------
    def _handle(self, conn: socket.socket, peer: str) -> None:
        allowed = is_loopback(peer) or self.client_filter.permits(peer)
        self._note_client(peer, allowed)
        if not allowed:
            log.warning("rejected connection from %s (not in allow_clients)", peer)
            conn.close()
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        try:
            version, code, _status = P.OP_HEADER.unpack(recv_exact(conn, P.OP_HEADER.size))
            if code == P.OP_REQ_DEVLIST:
                self._op_devlist(conn, peer)
            elif code == P.OP_REQ_IMPORT:
                busid = recv_exact(conn, 32).split(b"\0", 1)[0].decode(errors="replace")
                self._op_import(conn, peer, busid)
            elif code == P.OP_REQ_DEVLIST_EXT:
                self._op_devlist_ext(conn, peer)
            elif code == P.OP_REQ_ADMIN:
                self._op_admin(conn, peer)
            elif code == P.OP_REQ_RELEASE:
                busid = recv_exact(conn, 32).split(b"\0", 1)[0].decode(errors="replace")
                self._op_release(conn, peer, busid)
            else:
                log.warning("%s: unknown op 0x%04x (version 0x%04x)", peer, code, version)
        except (ConnectionError, OSError) as e:
            log.debug("%s: connection ended: %s", peer, e)
        except ProtocolError as e:
            log.warning("%s: protocol error: %s", peer, e)
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _op_devlist(self, conn: socket.socket, peer: str) -> None:
        devs = self.available_devices(peer)
        out = P.pack_op(P.OP_REP_DEVLIST) + len(devs).to_bytes(4, "big")
        out += b"".join(d.pack(with_interfaces=True) for d in devs)
        conn.sendall(out)

    def _op_devlist_ext(self, conn: socket.socket, peer: str) -> None:
        busy = self.in_use()
        items = []
        for d in self.exportable_devices(with_names=True):
            owners = self.assignments.owners(d)
            if d.busid in busy:
                state = "in_use"
            elif not self.assignments.permits(d, peer):
                state = "reserved"
            elif d.description.startswith("[not accessible"):
                state = "no_driver"
            else:
                state = "available"
            items.append({
                "busid": d.busid, "vid": d.id_vendor, "pid": d.id_product,
                "vidpid": d.vidpid, "description": d.description, "speed": d.speed,
                "class": d.device_class, "interfaces": [list(i) for i in d.interfaces],
                "state": state, "used_by": busy.get(d.busid), "assigned_to": owners,
            })
        payload = json.dumps({
            "server": "usbshare", "version": __version__, "hostname": socket.gethostname(),
            "you": peer, "devices": items,
        }).encode()
        conn.sendall(P.pack_op(P.OP_REP_DEVLIST_EXT) + len(payload).to_bytes(4, "big") + payload)

    def _op_release(self, conn: socket.socket, peer: str, busid: str) -> None:
        holder = self.in_use().get(busid)
        local = is_loopback(peer)
        if holder is None:
            status = P.ST_NODEV
        elif holder != peer and not local:
            log.warning("%s tried to release %s held by %s", peer, busid, holder)
            status = P.ST_NA
        else:
            status = P.ST_OK if self.release(busid) else P.ST_NODEV
            log.info("%s released %s", peer, busid)
        conn.sendall(P.pack_op(P.OP_REP_RELEASE, status))

    # -- local administration (server window) --------------------------------
    def admin_status(self) -> dict:
        busy = self.in_use()
        with self._lock:
            skip = set(self._busy)
        devs = self.backend.list_devices(with_names=True, skip=skip)
        out = []
        for d in devs:
            used_by = busy.get(d.busid)
            out.append({
                "busid": d.busid, "vidpid": d.vidpid, "description": d.description,
                "class": d.device_class, "interfaces": [list(i) for i in d.interfaces],
                "shared": self.device_filter.permits(d), "key": device_key(d, devs),
                "assigned_to": self.assignments.owners(d), "used_by": used_by,
                "used_by_name": self.client_name(used_by),
                "no_driver": d.description.startswith("[not accessible"),
            })
        with self._lock:
            seen = dict(self._seen)
            names = dict(self._names)
        using: Dict[str, List[str]] = {}
        for busid, ip in busy.items():
            using.setdefault(ip, []).append(busid)
        clients = [{"ip": ip, "name": names.get(ip, ""), "last_seen": v["last"],
                    "allowed": v["allowed"], "using": using.get(ip, [])}
                   for ip, v in sorted(seen.items(), key=lambda kv: -kv[1]["last"])]
        cfg = self.config or ServerConfig()
        return {"version": __version__, "hostname": socket.gethostname(), "port": self.port,
                "config_path": cfg.path, "config_writable": cfg.writable(),
                "allow_clients": list(cfg.allow_clients), "discovery": self.discovery,
                "devices": out, "clients": clients, "now": time.time()}

    def admin(self, req: dict) -> dict:
        action = req.get("action", "status")
        devs = self.backend.list_devices()
        dev = next((d for d in devs if d.busid == req.get("busid")), None)
        if action in ("share", "assign", "release") and dev is None:
            return {"ok": False, "error": _("Gerät nicht gefunden")}
        try:
            if action == "share":
                key = device_key(dev, devs)
                shared = bool(req.get("shared"))

                def change(cfg: ServerConfig):
                    match = lambda r: DeviceFilter._match(r, dev)  # noqa: E731
                    cfg.exclude = [r for r in cfg.exclude if not match(r)]
                    if shared:
                        if cfg.only and not any(match(r) for r in cfg.only):
                            cfg.only.append(key)
                    else:
                        cfg.only = [r for r in cfg.only if not match(r)]
                        cfg.exclude.append(key)
                self._change_config(change)
                if not shared and dev.busid in self.in_use():
                    self.release(dev.busid)
            elif action == "assign":
                key = device_key(dev, devs)
                clients = [c.strip() for c in req.get("clients", []) if c.strip()]
                ClientFilter(clients)        # validate before saving

                def change(cfg: ServerConfig):
                    cfg.assign = {r: v for r, v in cfg.assign.items()
                                  if not DeviceFilter._match(r, dev)}
                    if clients:
                        cfg.assign[key] = clients
                self._change_config(change)
                holder = self.in_use().get(dev.busid)
                if holder and not self.assignments.permits(dev, holder):
                    self.release(dev.busid)
            elif action == "allow_clients":
                clients = [c.strip() for c in req.get("clients", []) if c.strip()]
                ClientFilter(clients)

                def change(cfg: ServerConfig):
                    cfg.allow_clients = clients
                self._change_config(change)
            elif action == "release":
                self.release(dev.busid)
            elif action != "status":
                return {"ok": False, "error": _("unbekannte Aktion {action}", action=action)}
        except ValueError as e:
            return {"ok": False, "error": _("Ungültige Adresse: {error}", error=e)}
        except OSError as e:
            return {"ok": False, "error": _("Konfiguration konnte nicht gespeichert werden: {error}", error=e),
                    "status": self.admin_status()}
        return {"ok": True, "status": self.admin_status()}

    def _op_admin(self, conn: socket.socket, peer: str) -> None:
        n = int.from_bytes(recv_exact(conn, 4), "big")
        if n > 1024 * 1024:
            raise ProtocolError("admin request too large")
        req = json.loads(recv_exact(conn, n).decode() or "{}")
        if not is_loopback(peer):
            log.warning("%s: admin request refused (only allowed on the server itself)", peer)
            reply, status = {"ok": False, "error": _("nur auf dem Server-Rechner erlaubt")}, P.ST_NA
        else:
            reply, status = self.admin(req), P.ST_OK
        data = json.dumps(reply).encode()
        conn.sendall(P.pack_op(P.OP_REP_ADMIN, status) + len(data).to_bytes(4, "big") + data)

    def _op_import(self, conn: socket.socket, peer: str, busid: str) -> None:
        dev = next((d for d in self.exportable_devices() if d.busid == busid), None)
        if dev is None:
            log.info("%s: import of unknown/filtered device %s", peer, busid)
            conn.sendall(P.pack_op(P.OP_REP_IMPORT, P.ST_NODEV))
            return
        if not self.assignments.permits(dev, peer):
            log.info("%s: device %s is assigned to %s", peer, busid, self.assignments.owners(dev))
            conn.sendall(P.pack_op(P.OP_REP_IMPORT, P.ST_NA))
            return
        with self._lock:
            if busid in self._busy:
                conn.sendall(P.pack_op(P.OP_REP_IMPORT, P.ST_DEV_BUSY))
                log.info("%s: device %s is busy (used by %s)", peer, busid, self._busy[busid])
                return
            self._busy[busid] = peer
        try:
            try:
                session = self.backend.open(busid)
            except BackendError as e:
                log.error("%s: cannot export %s (%s): %s", peer, busid, dev.description, e)
                conn.sendall(P.pack_op(P.OP_REP_IMPORT, e.status))
                return
            log.info("%s attached %s %s %s", peer, busid, dev.vidpid, dev.description)
            with self._lock:
                self._conns[busid] = conn
            self._notify()
            try:
                conn.sendall(P.pack_op(P.OP_REP_IMPORT) + session.info.pack(with_interfaces=False))
                UrbPump(conn, session, peer).run()
            finally:
                session.close()
                log.info("%s detached %s", peer, busid)
        finally:
            with self._lock:
                self._busy.pop(busid, None)
                self._conns.pop(busid, None)
            self._notify()

    def _notify(self) -> None:
        if self.on_change:
            try:
                self.on_change()
            except Exception:  # pragma: no cover
                log.exception("on_change callback failed")


class UrbPump:
    """Runs the URB phase for one imported device."""

    def __init__(self, conn: socket.socket, session: DeviceSession, peer: str):
        self.conn, self.session, self.peer = conn, session, peer
        self._out: "queue.Queue[Optional[bytes]]" = queue.Queue()
        self._lock = threading.Lock()
        self._pending: Dict[int, Tuple[int, int]] = {}  # seqnum -> (direction, client npk)
        self._logged_npk = False
        self._unlinked: Dict[int, int] = {}         # submit seqnum -> unlink seqnum
        self._dead = threading.Event()
        self._gone = False
        self._unlinks = 0

    def run(self) -> None:
        writer = threading.Thread(target=self._writer, name=f"tx-{self.peer}", daemon=True)
        writer.start()
        try:
            self._reader()
        finally:
            self._dead.set()
            self._out.put(None)
            writer.join(timeout=5)

    def _send(self, data: bytes) -> None:
        if not self._dead.is_set():
            self._out.put(data)

    def _writer(self) -> None:
        while True:
            item = self._out.get()
            if item is None:
                return
            try:
                self.conn.sendall(item)
            except OSError:
                self._dead.set()
                try:
                    self.conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                return

    def _reader(self) -> None:
        while not self._dead.is_set():
            hdr = recv_exact(self.conn, P.HEADER_SIZE)
            command, seqnum, devid, direction, ep = P.BASIC_HEADER.unpack_from(hdr)
            if command == P.USBIP_CMD_SUBMIT:
                self._on_submit(hdr, seqnum, devid, direction, ep)
            elif command == P.USBIP_CMD_UNLINK:
                (target,) = P.CMD_UNLINK.unpack_from(hdr, P.BASIC_HEADER.size)
                self._on_unlink(seqnum, target)
            else:
                raise ProtocolError(f"unexpected command 0x{command:08x}")

    def _on_submit(self, hdr: bytes, seqnum: int, devid: int, direction: int, ep: int) -> None:
        flags, buflen, start_frame, npk, interval, setup = P.CMD_SUBMIT.unpack_from(
            hdr, P.BASIC_HEADER.size)
        if buflen < 0 or buflen > 64 * 1024 * 1024:
            raise ProtocolError(f"bad transfer_buffer_length {buflen}")
        data = recv_exact(self.conn, buflen) if direction == P.USBIP_DIR_OUT and buflen else b""
        iso = None
        if npk not in (0, -1) and npk & 0xFFFFFFFF != P.NO_ISO:
            if npk < 0 or npk > 4096:
                raise ProtocolError(f"bad number_of_packets {npk}")
            raw = recv_exact(self.conn, npk * P.ISO_DESC.size)
            iso = [IsoPacket(*P.ISO_DESC.unpack_from(raw, i * P.ISO_DESC.size)) for i in range(npk)]
        cmd = SubmitCmd(seqnum, devid, direction, ep, flags, buflen, start_frame,
                        npk if iso else 0, interval, setup, data, iso)
        wire_npk = -1 if npk == -1 or npk & 0xFFFFFFFF == P.NO_ISO else 0
        if not iso and not self._logged_npk:
            self._logged_npk = True
            log.debug("%s: client sends number_of_packets=%d for non-ISO URBs", self.peer, npk)
        with self._lock:
            self._pending[seqnum] = (direction, wire_npk)
        try:
            self.session.submit(cmd, self._done)
        except Exception:  # never let a backend bug kill the connection silently
            log.exception("%s: submit failed", self.peer)
            self._done(seqnum, UrbResult(status=-P.EPROTO))

    def _done(self, seqnum: int, res: UrbResult) -> None:
        with self._lock:
            pending = self._pending.pop(seqnum, None)
            unlink_seq = self._unlinked.pop(seqnum, None)
        if pending is None:
            return
        direction, wire_npk = pending
        if res.status == -P.ESHUTDOWN and not self._gone:
            # The real device disappeared on the server (unplugged, crashed, re-enumerated).
            # Drop the connection so the client sees an unplug right away and can attach the
            # device again once it is back (instead of keeping a dead device forever).
            self._gone = True
            log.warning("%s: device disappeared on the server – disconnecting client", self.peer)
            threading.Timer(0.3, self._hang_up).start()
        if unlink_seq is not None:
            self._send(P.pack_ret_unlink(unlink_seq, -P.ECONNRESET))
        else:
            self._send(P.pack_ret_submit(seqnum, res, direction, wire_npk))

    def _hang_up(self) -> None:
        try:
            self.conn.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def _on_unlink(self, seqnum: int, target: int) -> None:
        self._unlinks += 1
        if self._unlinks in (1, 10, 100, 1000):
            log.info(_("%s: Client bricht Übertragungen ab (bisher %d×)"), self.peer, self._unlinks)
        with self._lock:
            pending = target in self._pending
            if pending:
                self._unlinked[target] = seqnum
        if pending:
            self.session.cancel(target)
        else:
            # Already completed: the RET_SUBMIT is on its way / was sent.
            self._send(P.pack_ret_unlink(seqnum, 0))
