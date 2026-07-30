"""Content-addressed asset storage.

The property that matters is that re-exporting an unchanged scene writes nothing: geometry
extraction dominates export time, so this is the difference between a snappy re-render and
a ten-second stall every time the camera moves.
"""

from pathlib import Path

import pytest

from core.assets import AssetStore, content_hash, hash_file


def test_hash_is_sixteen_hex_chars() -> None:
    h = content_hash(b"hello")
    assert len(h) == 16
    assert all(c in "0123456789abcdef" for c in h)


def test_hash_is_content_dependent() -> None:
    assert content_hash(b"a") != content_hash(b"b")
    assert content_hash(b"a") == content_hash(b"a")


def test_hash_file_matches_hash_bytes(tmp_path: Path) -> None:
    p = tmp_path / "x.bin"
    p.write_bytes(b"some content")
    assert hash_file(p) == content_hash(b"some content")


def test_paths_are_relative_and_forward_slashed(tmp_path: Path) -> None:
    """The path goes into the IR, which is JSON that may be read across a process boundary
    and checked in as a fixture. A Windows backslash in a fixture is not portable."""
    store = AssetStore(root=tmp_path)
    rel = store.add_bytes(b"ply data", subdir="meshes", ext=".ply")
    assert rel.startswith("meshes/")
    assert "\\" not in rel
    assert not Path(rel).is_absolute()


def test_identical_content_is_written_once(tmp_path: Path) -> None:
    store = AssetStore(root=tmp_path)
    a = store.add_bytes(b"same", subdir="meshes", ext=".ply")
    b = store.add_bytes(b"same", subdir="meshes", ext=".ply")
    assert a == b
    assert store.written == 1
    assert store.reused == 1


def test_different_content_gets_different_files(tmp_path: Path) -> None:
    store = AssetStore(root=tmp_path)
    a = store.add_bytes(b"one", subdir="meshes", ext=".ply")
    b = store.add_bytes(b"two", subdir="meshes", ext=".ply")
    assert a != b
    assert store.written == 2


def test_content_is_readable_back(tmp_path: Path) -> None:
    store = AssetStore(root=tmp_path)
    rel = store.add_bytes(b"payload", subdir="meshes", ext=".ply")
    assert (tmp_path / rel).read_bytes() == b"payload"


def test_no_part_files_are_left_behind(tmp_path: Path) -> None:
    """Writes go to a `.part` sibling and are renamed, so a crash mid-write cannot leave a
    truncated file sitting at a hash that now claims to be complete."""
    store = AssetStore(root=tmp_path)
    store.add_bytes(b"payload", subdir="meshes", ext=".ply")
    assert list(tmp_path.rglob("*.part")) == []


def test_add_file_copies_under_its_hash(tmp_path: Path) -> None:
    src = tmp_path / "src" / "wood.PNG"
    src.parent.mkdir()
    src.write_bytes(b"png bytes")
    store = AssetStore(root=tmp_path / "out")
    rel = store.add_file(src, source="Material#3 base_color_map")
    assert rel == f"textures/{content_hash(b'png bytes')}.png"     # extension lowercased
    assert (tmp_path / "out" / rel).read_bytes() == b"png bytes"


def test_add_file_dedups(tmp_path: Path) -> None:
    """Two materials pointing at the same bitmap share one file, with no dedup pass."""
    src = tmp_path / "t.png"
    src.write_bytes(b"pixels")
    store = AssetStore(root=tmp_path / "out")
    assert store.add_file(src) == store.add_file(src)
    assert store.written == 1
    assert store.reused == 1


def test_manifest_records_provenance(tmp_path: Path) -> None:
    """When a user asks why a texture looks wrong, "which of these 400 hashes is it" is
    otherwise unanswerable."""
    store = AssetStore(root=tmp_path)
    rel = store.add_bytes(b"data", subdir="meshes", ext=".ply", source="Teapot001")
    path = store.write_manifest()
    assert path.exists()
    import json
    assert json.loads(path.read_text())[rel] == "Teapot001"


def test_missing_source_file_raises(tmp_path: Path) -> None:
    store = AssetStore(root=tmp_path)
    with pytest.raises(FileNotFoundError):
        store.add_file(tmp_path / "does-not-exist.png")
