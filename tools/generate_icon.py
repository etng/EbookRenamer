#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def encode_png(width: int, height: int, rgba: bytes) -> bytes:
    if len(rgba) != width * height * 4:
        raise ValueError("Invalid RGBA buffer size")
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)

    rows = bytearray()
    stride = width * 4
    for y in range(height):
        rows.append(0)  # no filter
        start = y * stride
        rows.extend(rgba[start : start + stride])
    compressed = zlib.compress(bytes(rows), level=9)
    return signature + png_chunk(b"IHDR", ihdr) + png_chunk(b"IDAT", compressed) + png_chunk(b"IEND", b"")


def set_px(buf: bytearray, size: int, x: int, y: int, color: tuple[int, int, int, int]) -> None:
    if x < 0 or y < 0 or x >= size or y >= size:
        return
    idx = (y * size + x) * 4
    buf[idx] = color[0]
    buf[idx + 1] = color[1]
    buf[idx + 2] = color[2]
    buf[idx + 3] = color[3]


def draw_rect(buf: bytearray, size: int, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int, int]) -> None:
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(size, x1), min(size, y1)
    for y in range(y0, y1):
        row = (y * size) * 4
        for x in range(x0, x1):
            i = row + x * 4
            buf[i : i + 4] = bytes(color)


def draw_rounded_rect(
    buf: bytearray,
    size: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    radius: int,
    color: tuple[int, int, int, int],
) -> None:
    rr = radius * radius
    for y in range(y0, y1):
        for x in range(x0, x1):
            cx = x
            cy = y
            inside = False
            if x >= x0 + radius and x < x1 - radius:
                inside = True
            elif y >= y0 + radius and y < y1 - radius:
                inside = True
            else:
                # corner checks
                cxs = x0 + radius if x < x0 + radius else x1 - radius - 1
                cys = y0 + radius if y < y0 + radius else y1 - radius - 1
                dx = cx - cxs
                dy = cy - cys
                inside = dx * dx + dy * dy <= rr
            if inside:
                set_px(buf, size, x, y, color)


def render_icon_rgba(size: int) -> bytes:
    buf = bytearray(size * size * 4)

    # Gradient background
    top = (30, 95, 150, 255)
    bottom = (18, 36, 70, 255)
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(top[0] + (bottom[0] - top[0]) * t)
        g = int(top[1] + (bottom[1] - top[1]) * t)
        b = int(top[2] + (bottom[2] - top[2]) * t)
        for x in range(size):
            set_px(buf, size, x, y, (r, g, b, 255))

    pad = int(size * 0.10)
    panel_r = int(size * 0.14)
    draw_rounded_rect(buf, size, pad, pad, size - pad, size - pad, panel_r, (245, 248, 252, 255))

    inset = int(size * 0.22)
    gap = int(size * 0.02)
    mid = size // 2
    book_r = int(size * 0.04)
    draw_rounded_rect(buf, size, inset, inset, mid - gap, size - inset, book_r, (54, 125, 201, 255))
    draw_rounded_rect(buf, size, mid + gap, inset, size - inset, size - inset, book_r, (37, 98, 166, 255))
    draw_rect(
        buf,
        size,
        mid - max(1, int(size * 0.01)),
        inset + int(size * 0.02),
        mid + max(1, int(size * 0.01)),
        size - inset - int(size * 0.02),
        (230, 236, 244, 255),
    )

    # Small accent square (avoids complex vector dependency)
    s = max(2, int(size * 0.05))
    cx = int(size * 0.73)
    cy = int(size * 0.32)
    draw_rounded_rect(buf, size, cx - s, cy - s, cx + s, cy + s, max(1, s // 4), (255, 205, 72, 255))

    return bytes(buf)


def write_png(path: Path, size: int) -> None:
    rgba = render_icon_rgba(size)
    path.write_bytes(encode_png(size, size, rgba))


def write_ico_from_png(path: Path, png_data: bytes) -> None:
    # Minimal ICO containing one PNG image (256x256).
    reserved = 0
    icon_type = 1
    count = 1
    header = struct.pack("<HHH", reserved, icon_type, count)
    width = 0  # 0 means 256
    height = 0  # 0 means 256
    color_count = 0
    icon_reserved = 0
    planes = 1
    bpp = 32
    size_in_bytes = len(png_data)
    offset = 6 + 16
    entry = struct.pack("<BBBBHHII", width, height, color_count, icon_reserved, planes, bpp, size_in_bytes, offset)
    path.write_bytes(header + entry + png_data)


def build_icns_with_iconutil(png_path: Path, icns_path: Path) -> bool:
    if shutil.which("iconutil") is None or shutil.which("sips") is None:
        return False

    iconset_sizes = [16, 32, 64, 128, 256, 512]
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir(parents=True, exist_ok=True)

        for size in iconset_sizes:
            p1 = iconset / f"icon_{size}x{size}.png"
            p2 = iconset / f"icon_{size}x{size}@2x.png"
            run(["sips", "-z", str(size), str(size), str(png_path), "--out", str(p1)])
            run(["sips", "-z", str(size * 2), str(size * 2), str(png_path), "--out", str(p2)])

        proc = run(["iconutil", "-c", "icns", str(iconset), "-o", str(icns_path)])
        return proc.returncode == 0 and icns_path.exists()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate default app icons for EbookRenamer")
    parser.add_argument("--out-dir", default="assets", help="Output directory for generated icon files")
    parser.add_argument("--app-name", default="EbookRenamer", help="App name (reserved for future use)")
    args = parser.parse_args()

    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    png_path = out_dir / "icon.png"
    ico_path = out_dir / "icon.ico"
    icns_path = out_dir / "icon.icns"

    write_png(png_path, 1024)
    png_256 = encode_png(256, 256, render_icon_rgba(256))
    write_ico_from_png(ico_path, png_256)

    icns_ok = build_icns_with_iconutil(png_path, icns_path)
    if icns_ok:
        print(f"[INFO] Generated: {icns_path}")
    else:
        print("[WARN] icon.icns was not generated (iconutil/sips unavailable).")

    print(f"[INFO] Generated: {png_path}")
    print(f"[INFO] Generated: {ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
