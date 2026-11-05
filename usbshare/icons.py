# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""App icons drawn with Pillow. Only used to (re)generate usbshare/assets/ –
the programs themselves load the pre-rendered files and need no image library."""
from __future__ import annotations

from PIL import Image, ImageDraw

COLORS = {"client": ((37, 99, 235), (29, 78, 216)),      # blue
          "server": ((13, 148, 136), (15, 118, 110))}    # teal
BADGE = {"ok": (34, 197, 94), "warn": (245, 158, 11), "off": (148, 163, 184)}


def make_icon(kind: str = "client", size: int = 256, badge: str = "") -> Image.Image:
    """Rounded square with a white USB trident. badge: '', 'ok', 'warn', 'off'."""
    s = 4                                   # supersampling for smooth edges
    S = size * s
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    top, bottom = COLORS.get(kind, COLORS["client"])
    r = int(S * 0.22)
    # vertical gradient inside a rounded rectangle
    grad = Image.new("RGBA", (S, S))
    gd = ImageDraw.Draw(grad)
    for y in range(S):
        t = y / (S - 1)
        gd.line([(0, y), (S, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=r, fill=255)
    img.paste(grad, (0, 0), mask)

    # USB trident
    w = max(int(S * 0.06), 2)
    white = (255, 255, 255, 255)
    cx = S // 2
    y0, y1 = int(S * 0.20), int(S * 0.80)
    d.line([(cx, y0 + int(S * 0.08)), (cx, y1 - int(S * 0.06))], fill=white, width=w)
    a = int(S * 0.075)                              # arrow head on top
    d.polygon([(cx, y0), (cx - a, y0 + int(a * 1.5)), (cx + a, y0 + int(a * 1.5))], fill=white)
    cr = int(S * 0.075)                             # round foot
    d.ellipse([cx - cr, y1 - cr * 2, cx + cr, y1], fill=white)
    # left branch with circle
    yl = int(S * 0.47)
    lx = cx - int(S * 0.20)
    d.line([(cx, int(S * 0.62)), (lx, yl), (lx, int(S * 0.38))], fill=white, width=w, joint="curve")
    lr = int(S * 0.055)
    d.ellipse([lx - lr, int(S * 0.38) - lr * 2, lx + lr, int(S * 0.38)], fill=white)
    # right branch with square
    yr = int(S * 0.52)
    rx = cx + int(S * 0.20)
    d.line([(cx, int(S * 0.67)), (rx, yr), (rx, int(S * 0.42))], fill=white, width=w, joint="curve")
    q = int(S * 0.05)
    d.rectangle([rx - q, int(S * 0.42) - q * 2, rx + q, int(S * 0.42)], fill=white)

    if badge:
        br = int(S * 0.20)
        bx, by = S - br - int(S * 0.02), S - br - int(S * 0.02)
        d.ellipse([bx - br - w, by - br - w, bx + br + w, by + br + w], fill=(255, 255, 255, 255))
        d.ellipse([bx - br, by - br, bx + br, by + br], fill=BADGE[badge] + (255,))
    return img.resize((size, size), Image.LANCZOS)


def save_ico(kind: str, path: str) -> None:
    make_icon(kind, 256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                           (128, 128), (256, 256)])




def write_assets(folder: str) -> None:
    """Pre-render all icons as files, so the programs need no image library at runtime."""
    import os
    os.makedirs(folder, exist_ok=True)
    for kind in ("client", "server"):
        for size in (16, 32, 40, 48, 256):
            make_icon(kind, size).save(os.path.join(folder, f"{kind}-{size}.png"), optimize=True)
        for badge in ("", "ok", "warn"):
            name = f"{kind}{'-' + badge if badge else ''}.ico"
            make_icon(kind, 256, badge).save(os.path.join(folder, name),
                                             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (256, 256)])


if __name__ == "__main__":   # python -m usbshare.icons  ->  regenerate usbshare/assets
    import os
    write_assets(os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets"))
