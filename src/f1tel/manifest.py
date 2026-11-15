"""Per-session ingestion manifest: SHA-256 checksums of every written file."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    """Return the hex SHA-256 digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while block := fh.read(chunk):
            h.update(block)
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write bytes to ``path`` atomically (temp file + rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


@dataclass
class Manifest:
    """Checksums for the files of one ingested session directory."""

    root: Path
    files: dict[str, dict[str, Any]] = field(default_factory=dict)
    complete: bool = False

    @classmethod
    def load(cls, root: Path) -> Manifest:
        """Load ``manifest.json`` from ``root``; an empty manifest if missing or corrupt."""
        path = root / MANIFEST_NAME
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return cls(root, dict(raw.get("files", {})), bool(raw.get("complete", False)))
            except (json.JSONDecodeError, OSError):
                pass
        return cls(root)

    def save(self) -> None:
        """Persist the manifest atomically."""
        payload = {"version": MANIFEST_VERSION, "complete": self.complete, "files": self.files}
        atomic_write_bytes(
            self.root / MANIFEST_NAME, json.dumps(payload, indent=1, sort_keys=True).encode()
        )

    def record(self, rel: str, rows: int | None = None) -> str:
        """Record the checksum of ``root/rel`` and return it."""
        path = self.root / rel
        digest = sha256_file(path)
        entry: dict[str, Any] = {"sha256": digest, "bytes": path.stat().st_size}
        if rows is not None:
            entry["rows"] = rows
        self.files[rel] = entry
        return digest

    def is_intact(self, rel: str) -> bool:
        """True if ``rel`` exists and matches its recorded checksum."""
        entry = self.files.get(rel)
        path = self.root / rel
        return bool(entry) and path.exists() and sha256_file(path) == entry["sha256"]

    def verify(self) -> list[str]:
        """Return the relative paths whose file is missing or has a different checksum."""
        return [rel for rel in self.files if not self.is_intact(rel)]
