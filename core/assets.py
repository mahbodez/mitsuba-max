"""Content-addressed asset storage for meshes and textures.

Every file the exporter writes is named by the hash of its contents:
`meshes/<sha1-16>.ply`, `textures/<sha1-16>.png`. Two consequences, both of which matter
for interactive use:

* Re-exporting a scene where only the camera moved rewrites nothing. Geometry extraction
  dominates export time on a heavy scene, so this is the difference between a snappy
  re-render and a ten-second stall.
* Two nodes with identical geometry, or two materials pointing at the same bitmap, share
  one file automatically. No dedup pass, no bookkeeping.

Truncating SHA-1 to 16 hex characters gives 64 bits. Collision probability across a
100 000-asset scene is around 2.7e-10 — far below the probability of a disk error, and
this is a cache key, not a security boundary.
"""

import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["AssetStore", "content_hash", "hash_file"]

_HASH_CHARS = 16
_CHUNK = 1 << 20


def content_hash(data: bytes) -> str:
    """First 16 hex characters of the SHA-1 of `data`."""
    return hashlib.sha1(data, usedforsecurity=False).hexdigest()[:_HASH_CHARS]


def hash_file(path: str | os.PathLike[str]) -> str:
    """As `content_hash` but streams the file, so a 2 GB EXR does not become 2 GB of RAM."""
    h = hashlib.sha1(usedforsecurity=False)
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()[:_HASH_CHARS]


@dataclass(slots=True)
class AssetStore:
    """Writes assets under `root` and records where each one came from.

    The manifest maps the stored relative path back to the originating Max node or source
    file. It is diagnostic only — nothing reads it to render — but when a user asks why a
    texture looks wrong, "which of these 400 hashes is it" is otherwise unanswerable.
    """

    root: Path
    manifest: dict[str, str] = field(default_factory=dict)
    written: int = 0
    reused: int = 0

    def __post_init__(self) -> None:
        self.root = Path(self.root)

    def _target(self, subdir: str, digest: str, ext: str) -> tuple[Path, str]:
        rel = f"{subdir}/{digest}{ext}"
        return self.root / subdir / f"{digest}{ext}", rel

    def add_bytes(self, data: bytes, *, subdir: str, ext: str, source: str = "") -> str:
        """Store `data`, returning its path relative to `root` with forward slashes.

        Relative and forward-slashed because the path goes into the IR, which is JSON that
        may be read on the other side of a process boundary and checked in as a fixture.
        """
        digest = content_hash(data)
        abs_path, rel = self._target(subdir, digest, ext)
        if source:
            self.manifest[rel] = source
        if abs_path.exists() and abs_path.stat().st_size == len(data):
            self.reused += 1
            return rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temporary sibling and rename, so a crash mid-write cannot leave a
        # truncated file sitting at a hash that now claims to be complete.
        tmp = abs_path.with_suffix(abs_path.suffix + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, abs_path)
        self.written += 1
        return rel

    def add_file(self, src: str | os.PathLike[str], *, subdir: str = "textures",
                 source: str = "") -> str:
        """Copy an existing file (a bitmap on disk) into the store under its content hash."""
        src_path = Path(src)
        digest = hash_file(src_path)
        ext = src_path.suffix.lower()
        abs_path, rel = self._target(subdir, digest, ext)
        self.manifest[rel] = source or str(src_path)
        if abs_path.exists() and abs_path.stat().st_size == src_path.stat().st_size:
            self.reused += 1
            return rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = abs_path.with_suffix(abs_path.suffix + ".part")
        shutil.copyfile(src_path, tmp)
        os.replace(tmp, abs_path)
        self.written += 1
        return rel

    def write_manifest(self, name: str = "manifest.json") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True), encoding="utf-8")
        return path
