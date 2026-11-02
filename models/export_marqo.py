"""Export the Marqo nsfw-image-detection-384 classifier (timm ViT-tiny, safetensors) to ONNX.

Needs the export dependency group: `uv sync --group export`. Called by fetch.py --export.
The exported graph takes `input` (N, 3, 384, 384) float32, already normalised as the manifest
says, and returns `logits` (N, 2) in label order [NSFW, SFW].
"""

from __future__ import annotations

from pathlib import Path


def export_marqo(weights: Path, target: Path, architecture: str = "vit_tiny_patch16_384") -> None:
    import timm
    import torch
    from safetensors.torch import load_file

    model = timm.create_model(architecture, pretrained=False, num_classes=2)
    state = load_file(str(weights))
    missing, unexpected = model.load_state_dict(state, strict=False)
    if missing or unexpected:
        raise SystemExit(f"state dict mismatch: missing {missing}, unexpected {unexpected}")
    model.eval()
    dummy = torch.zeros(1, 3, 384, 384)
    torch.onnx.export(
        model, dummy, str(target), input_names=["input"], output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}}, opset_version=17, dynamo=False,
    )
    import onnx

    onnx.checker.check_model(str(target))
    print(f"  exported {target.name}")


if __name__ == "__main__":
    import sys

    export_marqo(Path(sys.argv[1]), Path(sys.argv[2]))
