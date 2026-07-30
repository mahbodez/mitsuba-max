"""Probe 06c - a mesh extraction path that survives 2M triangles.

Probe 06b measured the obvious route at ~0.58M verts/s and its three alternatives all died
on MAXScript syntax: the generated source was concatenated without separators, so
`local n = getNumVerts m local out = #()` parsed as nonsense. Fixed here by joining with
newlines, which is the only reason those candidates failed.

The question this has to answer is a budget one. SPEC 8.1 wants 2M triangles in about 10 s.
Per-face Python calls need four round trips per face (getFace, getTVFace,
getFaceSmoothGroup, getFaceMatID) at roughly 0.4M calls/s, which is 20 s for 2M faces
before any UVs are looked at. So either a bulk path exists or the budget does not.

    python tools/maxbatch.py tools/probes/06c_mesh_fastpath.py
"""

import os
import struct
import tempfile
import time

from pymxs import runtime as rt

TMP = os.path.join(tempfile.gettempdir(), "mmx_probe")
os.makedirs(TMP, exist_ok=True)


def mxs(*lines):
    """Define MAXScript from a list of lines. Joined with newlines, never concatenated —
    MAXScript has no statement terminator, so `a = 1 b = 2` on one line is a parse error
    that reports itself somewhere unhelpful."""
    return rt.execute("\n".join(lines))


rt.resetMaxFile(rt.name("noPrompt"))

print("--- build benchmark mesh ---")
big = rt.Sphere(radius=10.0, segments=1000, mapCoords=True, pos=rt.Point3(0, 0, 0))
bm = rt.snapshotAsMesh(big)
NV = int(rt.getNumVerts(bm))
NF = int(rt.getNumFaces(bm))
NT = int(rt.getNumTVerts(bm))
print("  %d verts, %d faces (%.2f M tris), %d tverts" % (NV, NF, NF / 1e6, NT))


def timed(label, fn):
    t0 = time.perf_counter()
    try:
        note = fn()
    except Exception as exc:
        print("  %-42s FAILED  %s: %s" % (label, type(exc).__name__, exc))
        return None
    dt = time.perf_counter() - t0
    print("  %-42s %8.3f s  %6.2f Mtri/s   %s" % (label, dt, NF / dt / 1e6, note))
    return dt


# --------------------------------------------------------------------------------------
# candidate 1: bulk fetch then per-component attribute access (the probe 06b baseline)
# --------------------------------------------------------------------------------------
def bulk_attrs():
    ba = rt.execute("#{1..%d}" % NV)
    arr = rt.meshop.getVerts(bm, ba)
    out = [None] * NV
    for i in range(NV):
        p = arr[i]
        out[i] = (p.x, p.y, p.z)
    return "%d verts" % len(out)


