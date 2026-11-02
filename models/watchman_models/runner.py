"""Run a manifest entry with ONNX Runtime and turn its raw output into a score or boxes."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from watchman_models.manifest import Entry
from watchman_models.preprocess import Letterbox, prepare


@dataclass
class Box:
    label: str
    score: float
    x: float
    y: float
    w: float
    h: float


def softmax(logits: np.ndarray) -> np.ndarray:
    z = logits - logits.max()
    e = np.exp(z)
    return e / e.sum()


def classifier_score(raw: np.ndarray, output: dict) -> float:
    logits = np.asarray(raw, dtype=np.float64).reshape(-1)
    if output["kind"] == "softmax":
        probs = softmax(logits)
    elif output["kind"] == "sigmoid":
        probs = 1 / (1 + np.exp(-logits))
    else:
        raise ValueError(output["kind"])
    return float(probs[output["labels"].index(output["positive"])])


def iou(a: Box, b: Box) -> float:
    ax2, ay2, bx2, by2 = a.x + a.w, a.y + a.h, b.x + b.w, b.y + b.h
    iw = max(0.0, min(ax2, bx2) - max(a.x, b.x))
    ih = max(0.0, min(ay2, by2) - max(a.y, b.y))
    inter = iw * ih
    union = a.w * a.h + b.w * b.h - inter
    return inter / union if union > 0 else 0.0


def decode_yolov8(raw: np.ndarray, output: dict, lb: Letterbox | None, conf: float = 0.25,
                  nms_iou: float = 0.45) -> list[Box]:
    """raw is (1, 4 + classes, anchors) as exported by Ultralytics: cx, cy, w, h in letterboxed
    pixels, then one score per class. Returns boxes in original image pixels after NMS per class."""
    arr = np.asarray(raw, dtype=np.float32)
    if arr.ndim == 3:
        arr = arr[0]
    if arr.shape[0] != 4 + len(output["labels"]):
        arr = arr.T
    boxes: list[Box] = []
    scores = arr[4:]
    for i in range(arr.shape[1]):
        cls = int(scores[:, i].argmax())
        s = float(scores[cls, i])
        if s < conf:
            continue
        cx, cy, w, h = (float(v) for v in arr[:4, i])
        x, y = cx - w / 2, cy - h / 2
        if lb is not None:
            x, y, w, h = (x - lb.pad_x) / lb.scale, (y - lb.pad_y) / lb.scale, w / lb.scale, h / lb.scale
        boxes.append(Box(output["labels"][cls], s, x, y, w, h))
    kept: list[Box] = []
    for label in {b.label for b in boxes}:
        same = sorted((b for b in boxes if b.label == label), key=lambda b: -b.score)
        while same:
            best = same.pop(0)
            kept.append(best)
            same = [b for b in same if iou(best, b) < nms_iou]
    return sorted(kept, key=lambda b: -b.score)


def explicit_score(boxes: list[Box], output: dict) -> float:
    explicit = set(output.get("explicit_labels", []))
    return max((b.score for b in boxes if b.label in explicit), default=0.0)


class Runner:
    def __init__(self, entry: Entry, cache_dir: Path, providers: list[str] | None = None) -> None:
        import onnxruntime as ort

        self.entry = entry
        self.session = ort.InferenceSession(str(cache_dir / entry.file),
                                            providers=providers or ["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def run(self, image: Image.Image) -> tuple[float, list[Box]]:
        tensor, lb = prepare(image, self.entry.input)
        raw = self.session.run(None, {self.input_name: tensor})[0]
        if self.entry.output["kind"] == "yolov8":
            boxes = decode_yolov8(raw, self.entry.output, lb)
            return explicit_score(boxes, self.entry.output), boxes
        return classifier_score(raw, self.entry.output), []
