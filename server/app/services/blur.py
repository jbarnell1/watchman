"""The two blur levels a partner may see (ADR-004, softened 2026-11-02).

Every crop is first scaled to OUTPUT_SIZE, up or down, so a 54 px avatar and a 500 px photo get the
same blur relative to what they show. The default view (stored as "heavy" for continuity) is a
medium Gaussian blur: colours, shapes and whether it is a face, a body or a screenshot of text stay
readable; detail does not. The peek (stored as "medium") is a light blur, enough to tell a false
alarm from explicit content at a glance. The original squeeze to a few pixels made small crops
unreadable, so partners could not tell what they were looking at. Both are written as WebP at a fixed
output size so file size says nothing about the original.
"""

from __future__ import annotations

import io

from PIL import Image, ImageFilter

OUTPUT_SIZE = 256
DEFAULT_RADIUS = OUTPUT_SIZE / 24   # medium: about 11 px on 256
PEEK_RADIUS = OUTPUT_SIZE / 64      # light: 4 px on 256


def _fit(image: Image.Image) -> Image.Image:
    img = image.convert("RGB")
    w, h = img.size
    scale = OUTPUT_SIZE / max(w, h)
    return img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)


def heavy(image: Image.Image) -> Image.Image:
    """The default view: a medium blur."""
    return _fit(image).filter(ImageFilter.GaussianBlur(radius=DEFAULT_RADIUS))


def medium(image: Image.Image) -> Image.Image:
    """The timed peek: a light blur."""
    return _fit(image).filter(ImageFilter.GaussianBlur(radius=PEEK_RADIUS))


def to_webp(image: Image.Image, quality: int = 70) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="WEBP", quality=quality)
    return buf.getvalue()
