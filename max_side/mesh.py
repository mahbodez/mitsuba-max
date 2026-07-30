"""Geometry extraction from 3ds Max.

`pymxs` marshals every call through the MAXScript VM, so the shape of this module is
dictated entirely by call *count*. Probe 06c measured the alternatives on a 1M-triangle
sphere:

    meshop.getVerts bulk + attribute access      1.31 s
    MAXScript loop -> binary file -> struct      1.39 s   (positions)
    MAXScript loop -> binary file -> struct      8.23 s   (faces: 5 calls per face)

Positions are a wash, so the binary-file route is used throughout for consistency: one
MAXScript function per array, one file, one `struct.unpack`. Faces dominate either way,
because each one needs `getFace`, `getTVFace`, `getFaceSmoothGroup` and `getFaceMatID`.

The MAXScript source below is assembled from a list joined with newlines. MAXScript has no
statement terminator, so concatenating `local n = getNumVerts m` and `local out = #()` onto
one line is a parse error that reports itself somewhere unrelated — that mistake cost probe
06b three of its four measurements.
"""

import struct
import tempfile
import uuid
from pathlib import Path

from pymxs import runtime as rt

from core.meshbuild import FACE_STRIDE, RawMesh

__all__ = ["extract_raw_mesh", "install_maxscript_helpers", "node_is_mirrored"]

_HELPERS_INSTALLED = False

_DUMP_VERTS = (
    "fn mmx_dump_verts m path = (",
    "  local f = fopen path \"wb\"",
    "  local n = getNumVerts m",
    "  for i = 1 to n do (",
    "    local p = getVert m i",
    "    WriteFloat f p.x",
    "    WriteFloat f p.y",
    "    WriteFloat f p.z",
    "  )",
    "  fclose f",
    "  n",
    ")",
)

_DUMP_TVERTS = (
    "fn mmx_dump_tverts m path = (",
    "  local f = fopen path \"wb\"",
    "  local n = getNumTVerts m",
    "  for i = 1 to n do (",
    "    local p = getTVert m i",
    "    WriteFloat f p.x",
    "    WriteFloat f p.y",
    "  )",
    "  fclose f",
    "  n",
    ")",
)

# One pass over the faces emitting eight longs each, matching core.meshbuild.FACE_STRIDE:
# v0 v1 v2 t0 t1 t2 smoothing_group material_id. Indices are written 0-based here so the
# Python side never has to walk the array again just to subtract one.
_DUMP_FACES = (
    "fn mmx_dump_faces m path = (",
    "  local f = fopen path \"wb\"",
    "  local n = getNumFaces m",
    "  local hasuv = (getNumTVerts m) > 0",
    "  for i = 1 to n do (",
    "    local t = getFace m i",
    "    WriteLong f ((t.x as integer) - 1)",
    "    WriteLong f ((t.y as integer) - 1)",
    "    WriteLong f ((t.z as integer) - 1)",
    "    if hasuv then (",
    "      local u = getTVFace m i",
    "      WriteLong f ((u.x as integer) - 1)",
    "      WriteLong f ((u.y as integer) - 1)",
    "      WriteLong f ((u.z as integer) - 1)",
    "    ) else (",
    "      WriteLong f -1",
    "      WriteLong f -1",
    "      WriteLong f -1",
    "    )",
    "    WriteLong f (getFaceSmoothGroup m i)",
    "    WriteLong f (getFaceMatID m i)",
    "  )",
    "  fclose f",
    "  n",
    ")",
)


def install_maxscript_helpers() -> None:
    """Define the dump functions in the MAXScript global scope. Idempotent.

    Called on every extraction rather than at import, so a developer reload that purges
    this module does not leave Max holding stale function definitions.
    """
    global _HELPERS_INSTALLED
    if _HELPERS_INSTALLED:
        return
    for source in (_DUMP_VERTS, _DUMP_TVERTS, _DUMP_FACES):
        rt.execute("\n".join(source))
    _HELPERS_INSTALLED = True


def node_is_mirrored(node) -> bool:
    """True when the node's world transform has negative determinant.

    `snapshotAsMesh` bakes the transform into the vertices, so a mirrored node arrives with
    inside-out triangles and no other trace of what happened. The determinant of the node
    transform is the only surviving evidence, and it has to be read before the snapshot.

    Max stores `transform.row1..row3` as the local axes in world space, so the 3x3
    determinant is the scalar triple product of those three rows.
    """
    m = node.transform
    a = m.row1
    b = m.row2
    c = m.row3
    return (
        float(a.x) * (float(b.y) * float(c.z) - float(b.z) * float(c.y))
        - float(a.y) * (float(b.x) * float(c.z) - float(b.z) * float(c.x))
        + float(a.z) * (float(b.x) * float(c.y) - float(b.y) * float(c.x))
    ) < 0.0


def _scratch(prefix: str) -> Path:
    return Path(tempfile.gettempdir()) / f"mmx_{prefix}_{uuid.uuid4().hex}.bin"


def _dump(fn, mesh, path: Path) -> bytes:
    """Run a MAXScript dump function and read back what it wrote.

    MAXScript's `fopen` wants forward slashes; a Windows path with backslashes goes in as
    escape sequences and produces a file with a mangled name, or none at all.
    """
    fn(mesh, str(path).replace("\\", "/"))
    try:
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def extract_raw_mesh(node) -> RawMesh:
    """Snapshot a node and read its arrays out as a `core.meshbuild.RawMesh`.

    `rt.snapshotAsMesh` evaluates the whole modifier stack and returns a `TriMesh` in
    **world space** (probe 06: a teapot at `[100, 20, 5]` had vertex 1 at
    `[106.06, 23.5, 17.0]`). World space is what v1 wants. It is also what makes instancing
    impossible, so the M5 path will need `inverse(node.objectTransform)` applied to recover
    object space — noted here because that is the line that will need to change.

    Positions come back in Max's own system units and Z-up convention. Converting them is
    `core.meshbuild`'s job, once, so that nothing here has to know about Mitsuba.
    """
    install_maxscript_helpers()
    mesh = rt.snapshotAsMesh(node)

    verts_blob = _dump(rt.mmx_dump_verts, mesh, _scratch("verts"))
    faces_blob = _dump(rt.mmx_dump_faces, mesh, _scratch("faces"))
    tverts_blob = _dump(rt.mmx_dump_tverts, mesh, _scratch("tverts"))

    positions = list(struct.unpack(f"<{len(verts_blob) // 4}f", verts_blob))
    faces = list(struct.unpack(f"<{len(faces_blob) // 4}i", faces_blob))
    tverts = list(struct.unpack(f"<{len(tverts_blob) // 4}f", tverts_blob))

    if len(faces) % FACE_STRIDE:
        raise ValueError(
            f"{node.name}: face dump is {len(faces)} ints, not a multiple of {FACE_STRIDE}"
        )

    return RawMesh(positions=positions, faces=faces, tverts=tverts, name=str(node.name))
