"""The reference image hash every client must match (docs/api-contract.md, "Image hash").

64-bit difference hash: grayscale with 0.299 R + 0.587 G + 0.114 B, area-average down to 9 wide by
8 high, round each cell to the nearest integer (half up), then for each row set bit row*8 + col
when cell col is brighter than cell col + 1. Rounding first keeps flat regions identical across
platforms whose floating point sums differ in the last digit.
Big-endian, 16 lowercase hex digits. Two hashes match at Hamming distance 6 or less.

This file is pure Python on purpose: it defines the result, and the test vectors under
test-vectors/ are generated from it so the extension, Windows, Android and iOS tests can check
their own implementations against the same images.
"""

from __future__ import annotations

from collections.abc import Sequence

W, H = 9, 8


def gray(rgb: Sequence[int]) -> float:
    r, g, b = rgb
    return 0.299 * r + 0.587 * g + 0.114 * b


def area_average(pixels: list[list[float]], out_w: int = W, out_h: int = H) -> list[list[float]]:
    """Downscale a grayscale grid by area averaging with fractional coverage (a box filter)."""
    in_h = len(pixels)
    in_w = len(pixels[0])
    out = [[0.0] * out_w for _ in range(out_h)]
    for oy in range(out_h):
        y0 = oy * in_h / out_h
        y1 = (oy + 1) * in_h / out_h
        for ox in range(out_w):
            x0 = ox * in_w / out_w
            x1 = (ox + 1) * in_w / out_w
            total = 0.0
            area = 0.0
            for y in range(int(y0), min(in_h, int(-(-y1 // 1)))):
                cy = min(y + 1, y1) - max(y, y0)
                if cy <= 0:
                    continue
                for x in range(int(x0), min(in_w, int(-(-x1 // 1)))):
                    cx = min(x + 1, x1) - max(x, x0)
                    if cx <= 0:
                        continue
                    total += pixels[y][x] * cx * cy
                    area += cx * cy
            out[oy][ox] = total / area
    return out


def dhash_from_gray(small: list[list[float]]) -> str:
    bits = 0
    cells = [[int(v + 0.5) for v in row] for row in small]
    for row in range(H):
        for col in range(W - 1):
            if cells[row][col] > cells[row][col + 1]:
                bits |= 1 << (63 - (row * 8 + col))
    return f"{bits:016x}"


def dhash(width: int, height: int, rgb_rows: Sequence[Sequence[Sequence[int]]]) -> str:
    """rgb_rows[y][x] = (r, g, b). Works for any size, including sizes under 9 by 8."""
    pixels = [[gray(rgb_rows[y][x]) for x in range(width)] for y in range(height)]
    return dhash_from_gray(area_average(pixels))


def dhash_image(image) -> str:
    """Pillow convenience wrapper."""
    img = image.convert("RGB")
    w, h = img.size
    data = list(img.getdata())
    rows = [data[y * w:(y + 1) * w] for y in range(h)]
    return dhash(w, h, rows)


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def matches(a: str, b: str, tolerance: int = 6) -> bool:
    return hamming(a, b) <= tolerance
