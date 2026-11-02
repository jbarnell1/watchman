"""What the phone would have decided about each screenshot in a folder. Numbers only.

    uv run python eval/screenshots.py ~/Pictures/watchman-tests [--avatar 96,128] [--top 10] [--coverage explicit]

For every image it runs the whole-screen pass (crop classifier plus the small detector, as the
Android app does) and then slides avatar-sized square windows over the screen and scores each
with the crop classifier, the way the app judges each image the accessibility tree reports. It
prints the whole-screen scores, the detector labels, and the hottest windows with their position,
size, hash and score. Nothing is written anywhere; paste the output into chat to tune thresholds.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from PIL import Image, ImageStat

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from watchman_models.dhash import dhash_image  # noqa: E402
from watchman_models.manifest import load  # noqa: E402
from watchman_models.runner import Runner  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp"}
COVERAGE = {
    "explicit": ["FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED", "ANUS_EXPOSED",
                 "BUTTOCKS_EXPOSED"],
}
COVERAGE["revealing"] = COVERAGE["explicit"] + ["FEMALE_BREAST_COVERED", "FEMALE_GENITALIA_COVERED", "BUTTOCKS_COVERED",
                                                  "ANUS_COVERED", "MALE_BREAST_EXPOSED"]
COVERAGE["strict"] = COVERAGE["revealing"] + ["BELLY_EXPOSED", "ARMPITS_EXPOSED"]


def windows(width: int, height: int, size: int, stride: int):
    for y in range(0, max(1, height - size + 1), stride):
        for x in range(0, max(1, width - size + 1), stride):
            yield x, y


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", type=Path)
    ap.add_argument("--manifest", default=str(HERE.parent / "manifest.json"))
    ap.add_argument("--cache", default=str(HERE.parent / "cache"))
    ap.add_argument("--avatar", default="128", help="window sizes in px, comma separated; YouTube avatars on a 1440 px wide phone are about 110 to 130")
    ap.add_argument("--stride", type=float, default=0.75, help="window step as a fraction of its size")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--cover", type=float, default=0.80)
    ap.add_argument("--flag", type=float, default=0.60)
    ap.add_argument("--detector-cover", type=float, default=0.55)
    ap.add_argument("--coverage", choices=list(COVERAGE), default="explicit")
    ap.add_argument("--gray", action="store_true", help="also score each hot window as grayscale, to see what tint does")
    args = ap.parse_args()

    _, entries = load(Path(args.manifest))
    cache = Path(args.cache)
    classifier = Runner(next(e for e in entries if e.role == "crop_classifier_int8"), cache)
    detector_entry = next(e for e in entries if e.role == "detector_small")
    detector = Runner(detector_entry, cache)
    sizes = [int(s) for s in args.avatar.split(",")]
    labels = COVERAGE[args.coverage]

    files = sorted(p for p in args.folder.iterdir() if p.suffix.lower() in EXTS)
    if not files:
        print(f"no images in {args.folder}")
        return
    print(f"{len(files)} screenshots; cover {args.cover} flag {args.flag} detector {args.detector_cover}; coverage {args.coverage}")
    for f in files:
        t0 = time.perf_counter()
        img = Image.open(f).convert("RGB")
        w, h = img.size
        whole_cls, _ = classifier.run(img)
        _, boxes = detector.run(img)
        det = max((b.score for b in boxes if b.label in labels), default=0.0)
        found = sorted({b.label for b in boxes if b.score >= 0.25})
        hot = []
        for size in sizes:
            stride = max(16, int(size * args.stride))
            for x, y in windows(w, h, size, stride):
                crop = img.crop((x, y, x + size, y + size))
                if max(ImageStat.Stat(crop).stddev) < 8:
                    continue  # flat background: the classifier has nothing to say about it
                score, _ = classifier.run(crop)
                if score >= args.flag * 0.5:
                    hot.append((score, x, y, size, crop))
        hot.sort(key=lambda t: -t[0])
        shown = []
        for score, x, y, size, crop in hot:
            if any(abs(x - sx) < size // 2 and abs(y - sy) < size // 2 for _, sx, sy, _, _ in shown):
                continue
            shown.append((score, x, y, size, crop))
            if len(shown) >= args.top:
                break
        verdict = "COVER" if whole_cls >= args.cover or det >= args.detector_cover else "flag" if whole_cls >= args.flag else "safe"
        print(f"\n{f.name}  {w}x{h}  whole-screen classifier {whole_cls:.3f}  detector {det:.3f}  -> {verdict}"
              f"  [{', '.join(found) or 'no parts'}]  ({time.perf_counter() - t0:.1f}s)")
        for score, x, y, size, crop in shown:
            mark = "COVER" if score >= args.cover else "flag " if score >= args.flag else "safe "
            gray = ""
            if args.gray:
                g, _ = classifier.run(crop.convert("L").convert("RGB"))
                gray = f"  gray {g:.3f}"
            print(f"  {score:.3f}  {mark}  at {x:5d},{y:5d}  {size}px  {dhash_image(crop)}{gray}")
        if not shown:
            print("  no window scored above half the flag threshold")
    print("\nNothing was saved. Each line is position, size, hash and score of one window; the hash lets a known-bad list cover it everywhere.")


if __name__ == "__main__":
    main()
