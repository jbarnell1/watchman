"""Encrypted image storage. Each blurred image is an AES-GCM sealed WebP on disk (ADR-004).

Layout: <images_dir>/<event_id>.<level>.bin, where level is heavy or medium. The key lives in
<keys_dir>/images.key (32 random bytes, mode 600) and is created on first use.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

LEVELS = ("heavy", "medium")


class BlobStore:
    def __init__(self, images_dir: Path, keys_dir: Path) -> None:
        self.images_dir = images_dir
        self.keys_dir = keys_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.keys_dir.mkdir(parents=True, exist_ok=True)
        self._key = self._load_or_create_key()

    def _load_or_create_key(self) -> bytes:
        path = self.keys_dir / "images.key"
        if path.exists():
            return path.read_bytes()
        key = secrets.token_bytes(32)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    def _path(self, event_id: str, level: str) -> Path:
        if level not in LEVELS:
            raise ValueError(level)
        return self.images_dir / f"{event_id}.{level}.bin"

    def put(self, event_id: str, level: str, data: bytes) -> None:
        nonce = secrets.token_bytes(12)
        sealed = AESGCM(self._key).encrypt(nonce, data, event_id.encode())
        self._path(event_id, level).write_bytes(nonce + sealed)

    def get(self, event_id: str, level: str) -> bytes | None:
        path = self._path(event_id, level)
        if not path.exists():
            return None
        raw = path.read_bytes()
        return AESGCM(self._key).decrypt(raw[:12], raw[12:], event_id.encode())

    def delete(self, event_id: str) -> None:
        for level in LEVELS:
            self._path(event_id, level).unlink(missing_ok=True)

    def exists(self, event_id: str, level: str = "heavy") -> bool:
        return self._path(event_id, level).exists()
