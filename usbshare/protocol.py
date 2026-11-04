# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""USB/IP wire protocol (compatible with the Linux kernel's vhci-hcd and usbip-win2).

All integers are big-endian. Reference: Documentation/usb/usbip_protocol.rst
in the Linux kernel tree.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

USBIP_VERSION = 0x0111
DEFAULT_PORT = 3240

# Operation codes (connection setup phase)
OP_REQ_DEVLIST = 0x8005
OP_REP_DEVLIST = 0x0005
OP_REQ_IMPORT = 0x8003
OP_REP_IMPORT = 0x0003

# usbshare extensions (ignored by standard USB/IP clients; a standard server just
# closes the connection, so clients fall back to OP_REQ_DEVLIST)
OP_REQ_DEVLIST_EXT = 0x8F05   # reply: header + u32 length + JSON
OP_REP_DEVLIST_EXT = 0x0F05
OP_REQ_RELEASE = 0x8F06       # request: header + busid[32]; reply: header only
OP_REP_RELEASE = 0x0F06
OP_REQ_ADMIN = 0x8F07         # loopback only: header + u32 length + JSON, reply likewise
OP_REP_ADMIN = 0x0F07

DISCOVER_MAGIC = b"USBSHARE-DISCOVER 1"   # UDP broadcast to the server port

ST_OK = 0x00
ST_NA = 0x01          # device not available
ST_DEV_BUSY = 0x02
ST_DEV_ERR = 0x03
ST_NODEV = 0x04
ST_ERROR = 0x05

# URB phase commands
USBIP_CMD_SUBMIT = 0x0001
USBIP_CMD_UNLINK = 0x0002
USBIP_RET_SUBMIT = 0x0003
USBIP_RET_UNLINK = 0x0004

USBIP_DIR_OUT = 0
USBIP_DIR_IN = 1

# URB transfer_flags (Linux values)
URB_SHORT_NOT_OK = 0x0001
URB_ISO_ASAP = 0x0002
URB_ZERO_PACKET = 0x0040

# Linux errno values used as URB status
ENOENT = 2
ENODEV = 19
EPIPE = 32
EOVERFLOW = 75
ECONNRESET = 104
ESHUTDOWN = 108
ETIMEDOUT = 110
EREMOTEIO = 121
EPROTO = 71

# Linux enum usb_device_speed
SPEED_UNKNOWN = 0
SPEED_LOW = 1
SPEED_FULL = 2
SPEED_HIGH = 3
SPEED_WIRELESS = 4
SPEED_SUPER = 5
SPEED_SUPER_PLUS = 6

OP_HEADER = struct.Struct(">HHI")                 # version, code, status
DEVICE_STRUCT = struct.Struct(">256s32sIIIHHHBBBBBB")  # 312 bytes
INTERFACE_STRUCT = struct.Struct(">BBBx")
BASIC_HEADER = struct.Struct(">IIIII")            # command, seqnum, devid, direction, ep
CMD_SUBMIT = struct.Struct(">Iiiii8s")            # flags, buflen, start_frame, npackets, interval, setup
RET_SUBMIT = struct.Struct(">iiiii8x")            # status, actual_len, start_frame, npackets, error_count
CMD_UNLINK = struct.Struct(">I24x")
RET_UNLINK = struct.Struct(">i24x")
ISO_DESC = struct.Struct(">IIIi")                 # offset, length, actual_length, status
HEADER_SIZE = 48

assert DEVICE_STRUCT.size == 312
assert BASIC_HEADER.size + CMD_SUBMIT.size == HEADER_SIZE
assert BASIC_HEADER.size + RET_SUBMIT.size == HEADER_SIZE
assert BASIC_HEADER.size + CMD_UNLINK.size == HEADER_SIZE

NO_ISO = 0xFFFFFFFF


