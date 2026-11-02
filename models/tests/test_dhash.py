from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from watchman_models.dhash import area_average, dhash, dhash_image, hamming, matches

VECTORS = Path(__file__).resolve().parent.parent / "test-vectors" / "dhash"


def test_area_average_exact_blocks():
    # 18x16 grid of 2x2 blocks: each output cell is exactly one block's value.
    grid = [[float((x // 2) + 10 * (y // 2)) for x in range(18)] for y in range(16)]
    small = area_average(grid)
    assert small[0][0] == 0.0 and small[0][8] == 8.0 and small[7][0] == 70.0


def test_area_average_fractional_coverage():
    # 10 wide into 9: cells straddle pixel boundaries; a constant image stays constant.
    grid = [[42.0] * 10 for _ in range(8)]
    assert all(abs(v - 42.0) < 1e-9 for row in area_average(grid) for v in row)


def test_dhash_of_a_left_to_right_gradient_sets_no_bits():
    rows = [[(x * 10, x * 10, x * 10) for x in range(18)] for _ in range(16)]
    assert dhash(18, 16, rows) == "0" * 16


def test_dhash_of_a_right_to_left_gradient_sets_every_bit():
    rows = [[(255 - x * 10, 255 - x * 10, 255 - x * 10) for x in range(18)] for _ in range(16)]
    h = dhash(18, 16, rows)
    # Bits are row*8+col for col 0..7: all 64 set.
    assert h == "f" * 16


def test_hamming_and_tolerance():
    assert hamming("0" * 16, "0" * 15 + "f") == 4
    assert matches("0" * 16, "0" * 15 + "f")
    assert not matches("0" * 16, "0" * 14 + "7f")


def test_vectors_match_the_committed_hashes():
    data = json.loads((VECTORS / "vectors.json").read_text())
    assert len(data["vectors"]) >= 8
    for v in data["vectors"]:
        img = Image.open(VECTORS / v["file"])
        assert (img.width, img.height) == (v["width"], v["height"])
        assert dhash_image(img) == v["dhash"], v["file"]
