"""What /healthz reports: the server version, how many model files are present and match the
manifest, and whether the second opinion model is loaded. No personal data."""

from __future__ import annotations

import hashlib
import json
import logging
from importlib import metadata
from pathlib import Path

log = logging.getLogger(__name__)


def server_version() -> str:
    try:
        return metadata.version("watchman-server")
    except metadata.PackageNotFoundError:
        return "unknown"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verified_models(models_dir: Path) -> int:
    """Count the manifest's files that exist in models_dir with the manifest's sha256. Run once at
    startup; a few hundred megabytes of hashing takes about a second."""
    manifest = models_dir / "manifest.json"
    if not manifest.is_file():
        return 0
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8")).get("models", [])
    except (OSError, ValueError):
        log.warning("models manifest at %s is not readable", manifest)
        return 0
    count = 0
    for entry in entries:
        name, sha = Path(str(entry.get("file", ""))).name, entry.get("sha256")
        path = models_dir / name
        if not (name and sha and path.is_file()):
            continue
        if _sha256(path) == sha:
            count += 1
        else:
            log.warning("model %s does not match the manifest's sha256", name)
    return count