@dataclass
class DeviceInfo:
    busid: str
    busnum: int
    devnum: int
    speed: int                      # Linux usb_device_speed value
    id_vendor: int
    id_product: int
    bcd_device: int
    device_class: int
    device_subclass: int
    device_protocol: int
    configuration_value: int
    num_configurations: int
    interfaces: List[Tuple[int, int, int]] = field(default_factory=list)
    description: str = ""           # human readable, local only

    @property
    def path(self) -> str:
        return f"/usbshare/{self.busid}"

    @property
    def vidpid(self) -> str:
        return f"{self.id_vendor:04x}:{self.id_product:04x}"

    def pack(self, with_interfaces: bool) -> bytes:
        out = DEVICE_STRUCT.pack(
            self.path.encode()[:255], self.busid.encode()[:31],
            self.busnum, self.devnum, self.speed,
            self.id_vendor, self.id_product, self.bcd_device,
            self.device_class, self.device_subclass, self.device_protocol,
            self.configuration_value, self.num_configurations, len(self.interfaces),
        )
        if with_interfaces:
            out += b"".join(INTERFACE_STRUCT.pack(*i) for i in self.interfaces)
        return out

    @classmethod
    def unpack(cls, data: bytes) -> "DeviceInfo":
        (path, busid, busnum, devnum, speed, vid, pid, bcd, dcls, dsub, dproto,
         cfg, ncfg, nif) = DEVICE_STRUCT.unpack(data[:DEVICE_STRUCT.size])
        info = cls(_cstr(busid), busnum, devnum, speed, vid, pid, bcd, dcls, dsub,
                   dproto, cfg, ncfg)
        info._num_interfaces = nif  # type: ignore[attr-defined]
        return info


def _cstr(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode(errors="replace")


@dataclass
class IsoPacket:
    offset: int
    length: int
    actual_length: int = 0
    status: int = 0

    def pack(self) -> bytes:
        return ISO_DESC.pack(self.offset, self.length, self.actual_length, self.status)


@dataclass
class SubmitCmd:
    seqnum: int
    devid: int
    direction: int
    ep: int
    transfer_flags: int
    transfer_buffer_length: int
    start_frame: int
    number_of_packets: int
    interval: int
    setup: bytes
    data: bytes = b""
    iso_packets: Optional[List[IsoPacket]] = None

    @property
    def endpoint_address(self) -> int:
        return (self.ep & 0x0F) | (0x80 if self.direction == USBIP_DIR_IN else 0)


@dataclass
class UrbResult:
    status: int
    actual_length: int = 0
    data: bytes = b""                         # IN data (for ISO: packed, actual lengths only)
    iso_packets: Optional[List[IsoPacket]] = None
    start_frame: int = 0
    error_count: int = 0


def pack_op(code: int, status: int = ST_OK) -> bytes:
    return OP_HEADER.pack(USBIP_VERSION, code, status)


def pack_ret_submit(seqnum: int, res: UrbResult, direction: int, non_iso_npk: int = 0) -> bytes:
    """non_iso_npk: number_of_packets for non-ISO replies. The spec says 0xffffffff (-1);
    usbip-win2 sends -1 and expects it back, the Linux vhci sends 0 and only accepts
    0..sent. So the server echoes whatever the client put into the CMD_SUBMIT."""
    npk = len(res.iso_packets) if res.iso_packets is not None else non_iso_npk
    body = res.data if direction == USBIP_DIR_IN else b""
    actual = res.actual_length
    if direction == USBIP_DIR_IN and res.iso_packets is None and actual != len(body):
        # Never announce more (or less) data than follows: that would desynchronise the
        # stream and the client would misread every following reply.
        actual = len(body)
    hdr = BASIC_HEADER.pack(USBIP_RET_SUBMIT, seqnum, 0, 0, 0)
    hdr += RET_SUBMIT.pack(res.status, actual, res.start_frame, npk, res.error_count)
    if res.iso_packets:
        body += b"".join(p.pack() for p in res.iso_packets)
    return hdr + body


def pack_ret_unlink(seqnum: int, status: int) -> bytes:
    return BASIC_HEADER.pack(USBIP_RET_UNLINK, seqnum, 0, 0, 0) + RET_UNLINK.pack(status)


def pack_cmd_submit(cmd: SubmitCmd) -> bytes:
    """Client side helper (used by tests and tools)."""
    npk = len(cmd.iso_packets) if cmd.iso_packets is not None else 0
    out = BASIC_HEADER.pack(USBIP_CMD_SUBMIT, cmd.seqnum, cmd.devid, cmd.direction, cmd.ep)
    out += CMD_SUBMIT.pack(cmd.transfer_flags, cmd.transfer_buffer_length,
                           cmd.start_frame, npk, cmd.interval, cmd.setup.ljust(8, b"\0"))
    if cmd.direction == USBIP_DIR_OUT:
        out += cmd.data
    if cmd.iso_packets:
        out += b"".join(p.pack() for p in cmd.iso_packets)
    return out


def pack_cmd_unlink(seqnum: int, devid: int, unlink_seqnum: int) -> bytes:
    return BASIC_HEADER.pack(USBIP_CMD_UNLINK, seqnum, devid, 0, 0) + CMD_UNLINK.pack(unlink_seqnum)