# --------------------------------------------------------------------------------------
# candidate 2: MAXScript writes a raw binary blob, Python reads the file
# --------------------------------------------------------------------------------------
mxs(
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

mxs(
    "fn mmx_dump_faces m path = (",
    "  local f = fopen path \"wb\"",
    "  local n = getNumFaces m",
    "  local hasuv = (getNumTVerts m) > 0",
    "  for i = 1 to n do (",
    "    local t = getFace m i",
    "    WriteLong f (t.x as integer)",
    "    WriteLong f (t.y as integer)",
    "    WriteLong f (t.z as integer)",
    "    if hasuv then (",
    "      local u = getTVFace m i",
    "      WriteLong f (u.x as integer)",
    "      WriteLong f (u.y as integer)",
    "      WriteLong f (u.z as integer)",
    "    ) else (",
    "      WriteLong f 0",
    "      WriteLong f 0",
    "      WriteLong f 0",
    "    )",
    "    WriteLong f (getFaceSmoothGroup m i)",
    "    WriteLong f (getFaceMatID m i)",
    "  )",
    "  fclose f",
    "  n",
    ")",
)

mxs(
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


def _fspath(name):
    return os.path.join(TMP, name).replace("\\", "/")


def binary_verts():
    path = _fspath("verts.bin")
    n = int(rt.mmx_dump_verts(bm, path))
    with open(path, "rb") as fh:
        data = fh.read()
    vals = struct.unpack("<%df" % (len(data) // 4), data)
    os.remove(path)
    return "%d verts, %d floats, v0=(%.4f, %.4f, %.4f)" % (n, len(vals), *vals[:3])


def binary_faces():
    path = _fspath("faces.bin")
    n = int(rt.mmx_dump_faces(bm, path))
    with open(path, "rb") as fh:
        data = fh.read()
    vals = struct.unpack("<%dl" % (len(data) // 4), data)
    os.remove(path)
    return "%d faces, 8 longs each, f0=%s" % (n, vals[:8])


def binary_tverts():
    path = _fspath("tverts.bin")
    n = int(rt.mmx_dump_tverts(bm, path))
    with open(path, "rb") as fh:
        data = fh.read()
    vals = struct.unpack("<%df" % (len(data) // 4), data)
    os.remove(path)
    return "%d tverts, uv0=(%.4f, %.4f)" % (n, vals[0], vals[1])


# --------------------------------------------------------------------------------------
# candidate 3: numpy over the binary blob, which is what the real writer would do
# --------------------------------------------------------------------------------------
def binary_verts_numpy():
    import numpy as np

    path = _fspath("verts_np.bin")
    rt.mmx_dump_verts(bm, path)
    arr = np.fromfile(path, dtype="<f4").reshape(-1, 3)
    os.remove(path)
    return "ndarray %s, bbox x %.3f..%.3f" % (arr.shape, arr[:, 0].min(), arr[:, 0].max())


print("--- candidates ---")
timed("(1) bulk getVerts + attribute access", bulk_attrs)
timed("(2) MAXScript -> binary -> struct (verts)", binary_verts)
timed("(2) MAXScript -> binary -> struct (faces)", binary_faces)
timed("(2) MAXScript -> binary -> struct (tverts)", binary_tverts)
timed("(3) MAXScript -> binary -> numpy (verts)", binary_verts_numpy)

print("--- correctness cross-check ---")
try:
    path = _fspath("check.bin")
    rt.mmx_dump_verts(bm, path)
    with open(path, "rb") as fh:
        data = fh.read()
    flat = struct.unpack("<%df" % (len(data) // 4), data)
    os.remove(path)
    ok = True
    for idx in (1, 2, NV // 2, NV):
        direct = rt.getVert(bm, idx)
        got = flat[(idx - 1) * 3: idx * 3]
        same = (abs(direct.x - got[0]) < 1e-4 and abs(direct.y - got[1]) < 1e-4
                and abs(direct.z - got[2]) < 1e-4)
        ok = ok and same
        print("  vert %-8d direct=(%.4f, %.4f, %.4f)  file=(%.4f, %.4f, %.4f)  %s"
              % (idx, direct.x, direct.y, direct.z, got[0], got[1], got[2],
                 "match" if same else "MISMATCH"))
    print("  binary dump agrees with getVert: %s" % ok)
except Exception as exc:
    print("  cross-check FAILED %s: %s" % (type(exc).__name__, exc))

print("--- numpy availability inside Max ---")
try:
    import numpy
    print("  numpy %s at %s" % (numpy.__version__, numpy.__file__))
except Exception as exc:
    print("  numpy NOT AVAILABLE  %s: %s" % (type(exc).__name__, exc))

print("--- fopen mode check (does 'wb' truncate?) ---")
try:
    p = _fspath("modecheck.bin")
    rt.execute("(local f = fopen \"%s\" \"wb\"\nWriteLong f 1\nfclose f)" % p)
    first = os.path.getsize(p)
    rt.execute("(local f = fopen \"%s\" \"wb\"\nWriteLong f 1\nfclose f)" % p)
    second = os.path.getsize(p)
    os.remove(p)
    print("  size after one write=%d, after rewrite=%d (equal means 'wb' truncates)"
          % (first, second))
except Exception as exc:
    print("  FAILED %s: %s" % (type(exc).__name__, exc))

print("PROBE_COMPLETE")
