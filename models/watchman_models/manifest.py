"""Load and check models/manifest.json."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

ROLES = {"crop_classifier", "crop_classifier_int8", "detector_small", "detector_large", "second_opinion"}
OUTPUT_KINDS = {"softmax", "sigmoid", "yolov8"}
RESIZES = {"bilinear", "bicubic", "letterbox"}


class ManifestError(ValueError):
    pass


@dataclass
class Entry:
    role: str
    file: str
    url: str
    sha256: str
    bytes: int
    license: str
    source: str
    input: dict
    output: dict
    export: dict | None
    used_by: list[str]


def validate(manifest: dict) -> list[Entry]:
    if manifest.get("version") != 1:
        raise ManifestError("manifest version must be 1")
    entries = []
    seen_roles: set[str] = set()
    seen_files: set[str] = set()
    for raw in manifest.get("models", []):
        for key in ("role", "file", "license", "source", "input", "output"):
            if key not in raw:
                raise ManifestError(f"model missing {key}: {raw}")
        if raw["role"] not in ROLES:
            raise ManifestError(f"unknown role {raw['role']}")
        if raw["role"] in seen_roles or raw["file"] in seen_files:
            raise ManifestError(f"duplicate role or file: {raw['role']} {raw['file']}")
        seen_roles.add(raw["role"])
        seen_files.add(raw["file"])
        inp, out = raw["input"], raw["output"]
        for key in ("name", "size", "layout", "color", "scale", "mean", "std", "resize"):
            if key not in inp:
                raise ManifestError(f"{raw['role']} input missing {key}")
        if inp["resize"] not in RESIZES or inp["layout"] not in ("NCHW", "NHWC") or inp["color"] not in ("RGB", "BGR"):
            raise ManifestError(f"{raw['role']} input has an unknown resize, layout or color")
        if len(inp["mean"]) != 3 or len(inp["std"]) != 3:
            raise ManifestError(f"{raw['role']} mean and std need three channels")
        if out.get("kind") not in OUTPUT_KINDS or "labels" not in out:
            raise ManifestError(f"{raw['role']} output needs a kind and labels")
        if out["kind"] in ("softmax", "sigmoid") and out.get("positive") not in out["labels"]:
            raise ManifestError(f"{raw['role']} positive label must be one of its labels")
        if out["kind"] == "yolov8":
            missing = set(out.get("explicit_labels", [])) - set(out["labels"])
            if missing:
                raise ManifestError(f"{raw['role']} explicit_labels not in labels: {missing}")
        if not raw.get("url") and not raw.get("export"):
            raise ManifestError(f"{raw['role']} needs a url or an export recipe")
        entries.append(Entry(
            role=raw["role"], file=raw["file"], url=raw.get("url", ""), sha256=raw.get("sha256", ""),
            bytes=int(raw.get("bytes", 0)), license=raw["license"], source=raw["source"], input=inp,
            output=out, export=raw.get("export"), used_by=list(raw.get("used_by", [])),
        ))
    return entries


def load(path: Path) -> tuple[dict, list[Entry]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    return manifest, validate(manifest)


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
