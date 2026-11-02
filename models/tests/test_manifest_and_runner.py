from __future__ import annotations

import copy
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from watchman_models.manifest import ManifestError, load, validate
from watchman_models.preprocess import classifier_input, letterbox
from watchman_models.runner import Box, classifier_score, decode_yolov8, explicit_score

MANIFEST = Path(__file__).resolve().parent.parent / "manifest.json"


def test_committed_manifest_is_valid():
    manifest, entries = load(MANIFEST)
    roles = {e.role for e in entries}
    assert {"crop_classifier", "detector_small", "detector_large", "second_opinion"} <= roles
    for e in entries:
        assert e.license in ("Apache-2.0", "MIT")
        assert e.input["size"] in (320, 384, 640)


def test_validation_catches_bad_entries():
    manifest = json.loads(MANIFEST.read_text())
    bad = copy.deepcopy(manifest)
    bad["models"][0]["output"]["positive"] = "nope"
    with pytest.raises(ManifestError):
        validate(bad)
    bad = copy.deepcopy(manifest)
    bad["models"][2]["output"]["explicit_labels"] = ["NOT_A_LABEL"]
    with pytest.raises(ManifestError):
        validate(bad)
    bad = copy.deepcopy(manifest)
    bad["models"][3]["role"] = bad["models"][2]["role"]
    with pytest.raises(ManifestError):
        validate(bad)


def test_classifier_preprocessing_matches_the_spec():
    spec = {"size": 8, "layout": "NCHW", "color": "RGB", "scale": 255.0, "mean": [0.5, 0.5, 0.5],
            "std": [0.5, 0.5, 0.5], "resize": "bilinear"}
    img = Image.new("RGB", (30, 20), (255, 0, 128))
    x = classifier_input(img, spec)
    assert x.shape == (1, 3, 8, 8) and x.dtype == np.float32
    assert np.allclose(x[0, 0], 1.0) and np.allclose(x[0, 1], -1.0)
    assert np.allclose(x[0, 2], (128 / 255 - 0.5) / 0.5, atol=1e-5)


def test_letterbox_keeps_aspect_and_maps_back():
    spec = {"size": 64, "layout": "NCHW", "color": "RGB", "scale": 255.0, "mean": [0, 0, 0],
            "std": [1, 1, 1], "resize": "letterbox"}
    img = Image.new("RGB", (200, 100), (255, 255, 255))
    x, lb = letterbox(img, spec)
    assert x.shape == (1, 3, 64, 64)
    assert lb.scale == 0.32 and lb.pad_x == 0 and lb.pad_y == 16
    assert np.allclose(x[0, :, 16:48, :], 1.0)          # the image
    assert np.allclose(x[0, 0, :16, :], 114 / 255)      # the padding


def test_softmax_score_picks_the_positive_label():
    out = {"kind": "softmax", "labels": ["NSFW", "SFW"], "positive": "NSFW"}
    assert classifier_score(np.array([[2.0, 0.0]]), out) > 0.85
    out2 = {"kind": "softmax", "labels": ["sfw", "nsfw"], "positive": "nsfw"}
    assert classifier_score(np.array([[2.0, 0.0]]), out2) < 0.15


def test_yolov8_decode_with_nms_and_unletterbox():
    labels = ["A", "B", "C"]
    out = {"kind": "yolov8", "labels": labels, "explicit_labels": ["B"]}
    # Three anchors: two overlapping B boxes (keep the stronger), one weak A below conf.
    raw = np.zeros((1, 4 + 3, 3), dtype=np.float32)
    raw[:, :, 0] = [100, 100, 40, 40, 0.1, 0.9, 0.0]
    raw[:, :, 1] = [104, 102, 40, 40, 0.1, 0.6, 0.0]
    raw[:, :, 2] = [10, 10, 10, 10, 0.2, 0.0, 0.0]
    from watchman_models.preprocess import Letterbox
    boxes = decode_yolov8(raw, out, Letterbox(scale=0.5, pad_x=0, pad_y=20))
    assert len(boxes) == 1
    b = boxes[0]
    assert b.label == "B" and abs(b.score - 0.9) < 1e-6
    assert (b.x, b.y, b.w, b.h) == (160.0, 120.0, 80.0, 80.0)
    assert explicit_score(boxes, out) == pytest.approx(0.9)
    assert explicit_score([Box("A", 0.99, 0, 0, 1, 1)], out) == 0.0
