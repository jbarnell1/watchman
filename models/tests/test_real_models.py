"""Runs only when the weights are in cache/ (never in CI). Checks every manifest entry loads,
accepts the manifest's preprocessing and returns a score in range on a synthetic image."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from watchman_models.manifest import load, sha256_of
from watchman_models.runner import Runner

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"


@pytest.fixture(scope="module")
def entries():
    _, entries = load(ROOT / "manifest.json")
    missing = [e.file for e in entries if not (CACHE / e.file).exists()]
    if missing:
        pytest.skip(f"weights not fetched: {missing}")
    return entries


def test_cached_files_match_the_manifest(entries):
    for e in entries:
        assert e.sha256 and sha256_of(CACHE / e.file) == e.sha256, e.file
        assert (CACHE / e.file).stat().st_size == e.bytes, e.file


def test_every_model_runs(entries):
    img = Image.new("RGB", (300, 200), (180, 140, 120))
    for e in entries:
        score, boxes = Runner(e, CACHE).run(img)
        assert 0.0 <= score <= 1.0, e.role
        if e.output["kind"] == "yolov8":
            assert isinstance(boxes, list)
