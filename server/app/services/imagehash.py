"""The contract's dHash (docs/api-contract.md, "Image hash") for the server, with numpy, plus the
in-memory verdict cache the judge endpoint keeps so one borderline crop is scored once (ADR-023).
Nothing here touches disk."""

from __future__ import annotations

from collections import OrderedDict

import numpy as np
from PIL import Image

W, H = 9, 8


def dhash(image: Image.Image) -> str:
    gray = np.asarray(image.convert("RGB"), dtype=np.float64)
    gray = 0.299 * gray[:, :, 0] + 0.587 * gray[:, :, 1] + 0.114 * gray[:, :, 2]
    small = _area_average(gray)
    cells = np.floor(small + 0.5).astype(np.int64)
    bits = 0
    for row in range(H):
        for col in range(W - 1):
            if cells[row, col] > cells[row, col + 1]:
                bits |= 1 << (63 - (row * 8 + col))
    return f"{bits:016x}"


def _area_average(gray: np.ndarray) -> np.ndarray:
    """Box filter to 9 by 8 with fractional coverage at cell edges, the reference algorithm."""
    in_h, in_w = gray.shape
    out = np.zeros((H, W))
    ys = _weights(in_h, H)
    xs = _weights(in_w, W)
    for oy in range(H):
        for ox in range(W):
            w = np.outer(ys[oy], xs[ox])
            out[oy, ox] = float((gray * w).sum() / w.sum())
    return out


def _weights(n_in: int, n_out: int) -> list[np.ndarray]:
    rows = []
    for o in range(n_out):
        a = o * n_in / n_out
        b = (o + 1) * n_in / n_out
        idx = np.arange(n_in)
        cover = np.clip(np.minimum(idx + 1, b) - np.maximum(idx, a), 0, None)
        rows.append(cover)
    return rows


class JudgeCache:
    """Hash to (score, verdict), bounded. Two devices asking about the same crop pay one inference."""

    def __init__(self, limit: int = 4096) -> None:
        self.limit = limit
        self._map: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self.hits = 0

    def get(self, key: str) -> tuple[float, str] | None:
        v = self._map.get(key)
        if v is not None:
            self._map.move_to_end(key)
            self.hits += 1
        return v

    def put(self, key: str, value: tuple[float, str]) -> None:
        self._map[key] = value
        self._map.move_to_end(key)
        while len(self._map) > self.limit:
            self._map.popitem(last=False)

    def __len__(self) -> int:
        return len(self._map)
