from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fetch  # noqa: E402
from watchman_models.manifest import Entry, sha256_of  # noqa: E402


def _entry(file: str, sha: str = "", size: int = 0) -> Entry:
    return Entry(role="detector_small", file=file, url="file://x", sha256=sha, bytes=size, license="MIT",
                 source="", input={}, output={}, export=None, used_by=[])


def test_verify_records_hash_when_asked(tmp_path):
    f = tmp_path / "m.onnx"
    f.write_bytes(b"weights")
    raw: dict = {}
    assert fetch.verify(_entry("m.onnx"), f, write=True, raw=raw)
    assert raw["sha256"] == sha256_of(f) and raw["bytes"] == 7


def test_verify_refuses_unknown_hash_without_write(tmp_path):
    f = tmp_path / "m.onnx"
    f.write_bytes(b"weights")
    assert not fetch.verify(_entry("m.onnx"), f, write=False, raw={})


def test_verify_rejects_mismatch(tmp_path):
    f = tmp_path / "m.onnx"
    f.write_bytes(b"weights")
    good = sha256_of(f)
    assert fetch.verify(_entry("m.onnx", good, 7), f, write=False, raw={})
    assert not fetch.verify(_entry("m.onnx", "0" * 64, 7), f, write=False, raw={})
    assert not fetch.verify(_entry("m.onnx", good, 8), f, write=False, raw={})


def test_download_uses_a_part_file(tmp_path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"x" * 3000)
    target = tmp_path / "out.bin"
    fetch.download(src.resolve().as_uri(), target)
    assert target.read_bytes() == b"x" * 3000
    assert not target.with_suffix(".bin.part").exists()


def test_manifest_json_is_tidy():
    text = (ROOT / "manifest.json").read_text(encoding="utf-8")
    assert json.loads(text)["version"] == 1


def test_copy_verified_keeps_only_matching_files(tmp_path):
    src = tmp_path / "artifact" / "m.onnx"
    src.parent.mkdir()
    src.write_bytes(b"weights")
    good = sha256_of(src)
    target = tmp_path / "m.onnx"
    assert not fetch.copy_verified(_entry("m.onnx", "0" * 64, 7), src, target)
    assert not target.exists() and not target.with_suffix(".onnx.part").exists()
    assert not fetch.copy_verified(_entry("m.onnx"), src, target)   # no hash in the manifest: never copied
    assert fetch.copy_verified(_entry("m.onnx", good, 7), src, target)
    assert target.read_bytes() == b"weights"


def test_from_artifact_fills_exported_entries(tmp_path, monkeypatch):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "c.onnx").write_bytes(b"classifier")
    sha = sha256_of(artifact / "c.onnx")
    manifest = {"version": 1, "models": [{
        "role": "crop_classifier", "file": "c.onnx", "url": "", "sha256": sha, "bytes": 10,
        "license": "Apache-2.0", "source": "", "export": {"kind": "marqo"},
        "input": {"name": "input", "size": 384, "layout": "NCHW", "color": "RGB", "scale": 255.0,
                  "mean": [0.5] * 3, "std": [0.5] * 3, "resize": "bicubic"},
        "output": {"name": "logits", "kind": "softmax", "labels": ["NSFW", "SFW"], "positive": "NSFW"},
    }]}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(fetch, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(fetch, "CACHE", tmp_path / "cache")
    assert fetch.main([]) == 1                                  # exported locally, nothing to copy from
    assert fetch.main(["--from-artifact", str(artifact)]) == 0
    assert (tmp_path / "cache" / "c.onnx").read_bytes() == b"classifier"
    assert (tmp_path / "cache" / "manifest.json").exists()


def test_github_release_urls_are_recognised():
    assert fetch.GITHUB_RELEASE.match("https://github.com/notAI-tech/NudeNet/releases/download/v3.4-weights/320n.onnx")
    assert fetch.github_asset_url("https://huggingface.co/x/resolve/main/model.onnx", "token") is None
