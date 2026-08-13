"""Generate the application icons.

    uv run python tools/make_icon.py

Writes `assets/pushtotalk.ico` (idle) and `assets/pushtotalk-rec.ico` (recording) with
every size Windows asks for. Done in code rather than shipping a binary asset so the
colours and the glyph stay editable, and so there is nothing in the repo nobody can open.

The glyph is a microphone, drawn from distance fields at 4x and box-filtered down, which
is what gives it clean edges at 16x16 without any image library. The only writing here is
the ICO container itself: a directory of 32-bit BGRA DIBs, bottom-up, each followed by the
1-bit AND mask the format still requires even when the alpha channel carries the shape.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
SUPERSAMPLE = 4

# Solid, mid-tone colours: both read against a light and a dark taskbar.
IDLE = (0x3B, 0x82, 0xF6)  # blue
REC = (0xE5, 0x3A, 0x3A)  # red


def _coverage(size: int, color: tuple[int, int, int]) -> np.ndarray:
    """Render the microphone at `size`, returning an RGBA uint8 array."""
    n = size * SUPERSAMPLE
    # Normalised coordinates, 0..1 across the icon, y downwards.
    y, x = np.mgrid[0:n, 0:n] / n + 0.5 / n

    def rounded_rect(cx, cy, half_w, half_h, radius):
        dx = np.abs(x - cx) - (half_w - radius)
        dy = np.abs(y - cy) - (half_h - radius)
        dx = np.maximum(dx, 0.0)
        dy = np.maximum(dy, 0.0)
        return np.hypot(dx, dy) <= radius

    def ring(cx, cy, r_outer, r_inner, keep_below):
        d = np.hypot(x - cx, y - cy)
        band = (d <= r_outer) & (d >= r_inner)
        return band & (y >= keep_below)

    # Capsule: the microphone head.
    head = rounded_rect(0.5, 0.335, 0.135, 0.225, 0.135)
    # Cradle: the U around its lower half. Kept deliberately thick -- at 16x16 a
    # hairline arc disappears into the taskbar, and 16x16 is the size that matters.
    cradle = ring(0.5, 0.50, 0.270, 0.185, 0.50)
    # Stem and base.
    stem = rounded_rect(0.5, 0.775, 0.045, 0.085, 0.040)
    base = rounded_rect(0.5, 0.858, 0.180, 0.038, 0.038)

    mask = (head | cradle | stem | base).astype(np.float32)
    # Box-filter the supersampled mask down to the requested size.
    alpha = mask.reshape(size, SUPERSAMPLE, size, SUPERSAMPLE).mean(axis=(1, 3))

    rgba = np.zeros((size, size, 4), dtype=np.uint8)
    rgba[..., 0] = color[0]
    rgba[..., 1] = color[1]
    rgba[..., 2] = color[2]
    rgba[..., 3] = np.clip(alpha * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return rgba


def _dib(rgba: np.ndarray) -> bytes:
    """One ICO image: BITMAPINFOHEADER + bottom-up BGRA + a zeroed AND mask."""
    height, width = rgba.shape[:2]
    bgra = rgba[::-1, :, [2, 1, 0, 3]]  # flip vertically, RGBA -> BGRA
    pixels = np.ascontiguousarray(bgra).tobytes()
    # 1 bit per pixel, rows padded to 4 bytes. All zero: the alpha channel is the shape,
    # but the mask must still be there and the right size or the icon renders as garbage.
    mask_stride = ((width + 31) // 32) * 4
    mask = b"\x00" * (mask_stride * height)
    header = struct.pack(
        "<IiiHHIIiiII",
        40,                      # biSize
        width,
        height * 2,              # biHeight covers the colour data and the mask
        1,                       # biPlanes
        32,                      # biBitCount
        0,                       # biCompression = BI_RGB
        len(pixels) + len(mask),  # biSizeImage
        0, 0, 0, 0,
    )
    return header + pixels + mask


def write_ico(path: Path, color: tuple[int, int, int]) -> None:
    images = [_dib(_coverage(size, color)) for size in SIZES]
    count = len(images)
    offset = 6 + 16 * count
    directory = b""
    for size, data in zip(SIZES, images, strict=True):
        directory += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,  # 0 means 256 in this field
            size if size < 256 else 0,
            0,      # colours in palette
            0,      # reserved
            1,      # planes
            32,     # bits per pixel
            len(data),
            offset,
        )
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join([struct.pack("<HHH", 0, 1, count), directory, *images]))
    print(f"{path}  {path.stat().st_size:,} bytes, {count} sizes")


def main() -> int:
    assets = Path(__file__).resolve().parent.parent / "assets"
    write_ico(assets / "pushtotalk.ico", IDLE)
    write_ico(assets / "pushtotalk-rec.ico", REC)
    return 0


if __name__ == "__main__":
    sys.exit(main())
