from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.score import images_in, rates, shrink  # noqa: E402


def test_rates_at_thresholds():
    r = rates([0.1, 0.55, 0.95], [0.7, 0.9], [0.5, 0.8])
    assert r[0]["false_alarm_rate"] == 2 / 3 and r[0]["catch_rate"] == 1.0
    assert r[1]["false_alarm_rate"] == 1 / 3 and r[1]["catch_rate"] == 0.5
    assert rates([], [], [0.5])[0]["catch_rate"] is None


def test_shrink_only_downscales():
    img = Image.new("RGB", (400, 200))
    assert shrink(img, 100).size == (100, 50)
    assert shrink(img, 0).size == (400, 200)
    assert shrink(img, 1000).size == (400, 200)


def test_images_in_filters_by_extension(tmp_path):
    (tmp_path / "a.jpg").write_bytes(b"")
    (tmp_path / "b.txt").write_bytes(b"")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.png").write_bytes(b"")
    assert [p.name for p in images_in(tmp_path)] == ["a.jpg", "c.png"]
    assert images_in(None) == []
