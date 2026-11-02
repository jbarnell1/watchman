"""Generate the dHash test vectors every client checks against (test-vectors/dhash/).

Deterministic synthetic images only: gradients, checkers, blocks and seeded noise at several
sizes, including sizes smaller than the 9 by 8 hash grid. Run once and commit the output; the
Python reference in watchman_models/dhash.py defines the expected hashes.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image

from watchman_models.dhash import dhash_image

OUT = Path(__file__).resolve().parent / "test-vectors" / "dhash"


def gradient(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    img.putdata([(int(255 * x / max(1, w - 1)), int(255 * y / max(1, h - 1)), 128)
                 for y in range(h) for x in range(w)])
    return img


def checker(w: int, h: int, cell: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    img.putdata([(230, 230, 230) if ((x // cell) + (y // cell)) % 2 == 0 else (20, 20, 20)
                 for y in range(h) for x in range(w)])
    return img


def noise(w: int, h: int, seed: int) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (w, h))
    img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(w * h)])
    return img


def blocks(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (40, 60, 200))
    for i, colour in enumerate([(250, 40, 40), (40, 250, 40), (250, 250, 40)]):
        box = Image.new("RGB", (w // 3, h // 2), colour)
        img.paste(box, (i * (w // 3), (i % 2) * (h // 2)))
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cases = {
        "gradient-36x32": gradient(36, 32),
        "gradient-100x75": gradient(100, 75),
        "checker-64x64-c8": checker(64, 64, 8),
        "checker-45x40-c5": checker(45, 40, 5),
        "noise-48x48-s1": noise(48, 48, 1),
        "noise-200x120-s7": noise(200, 120, 7),
        "blocks-90x60": blocks(90, 60),
        "tiny-5x4": noise(5, 4, 3),
        "flat-20x20": Image.new("RGB", (20, 20), (77, 77, 77)),
    }
    vectors = []
    for name, img in cases.items():
        img.save(OUT / f"{name}.png")
        vectors.append({"file": f"{name}.png", "width": img.width, "height": img.height, "dhash": dhash_image(img)})
    # raw.json carries the same images as packed 0xRRGGBB ints, for test runners without a PNG decoder
    # (Android unit tests compile against android.jar, which has no javax.imageio).
    raw = []
    for name, img in cases.items():
        rgb = img.convert("RGB")
        raw.append({"file": f"{name}.png", "width": rgb.width, "height": rgb.height,
                    "pixels": [(r << 16) | (g << 8) | b for r, g, b in rgb.getdata()],
                    "dhash": dhash_image(img)})
    (OUT / "raw.json").write_text(json.dumps({"vectors": raw}, separators=(",", ":")) + chr(10), encoding="utf-8")
    (OUT / "vectors.json").write_text(json.dumps({
        "note": "Expected 64-bit dHash per image, from watchman_models/dhash.py. Clients must produce "
                "exactly these hashes for these PNGs.",
        "vectors": vectors,
    }, indent=2) + "\n", encoding="utf-8")
    for v in vectors:
        print(v["file"], v["dhash"])


if __name__ == "__main__":
    main()
