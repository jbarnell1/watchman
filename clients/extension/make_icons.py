"""Rasterise docs/brand/mark.svg's shapes into the icon PNGs. Pillow cannot render SVG, so the
mark's polygons are drawn directly at 8x and downsampled. Run once; the PNGs are committed."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "icons"
INK = (11, 17, 20, 255)
HUE = (21, 109, 122, 255)
WHITE = (255, 255, 255, 255)

# From docs/brand/mark.svg, viewBox 0 0 64 64.
SHAPES = [
    (INK, [(4, 22), (15.3, 22), (26.4, 56.6), (15.3, 56.6)]),
    (INK, [(48.7, 22), (60, 22), (48.7, 56.6), (37.6, 56.6)]),
    (HUE, [(28.9, 22), (35.1, 22), (48.7, 56.6), (37.6, 56.6), (32, 37), (26.4, 56.6), (15.3, 56.6)]),
    (INK, [(30.2, 17.6), (33.8, 17.6), (34.2, 22.4), (29.8, 22.4)]),
    (INK, [(28.8, 16.5), (35.2, 16.5), (35.2, 17.9), (28.8, 17.9)]),
    (INK, [(30.4, 13.4), (33.6, 13.4), (33.6, 16.6), (30.4, 16.6)]),
    (INK, [(29.3, 13.5), (34.7, 13.5), (32, 11.3)]),
    (INK, [(31.6, 9.9), (32.4, 9.9), (32.4, 11.5), (31.6, 11.5)]),
    (WHITE, [(31.3, 14.2), (32.7, 14.2), (32.7, 16.1), (31.3, 16.1)]),
]


def render(size: int, background: tuple | None = None) -> Image.Image:
    s = 8
    big = Image.new("RGBA", (64 * s, 64 * s), background or (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    for colour, pts in SHAPES:
        d.polygon([(x * s, y * s) for x, y in pts], fill=colour)
    return big.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    for size in (16, 32, 48, 128, 512):
        render(size).save(OUT / f"mark-{size}.png")
    print("icons written to", OUT)


if __name__ == "__main__":
    main()
