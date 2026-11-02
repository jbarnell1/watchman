"""Download, export and verify every model in manifest.json into cache/.

    uv run python fetch.py                 # fetch what is missing, verify every hash
    uv run python fetch.py --write-hashes  # first time: record sha256 and bytes for entries without one
    uv run python fetch.py --export        # also run the export recipes (needs the export group)
    uv run python fetch.py --from-artifact DIR   # copy verified files from an unpacked watchman-models artifact

Entries with a url are downloaded. GitHub answers some release downloads only to a signed-in client;
with GITHUB_TOKEN set (any account's token) those go through the GitHub API instead.
Entries with an export recipe are produced locally:
  marqo     timm ViT weights (safetensors) -> ONNX fp32 (export_marqo.py)
  quantize  an ONNX file in the cache -> dynamic int8 (onnxruntime.quantization)
A hash mismatch is an error: the manifest is the source of truth, never the download.

--from-artifact takes a folder holding files from the `watchman-models` workflow artifact (models.yml,
run by hand) or from another machine's cache/. A file is copied only when its sha256 and size match
the manifest, so the folder does not need to be trusted.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from watchman_models.manifest import Entry, load, sha256_of  # noqa: E402

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.json"
CACHE = HERE / "cache"


GITHUB_RELEASE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/releases/download/([^/]+)/([^/?#]+)$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_asset_url(url: str, token: str) -> str | None:
    """The signed download URL for a GitHub release asset, asked through the API with a token.
    The token goes to api.github.com only, never to the storage host the asset redirects to."""
    m = GITHUB_RELEASE.match(url)
    if not m:
        return None
    owner, repo, tag, name = m.groups()
    api = f"https://api.github.com/repos/{owner}/{repo}"
    auth = {"Authorization": f"Bearer {token}", "User-Agent": "watchman-fetch/1"}
    req = urllib.request.Request(f"{api}/releases/tags/{tag}",
                                 headers={**auth, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        release = json.load(resp)
    asset = next((a for a in release.get("assets", []) if a.get("name") == name), None)
    if asset is None:
        raise SystemExit(f"{name} is not an asset of {owner}/{repo} {tag}")
    req = urllib.request.Request(f"{api}/releases/assets/{asset['id']}",
                                 headers={**auth, "Accept": "application/octet-stream"})
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=60) as resp:
            return resp.geturl()
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308) and exc.headers.get("Location"):
            return exc.headers["Location"]
        raise


def download(url: str, target: Path) -> None:
    tmp = target.with_suffix(target.suffix + ".part")
    headers = {"User-Agent": "watchman-fetch/1"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and GITHUB_RELEASE.match(url):
        url = github_asset_url(url, token) or url
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        if "text/html" in (resp.headers.get("Content-Type") or "") or "/login" in resp.geturl():
            raise SystemExit(f"{url} answered with a web page, not a file (GitHub may want a token: "
                             f"set GITHUB_TOKEN, or `gh release download`)")
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            if done == 0 and chunk[:15].lower().startswith(b"<!doctype html"):
                raise SystemExit(f"{url} answered with a web page, not a file")
            out.write(chunk)
            done += len(chunk)
            if total:
                print(f"\r  {target.name}: {done * 100 // total}%", end="", flush=True)
    print()
    tmp.replace(target)


def verify(entry: Entry, path: Path, write: bool, raw: dict) -> bool:
    digest = sha256_of(path)
    size = path.stat().st_size
    if entry.sha256:
        if digest != entry.sha256:
            print(f"  HASH MISMATCH for {entry.file}: manifest {entry.sha256[:12]}, file {digest[:12]}")
            return False
        if entry.bytes and size != entry.bytes:
            print(f"  SIZE MISMATCH for {entry.file}: manifest {entry.bytes}, file {size}")
            return False
        print(f"  {entry.file}: ok ({size} bytes)")
        return True
    if write:
        raw["sha256"] = digest
        raw["bytes"] = size
        print(f"  {entry.file}: recorded sha256 {digest[:12]} ({size} bytes)")
        return True
    print(f"  {entry.file}: no hash in the manifest; run with --write-hashes once you trust this file")
    return False


def copy_verified(entry: Entry, source: Path, target: Path) -> bool:
    """Copy one file from an artifact folder, keeping it only if it matches the manifest."""
    if not entry.sha256:
        print(f"  {entry.file}: the manifest has no hash for it, so it is not copied from the artifact")
        return False
    tmp = target.with_suffix(target.suffix + ".part")
    shutil.copyfile(source, tmp)
    if sha256_of(tmp) != entry.sha256 or (entry.bytes and tmp.stat().st_size != entry.bytes):
        tmp.unlink()
        print(f"  {entry.file}: the artifact copy does not match the manifest; not used")
        return False
    tmp.replace(target)
    print(f"  {entry.file}: copied from the artifact")
    return True


def export(entry: Entry, raw: dict, write: bool) -> Path:
    recipe = entry.export or {}
    target = CACHE / entry.file
    if recipe.get("kind") == "marqo":
        weights = CACHE / "marqo-model.safetensors"
        if not weights.exists():
            print(f"  downloading weights for {entry.file}")
            download(recipe["weights_url"], weights)
        digest = sha256_of(weights)
        if recipe.get("weights_sha256") and recipe["weights_sha256"] != digest:
            raise SystemExit(f"weights hash mismatch for {entry.file}")
        if write and not recipe.get("weights_sha256"):
            raw["export"]["weights_sha256"] = digest
        from export_marqo import export_marqo

        export_marqo(weights, target, recipe.get("architecture", "vit_tiny_patch16_384"))
    elif recipe.get("kind") == "quantize":
        from onnxruntime.quantization import QuantType, quantize_dynamic

        source = CACHE / recipe["from"]
        if not source.exists():
            raise SystemExit(f"{entry.file} needs {source.name} first")
        # MatMul only: ONNX Runtime Web has no WASM kernel for ConvInteger, so the patch embedding
        # convolution stays float and the transformer blocks go int8.
        quantize_dynamic(str(source), str(target), weight_type=QuantType.QInt8, op_types_to_quantize=["MatMul"])
    else:
        raise SystemExit(f"unknown export kind for {entry.file}")
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-hashes", action="store_true")
    ap.add_argument("--export", action="store_true", help="run export recipes (needs: uv sync --group export)")
    ap.add_argument("--only", help="one role")
    ap.add_argument("--from-artifact", type=Path, metavar="DIR",
                    help="copy files that match the manifest from an unpacked watchman-models artifact")
    args = ap.parse_args(argv)
    if args.from_artifact and not args.from_artifact.is_dir():
        raise SystemExit(f"{args.from_artifact} is not a folder")

    manifest, entries = load(MANIFEST)
    CACHE.mkdir(exist_ok=True)
    ok = True
    for raw, entry in zip(manifest["models"], entries, strict=True):
        if args.only and entry.role != args.only:
            continue
        target = CACHE / entry.file
        print(f"{entry.role}")
        if not target.exists() and args.from_artifact and (args.from_artifact / entry.file).is_file():
            copy_verified(entry, args.from_artifact / entry.file, target)
        if not target.exists():
            if entry.url:
                download(entry.url, target)
            elif args.export:
                export(entry, raw, args.write_hashes)
            else:
                print(f"  {entry.file} is exported locally; run with --export, or --from-artifact DIR "
                      f"with the watchman-models artifact (docs/models.md)")
                ok = False
                continue
        ok = verify(entry, target, args.write_hashes, raw) and ok
    if args.write_hashes:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        print("manifest updated")
    shutil.copy(MANIFEST, CACHE / "manifest.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
