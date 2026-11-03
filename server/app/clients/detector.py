"""The server's own nudity detector (ADR-046): which body parts a reported picture shows, exposed or
covered, so the server does not rely on the phone's smaller detector having run. One interface, an
ONNX implementation of the manifest's detector_large entry (NudeNet 640m) and a fake.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

# Bare breast, genitals, buttocks and anus: what reaches partners when a covered picture shows it.
# The manifest's explicit_labels override this when the model is loaded.
EXPLICIT = frozenset({
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED", "BUTTOCKS_EXPOSED",
})
# Labels below this confidence are not kept: they say nothing and only fill the record.
KEEP = 0.25
PAD = 114


class Detector:
    explicit: frozenset[str] = EXPLICIT

    def labels(self, image: Image.Image) -> dict[str, float]:
        """The best confidence per label found in the image, for labels at KEEP or above."""
        raise NotImplementedError


class FakeDetector(Detector):
    """Returns fixed labels; finds nothing by default."""

    def __init__(self, labels: dict[str, float] | None = None) -> None:
        self.fixed = dict(labels or {})
        self.calls = 0

    def labels(self, image: Image.Image) -> dict[str, float]:
        self.calls += 1
        return dict(self.fixed)


class OnnxDetector(Detector):
    def __init__(self, manifest_path: Path, models_dir: Path, device: str = "cpu") -> None:
        import onnxruntime as ort

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(m for m in manifest["models"] if m["role"] == "detector_large")
        self.input = entry["input"]
        self.names = entry["output"]["labels"]
        self.explicit = frozenset(entry["output"].get("explicit_labels") or EXPLICIT)
        providers = ["CPUExecutionProvider"]
        if device == "cuda":
            providers.insert(0, "CUDAExecutionProvider")
        self.session = ort.InferenceSession(str(models_dir / entry["file"]), providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    def preprocess(self, image: Image.Image) -> np.ndarray:
        """Letterbox into size x size, padded with grey, as models/watchman_models/preprocess.py."""
        size = int(self.input["size"])
        img = image.convert("RGB")
        w, h = img.size
        scale = min(size / w, size / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        canvas = Image.new("RGB", (size, size), (PAD, PAD, PAD))
        canvas.paste(img.resize((nw, nh), Image.Resampling.BILINEAR), ((size - nw) // 2, (size - nh) // 2))
        x = np.asarray(canvas, dtype=np.float32) / float(self.input.get("scale", 255.0))
        x = (x - np.array(self.input["mean"], dtype=np.float32)) / np.array(self.input["std"], dtype=np.float32)
        return x.transpose(2, 0, 1)[None, ...]

    def labels(self, image: Image.Image) -> dict[str, float]:
        raw = np.asarray(self.session.run(None, {self.input_name: self.preprocess(image)})[0], dtype=np.float32)
        arr = raw[0] if raw.ndim == 3 else raw
        if arr.shape[0] != 4 + len(self.names):
            arr = arr.T
        # YOLOv8 output: four box values, then one score per label for every anchor. Only which
        # labels appear matters here, so take each label's best score; no boxes are kept.
        best = arr[4:].max(axis=1)
        return {name: round(float(s), 3) for name, s in zip(self.names, best, strict=True) if s >= KEEP}
