"""The two blur levels a partner may see (ADR-004, ADR-029, ADR-031).

Every crop is first scaled to OUTPUT_SIZE, up or down, so a 54 px avatar and a 500 px photo get the
same blur relative to what they show. The default view (stored as "heavy") is a heavy Gaussian blur:
colour and rough shape only. The peek (stored as "medium") is a light blur shown for at most three
seconds, enough to tell a false alarm from explicit content at a glance. Both are written as WebP at
a fixed output size so file size says nothing about the original.
"""

from __future__ import annotations

import io

from PIL import Image, ImageFilter

OUTPUT_SIZE = 256
DEFAULT_RADIUS = OUTPUT_SIZE / 12   # heavy: about 21 px on 256; shape and colour only
PEEK_RADIUS = OUTPUT_SIZE / 32      # light: 8 px on 256, a little clearer than the old default


def _fit(image: Image.Image) -> Image.Image:
    img = image.convert("RGB")
    w, h = img.size
    scale = OUTPUT_SIZE / max(w, h)
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)


def heavy(image: Image.Image) -> Image.Image:
    """The default view: a heavy blur."""
    return _fit(image).filter(ImageFilter.GaussianBlur(radius=DEFAULT_RADIUS))


def medium(image: Image.Image) -> Image.Image:
    """The timed peek: a light blur."""
    return _fit(image).filter(ImageFilter.GaussianBlur(radius=PEEK_RADIUS))


def to_webp(image: Image.Image, quality: int = 70) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()
