"""The server's second look at a flagged crop. One interface, an ONNX implementation and a fake.

The model, its preprocessing and its outputs come from models/manifest.json (role second_opinion);
nothing about shapes or labels is hard-coded here beyond reading that entry.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image


class SecondOpinion:
    def score(self, image: Image.Image) -> float:
        """Probability that the image is explicit, 0 to 1."""
        raise NotImplementedError


class FakeSecondOpinion(SecondOpinion):
    """Returns a fixed score, or one looked up by the image's size for tests that need both answers."""

    def __init__(self, score: float = 0.0) -> None:
        self.fixed = score
        self.calls = 0

    def score(self, image: Image.Image) -> float:
        self.calls += 1
        return self.fixed


class OnnxSecondOpinion(SecondOpinion):
    def __init__(self, manifest_path: Path, models_dir: Path, device: str = "cpu") -> None:
        import onnxruntime as ort

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        entry = next(m for m in manifest["models"] if m["role"] == "second_opinion")
        self.input = entry["input"]
        self.output = entry["output"]
        providers = ["CPUExecutionProvider"]
        if device == "cuda":
            providers.insert(0, "CUDAExecutionProvider")
        self.session = ort.InferenceSession(str(models_dir / entry["file"]), providers=providers)
        self.input_name = self.session.get_inputs()[0].name

    def preprocess(self, image: Image.Image) -> np.ndarray:
        size = self.input["size"]
        resample = Image.Resampling.BICUBIC if self.input.get("resize") == "bicubic" else Image.Resampling.BILINEAR
        img = image.convert("RGB").resize((size, size), resample)
        arr = np.asarray(img, dtype=np.float32) / 255.0
        mean = np.array(self.input["mean"], dtype=np.float32)
        std = np.array(self.input["std"], dtype=np.float32)
        arr = (arr - mean) / std
        if self.input.get("layout", "NCHW") == "NCHW":
            arr = arr.transpose(2, 0, 1)
        return arr[None, ...]

    def score(self, image: Image.Image) -> float:
        out = self.session.run(None, {self.input_name: self.preprocess(image)})[0][0]
        logits = np.asarray(out, dtype=np.float64)
        if self.output.get("kind") == "softmax":
            probs = np.exp(logits - logits.max())
            probs /= probs.sum()
        elif self.output.get("kind") == "sigmoid":
            probs = 1 / (1 + np.exp(-logits))
        else:
            probs = logits
        labels = self.output["labels"]
        return float(probs[labels.index(self.output["positive"])])
