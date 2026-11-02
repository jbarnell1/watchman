# Models

`models/manifest.json` is the source of truth for files, hashes, preprocessing and outputs. Every
client reads it (served by the household server at `/api/v1/models/manifest`) rather than hard-coding
shapes. Weights are never committed; `models/fetch.py` downloads and verifies them, and
`models/export_*.py` produces ONNX files where upstream has none.

| Role (`manifest.roles`) | Model | License | Used by |
|---|---|---|---|
| `crop_classifier` | Marqo/nsfw-image-detection-384, exported to ONNX fp32 (`crop_classifier_int8` is the dynamic int8 copy) | Apache-2.0 | all clients, extension |
| `crop_gate` | taufiqdp MobileNetV4-conv-small NSFW classifier (5 classes), ONNX from upstream, pinned revision. Orders the work only, never clears a picture (ADR-034) | Apache-2.0 | Android |
| `detector_small` | NudeNet 320n ONNX (v3.4 weights) | MIT (PyPI 3.4.2) | Android, iOS |
| `detector_large` | NudeNet 640m ONNX | MIT | Windows, server |
| `second_opinion` | AdamCodd/vit-base-nsfw-detector ONNX int8 | Apache-2.0 | server |

## Manifest shape

```json
{"version": 1,
 "models": [{
   "role": "crop_classifier", "file": "nsfw-384-int8.onnx", "url": "https://...", "sha256": "...",
   "bytes": 6200000, "license": "Apache-2.0", "source": "https://huggingface.co/...",
   "input": {"name": "input", "size": 384, "layout": "NCHW", "color": "RGB",
             "mean": [0.5, 0.5, 0.5], "std": [0.5, 0.5, 0.5], "resize": "bilinear"},
   "output": {"name": "logits", "kind": "softmax", "labels": ["NSFW", "SFW"], "positive": "NSFW"}
 }]}
```

Detector entries use `"input": {"resize": "letterbox", ...}` (fit inside size by size, pad with 114
gray) and `"output": {"kind": "yolov8", "labels": [...], "explicit_labels": [...]}`; a detector's
score is the best box score among `explicit_labels`. `scale` is what pixels are divided by before
`mean` and `std` are applied. Layout `CHW` is an unbatched `NCHW` (the model has no batch axis).
A softmax entry may list several `positives`; its score is their summed probability.
`watchman_models/preprocess.py` and `runner.py` are the reference
for preprocessing and output decoding; clients reproduce them.

## Fetching

```bash
cd models
uv sync
uv run python fetch.py                       # download and verify into cache/
uv sync --group export && uv run python fetch.py --export   # also export the Marqo classifier (needs torch)
uv run python fetch.py --from-artifact ~/watchman-models   # copy verified files from an unpacked artifact
```

`fetch.py --write-hashes` records sha256 and sizes for entries that have none yet; it is how the
manifest was filled in the first time, and it is not something a household runs.

GitHub now sends anonymous downloads of the NudeNet release files to a sign-in page. With
`GITHUB_TOKEN` set (a token from any GitHub account; `gh auth token` prints one), `fetch.py` asks
the GitHub API for those assets instead. The token is sent to `api.github.com` only.

### The `watchman-models` artifact

A machine without torch cannot produce `nsfw-384.onnx` and `nsfw-384-int8.onnx`. The models
workflow has a job started by hand (Actions, models, Run workflow) that installs the export
toolchain, runs `fetch.py --export`, checks every file against the manifest a second time, and
keeps `cache/*.onnx`, `manifest.json` and `SHA256SUMS` as a workflow artifact named
`watchman-models`. Download it from that run (it expires with the repository's artifact retention,
90 days by default), unzip it, and run `fetch.py --from-artifact <folder>`. A file is copied only
if its sha256 and size match `manifest.json`, so the folder itself does not need to be trusted.
Nothing is attached to a release or published anywhere; downloading the artifact needs read access
to this repository.

The export is reproducible: on 2026-11-02 a fresh `uv sync --frozen --group export` on Windows
produced `nsfw-384.onnx` and `nsfw-384-int8.onnx` with the manifest's hashes byte for byte. If the
CI job ever reports a hash mismatch for them, the locked torch, timm, onnx or onnxruntime versions
changed the export, and the manifest needs new hashes from a reviewed run, not a looser check.

### What the licences allow us to redistribute

Hosting the files publicly (a release, a public bucket) is a separate decision; this is what the
licences say about it.

- Marqo/nsfw-image-detection-384 is Apache-2.0 (`docs/THIRD_PARTY.md`; the Hugging Face model card
  says the same, and the repository has no NOTICE file). Apache-2.0 allows redistributing the
  weights and derivative works, which includes our ONNX export and its int8 quantization, in any
  form and commercially, on three conditions: ship a copy of the Apache-2.0 licence with them,
  keep any attribution notices, and state that the files were changed (converted to ONNX, and for
  the int8 copy, dynamically quantized). It grants no trademark rights, so a copy must not be
  presented as Marqo's own release. A public artifact would therefore carry `LICENSE-Apache-2.0`
  and a short `NOTICE` naming the source and the conversion.
- AdamCodd/vit-base-nsfw-detector (the second opinion) is also Apache-2.0, with the same
  conditions. NudeNet's weights are MIT: keep the copyright notice and licence text with them.

## The image hash

`watchman_models/dhash.py` is the reference dHash (docs/api-contract.md, "Image hash") and
`make_vectors.py` writes `test-vectors/dhash/`: nine synthetic PNGs with their expected hashes.
Every client's test suite loads those files and must match them exactly.

## Evaluation

See also `eval/screenshots.py` and ADR-021: a household's own screenshots and photos are the
evaluation set; no positives are collected.

`models/eval/` scores a folder and reports per-model rates at several thresholds. False positives
are measured on a household's own photos (exported to a local temp folder, scored, results
kept as numbers only) and ordinary browsing screenshots. Positives are measured only on a folder the
household supplies locally; nothing explicit is downloaded or committed by tooling. Avatar-size
behaviour is measured by downscaling every evaluation image to 32, 48, 64 and 96 px before scoring.
