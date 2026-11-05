# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Client-side helpers: query servers, discovery, locate the usbip tool."""
from __future__ import annotations

import logging
import os
import shutil
import socket
import sys
import time
from typing import Dict, List, Optional, Tuple

from . import protocol as P
from .protocol import DeviceInfo
from .server import recv_exact

log = logging.getLogger("usbshare.client")


def remote_devlist(host: str, port: int = P.DEFAULT_PORT, timeout: float = 5.0) -> List[DeviceInfo]:
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(P.pack_op(P.OP_REQ_DEVLIST))
        _ver, code, status = P.OP_HEADER.unpack(recv_exact(s, P.OP_HEADER.size))
        if code != P.OP_REP_DEVLIST or status != P.ST_OK:
            raise ConnectionError(f"bad devlist reply code=0x{code:04x} status={status}")
        n = int.from_bytes(recv_exact(s, 4), "big")
        devs = []
        for _ in range(n):
            info = DeviceInfo.unpack(recv_exact(s, P.DEVICE_STRUCT.size))
            raw = recv_exact(s, 4 * info._num_interfaces)  # type: ignore[attr-defined]
            info.interfaces = [P.INTERFACE_STRUCT.unpack_from(raw, i * 4)
                               for i in range(info._num_interfaces)]  # type: ignore[attr-defined]
            devs.append(info)
        return devs


def find_usbip_tool() -> Optional[str]:
    for name in ("usbip", "usbip.exe"):
        p = shutil.which(name)
        if p:
            return p
    if sys.platform.startswith("win"):
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
            p = os.path.join(base, "USBip", "usbip.exe")
            if os.path.exists(p):
                return p
    for p in ("/usr/sbin/usbip", "/usr/bin/usbip", "/sbin/usbip"):
        if os.path.exists(p):
            return p
    return None


def parse_server(spec: str, default_port: int = P.DEFAULT_PORT) -> Tuple[str, int]:
    """'host', 'host:port', '[v6addr]:port' -> (host, port)"""
    if spec.startswith("["):
        host, _, rest = spec[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") else default_port
    if spec.count(":") == 1:
        host, port = spec.split(":")
        return host, int(port)
    return spec, default_port


# -- usbshare extensions -------------------------------------------------------
def remote_devlist_ext(host: str, port: int = P.DEFAULT_PORT, timeout: float = 4.0) -> dict:
    """Full device list incl. busy/reserved devices and names (usbshare servers).
    Falls back to the standard list for plain USB/IP servers (ext=False)."""
    import json
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(P.pack_op(P.OP_REQ_DEVLIST_EXT))
            _ver, code, status = P.OP_HEADER.unpack(recv_exact(s, P.OP_HEADER.size))
            if code != P.OP_REP_DEVLIST_EXT or status != P.ST_OK:
                raise ConnectionError("no ext support")
            n = int.from_bytes(recv_exact(s, 4), "big")
            if n > 16 * 1024 * 1024:
                raise ConnectionError("reply too large")
            data = json.loads(recv_exact(s, n).decode())
            data["ext"] = True
            return data
    except ConnectionError:
        pass   # plain USB/IP server closed the connection: use the standard list
    devs = remote_devlist(host, port, timeout)
    return {"server": "usbip", "ext": False, "you": None, "devices": [
        {"busid": d.busid, "vid": d.id_vendor, "pid": d.id_product, "vidpid": d.vidpid,
         "description": "", "speed": d.speed, "class": d.device_class,
         "interfaces": [list(i) for i in d.interfaces], "state": "available",
         "used_by": None, "assigned_to": []} for d in devs]}


def remote_release(host: str, port: int, busid: str, timeout: float = 4.0) -> int:
    """Ask a usbshare server to disconnect whoever uses busid. Returns ST_* status."""
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(P.pack_op(P.OP_REQ_RELEASE) + busid.encode()[:31].ljust(32, b"\0"))
        _ver, code, status = P.OP_HEADER.unpack(recv_exact(s, P.OP_HEADER.size))
        if code != P.OP_REP_RELEASE:
            raise ConnectionError("server does not support release")
        return status


def admin_request(req: dict, port: int = P.DEFAULT_PORT, host: str = "127.0.0.1",
                  timeout: float = 8.0) -> dict:
    """Talk to the local server (server window). Only works on the server machine."""
    import json
    data = json.dumps(req).encode()
    with socket.create_connection((host, port), timeout=timeout) as s:
        s.sendall(P.pack_op(P.OP_REQ_ADMIN) + len(data).to_bytes(4, "big") + data)
        _ver, code, status = P.OP_HEADER.unpack(recv_exact(s, P.OP_HEADER.size))
        if code != P.OP_REP_ADMIN:
            raise ConnectionError("server does not support administration (old version?)")
        n = int.from_bytes(recv_exact(s, 4), "big")
        return json.loads(recv_exact(s, n).decode())


def _broadcast_addresses() -> List[str]:
    """255.255.255.255 plus the /24 broadcast of every local IPv4 address."""
    addrs = {"255.255.255.255"}
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                addrs.add(ip.rsplit(".", 1)[0] + ".255")
    except OSError:
        pass
    return sorted(addrs)


def discover_servers(port: int = P.DEFAULT_PORT, timeout: float = 1.5,
                     targets: Optional[List[str]] = None) -> List[dict]:
    """Find usbshare servers in the local network that accept this client."""
    import json
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.bind(("", 0))
    found: Dict[Tuple[str, int], dict] = {}
    try:
        for addr in targets or _broadcast_addresses():
            try:
                s.sendto(P.DISCOVER_MAGIC, (addr, port))
            except OSError:
                pass
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            s.settimeout(left)
            try:
                data, (ip, _p) = s.recvfrom(2048)
            except socket.timeout:
                break
            except OSError:
                continue
            try:
                info = json.loads(data.decode())
            except ValueError:
                continue
            if info.get("usbshare") != 1:
                continue
            info["host"] = ip
            found[(ip, int(info.get("port", port)))] = info
    finally:
        s.close()
    return sorted(found.values(), key=lambda i: (i.get("name", ""), i["host"]))
