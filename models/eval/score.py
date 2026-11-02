"""Score a folder of images with one or more manifest models and report rates at thresholds.

    uv run python eval/score.py --role crop_classifier --negatives ~/photos --positives ~/private/pos \
        --sizes 0,96,64,48,32 --thresholds 0.5,0.6,0.7,0.8,0.9

Negatives are images that must never alert (the household's own photos, ordinary screenshots).
Positives come only from a folder the household supplies locally; nothing explicit is downloaded or
stored by this tool. Results are numbers only; no image leaves the machine. `--sizes` downscales
every image to that long side before scoring (0 keeps the original) to measure avatar-size
behaviour. Writes a JSON report to eval/out/<role>-<timestamp>.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from watchman_models.manifest import load  # noqa: E402
from watchman_models.runner import Runner  # noqa: E402

EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def images_in(folder: Path | None) -> list[Path]:
    if folder is None:
        return []
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in EXTS)


def shrink(img: Image.Image, long_side: int) -> Image.Image:
    if long_side <= 0 or max(img.size) <= long_side:
        return img
    out = img.copy()
    out.thumbnail((long_side, long_side), Image.Resampling.LANCZOS)
    return out


def score_folder(runner: Runner, files: list[Path], size: int) -> list[float]:
    scores = []
    for f in files:
        try:
            img = Image.open(f)
            img.load()
        except Exception:
            continue
        score, _ = runner.run(shrink(img, size))
        scores.append(score)
    return scores


def rates(neg: list[float], pos: list[float], thresholds: list[float]) -> list[dict]:
    out = []
    for t in thresholds:
        fp = sum(1 for s in neg if s >= t)
        tp = sum(1 for s in pos if s >= t)
        out.append({
            "threshold": t,
            "false_alarm_rate": fp / len(neg) if neg else None,
            "catch_rate": tp / len(pos) if pos else None,
            "negatives": len(neg), "positives": len(pos),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(HERE.parent / "manifest.json"))
    ap.add_argument("--cache", default=str(HERE.parent / "cache"))
    ap.add_argument("--role", action="append", required=True)
    ap.add_argument("--negatives", type=Path)
    ap.add_argument("--positives", type=Path)
    ap.add_argument("--sizes", default="0,96,64,48,32")
    ap.add_argument("--thresholds", default="0.5,0.6,0.7,0.8,0.9")
    args = ap.parse_args()

    _, entries = load(Path(args.manifest))
    sizes = [int(s) for s in args.sizes.split(",")]
    thresholds = [float(t) for t in args.thresholds.split(",")]
    neg_files, pos_files = images_in(args.negatives), images_in(args.positives)
    report = {"when": time.strftime("%Y-%m-%dT%H:%M:%S"), "roles": {}}
    for role in args.role:
        entry = next(e for e in entries if e.role == role)
        runner = Runner(entry, Path(args.cache))
        per_size = {}
        for size in sizes:
            t0 = time.perf_counter()
            neg = score_folder(runner, neg_files, size)
            pos = score_folder(runner, pos_files, size)
            elapsed = time.perf_counter() - t0
            n = len(neg) + len(pos)
            per_size[str(size)] = {
                "rates": rates(neg, pos, thresholds),
                "ms_per_image": round(1000 * elapsed / n, 1) if n else None,
            }
            parts = []
            for r in per_size[str(size)]["rates"]:
                fa = "-" if r["false_alarm_rate"] is None else f"{r['false_alarm_rate']:.3f}"
                catch = "-" if r["catch_rate"] is None else f"{r['catch_rate']:.3f}"
                parts.append(f"t{r['threshold']}: fa {fa} catch {catch}")
            print(f"{role} @ {size or 'full'}: " + ", ".join(parts))
        report["roles"][role] = per_size
    out = HERE / "out"
    out.mkdir(exist_ok=True)
    path = out / f"{'-'.join(args.role)}-{int(time.time())}.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"report: {path}")


if __name__ == "__main__":
    main()
