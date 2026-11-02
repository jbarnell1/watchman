"""Manifest-driven preprocessing, the same on every platform.

Classifier entries: resize the whole crop to size x size with the named filter (no crop), then
(pixel / scale - mean) / std per channel, laid out as the manifest says.
Detector entries (resize "letterbox"): scale the image to fit inside size x size keeping aspect,
pad the rest with 114 gray, same normalisation. `letterbox()` also returns what is needed to map
boxes back to the original image.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

FILTERS = {"bilinear": Image.Resampling.BILINEAR, "bicubic": Image.Resampling.BICUBIC}
PAD = 114


@dataclass
class Letterbox:
    scale: float
    pad_x: int
    pad_y: int


def normalise(arr: np.ndarray, spec: dict) -> np.ndarray:
    """arr is HWC uint8 in the manifest's colour order. Returns the model's input tensor (1,...)."""
    x = arr.astype(np.float32) / float(spec["scale"])
    x = (x - np.array(spec["mean"], dtype=np.float32)) / np.array(spec["std"], dtype=np.float32)
    if spec["layout"] == "NCHW":
        x = x.transpose(2, 0, 1)
    return x[None, ...]


def to_colour(image: Image.Image, spec: dict) -> Image.Image:
    img = image.convert("RGB")
    if spec["color"] == "BGR":
        r, g, b = img.split()
        img = Image.merge("RGB", (b, g, r))
    return img


def classifier_input(image: Image.Image, spec: dict) -> np.ndarray:
    size = int(spec["size"])
    img = to_colour(image, spec).resize((size, size), FILTERS[spec["resize"]])
    return normalise(np.asarray(img), spec)


def letterbox(image: Image.Image, spec: dict) -> tuple[np.ndarray, Letterbox]:
    size = int(spec["size"])
    img = to_colour(image, spec)
    w, h = img.size
    scale = min(size / w, size / h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = img.resize((nw, nh), Image.Resampling.BILINEAR)
    canvas = Image.new("RGB", (size, size), (PAD, PAD, PAD))
    pad_x, pad_y = (size - nw) // 2, (size - nh) // 2
    canvas.paste(resized, (pad_x, pad_y))
    return normalise(np.asarray(canvas), spec), Letterbox(scale=scale, pad_x=pad_x, pad_y=pad_y)


def prepare(image: Image.Image, spec: dict) -> tuple[np.ndarray, Letterbox | None]:
    if spec["resize"] == "letterbox":
        return letterbox(image, spec)
    return classifier_input(image, spec), None
