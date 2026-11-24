"""The client files this server carries (ADR-060).

A release image has the Android APKs, the Windows MSI and the extension zips of its own version
under the downloads directory, with a SHA256SUMS file. Devices and new households get their
clients from their own server, so nothing on a device asks GitHub for updates. A server built
from source has no files and says where to get them.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_VERSION = r"(?P<version>\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)"
_PATTERNS = [
    ("android", re.compile(rf"^watchman-android-{_VERSION}-(?P<variant>arm64-v8a|armeabi-v7a|x86_64)\.apk$")),
    ("windows", re.compile(rf"^watchman-windows-{_VERSION}-(?P<variant>x64)\.msi$")),
    ("extension", re.compile(rf"^watchman-extension-(?P<variant>chrome|firefox)-{_VERSION}\.zip$")),
]

# Which bundled client each device platform (docs/api-contract.md, pairing) installs.
_PLATFORM_CLIENT = {"android": "android", "windows": "windows", "chrome": "extension", "edge": "extension",
                    "firefox": "extension", "firefox-android": "extension"}

MEDIA_TYPES = {".apk": "application/vnd.android.package-archive", ".msi": "application/x-msi",
               ".zip": "application/zip"}


@dataclass(frozen=True)
class ClientFile:
    name: str
    client: str        # android | windows | extension
    variant: str       # arm64-v8a, x64, chrome ...
    version: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Bundle:
    version: str
    files: tuple[ClientFile, ...]

    def get(self, name: str) -> ClientFile | None:
        return next((f for f in self.files if f.name == name), None)

    def of(self, client: str) -> list[ClientFile]:
        return [f for f in self.files if f.client == client]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@lru_cache(maxsize=4)
def load(directory: Path) -> Bundle | None:
    """Read SHA256SUMS and keep the client files that exist and match it. The directory is part
    of the image and does not change while the server runs, so this is read once."""
    sums = directory / "SHA256SUMS"
    if not sums.is_file():
        return None
    files = []
    for line in sums.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].lstrip("*")
        match = next(((client, m) for client, p in _PATTERNS if (m := p.match(name))), None)
        path = directory / name
        if match is None or not path.is_file():
            continue
        if _sha256(path) != digest:
            log.warning("bundled client %s does not match SHA256SUMS; not offered", name)
            continue
        client, m = match
        files.append(ClientFile(name, client, m["variant"], m["version"], digest, path.stat().st_size))
    versions = {f.version for f in files}
    if len(versions) != 1:
        if versions:
            log.warning("bundled clients have more than one version (%s); none offered", ", ".join(sorted(versions)))
        return None
    return Bundle(versions.pop(), tuple(files))


def parse_version(v: str | None) -> tuple | None:
    """Order versions: 0.2.0-rc.1 < 0.2.0 < 0.2.1. None when the text is not a version."""
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.]+))?", (v or "").strip())
    if not m:
        return None
    major, minor, patch, pre = int(m[1]), int(m[2]), int(m[3]), m[4]
    return (major, minor, patch, 0, pre) if pre else (major, minor, patch, 1, "")


def update_for(bundle: Bundle | None, platform: str, app_version: str | None) -> str | None:
    """The version a device should move to, or None when it is current, newer, or not something
    this server carries (iOS and Safari, for example)."""
    if bundle is None:
        return None
    client = _PLATFORM_CLIENT.get(platform)
    if client is None or not bundle.of(client):
        return None
    have, latest = parse_version(app_version), parse_version(bundle.version)
    if have is None or latest is None or have >= latest:
        return None
    return bundle.version
