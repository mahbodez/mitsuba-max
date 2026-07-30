"""Probe 06b - UV access on a mesh that actually has UVs, and how to move 2M triangles.

Probe 06 answered the shape of the mesh API but two of its questions were void: the teapot
was created with mapCoords off, so getNumTVerts was 0, and meshop.getMapVerts /
getMapFaces turned out not to exist at all. This probe uses a mesh with UVs and searches
for a bulk path that is fast enough for the SPEC 8.1 target of 2M triangles in ~10 s.

    python tools/maxbatch.py tools/probes/06b_mesh_uv_perf.py
"""

import os
import struct
import tempfile
import time

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-40s %r" % (label + ":", v))
        return v
    except Exception as exc:
        print("%-40s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))
        return None


rt.resetMaxFile(rt.name("noPrompt"))

box = rt.Box(width=10.0, length=10.0, height=10.0, mapCoords=True,
             pos=rt.Point3(0, 0, 0))
mesh = rt.snapshotAsMesh(box)

print("--- uv presence ---")
show("getNumVerts", lambda: int(rt.getNumVerts(mesh)))
show("getNumFaces", lambda: int(rt.getNumFaces(mesh)))
show("getNumTVerts", lambda: int(rt.getNumTVerts(mesh)))
show("meshop.getNumMaps", lambda: int(rt.meshop.getNumMaps(mesh)))
show("meshop.getMapSupport(mesh,1)", lambda: bool(rt.meshop.getMapSupport(mesh, 1)))
show("meshop.getNumMapVerts(mesh,1)", lambda: int(rt.meshop.getNumMapVerts(mesh, 1)))
show("meshop.getNumMapFaces(mesh,1)", lambda: int(rt.meshop.getNumMapFaces(mesh, 1)))

print("--- uv accessors ---")
show("getTVert(mesh, 1)", lambda: [float(c) for c in rt.getTVert(mesh, 1)])
show("getTVFace(mesh, 1)", lambda: [int(c) for c in rt.getTVFace(mesh, 1)])
show("meshop.getMapVert(mesh,1,1)", lambda: [float(c) for c in rt.meshop.getMapVert(mesh, 1, 1)])
show("meshop.getMapFace(mesh,1,1)", lambda: [int(c) for c in rt.meshop.getMapFace(mesh, 1, 1)])
show("getFace vs getTVFace differ",
     lambda: [int(c) for c in rt.getFace(mesh, 1)] != [int(c) for c in rt.getTVFace(mesh, 1)])

print("--- transform access ---")
show("box.objectTransform.row4",
     lambda: [float(c) for c in box.objectTransform.row4])
show("box.transform.row1", lambda: [float(c) for c in box.transform.row1])
show("box.transform.row4", lambda: [float(c) for c in box.transform.row4])
show("inverse(objectTransform).row4",
     lambda: [float(c) for c in rt.inverse(box.objectTransform).row4])
show("box.baseObject handle", lambda: int(rt.getHandleByAnim(box.baseObject)))

print("--- material id spread ---")
show("getFaceMatID(1..6)", lambda: [int(rt.getFaceMatID(mesh, i)) for i in range(1, 7)])

print("--- smoothing groups ---")
show("getFaceSmoothGroup(1..6)", lambda: [int(rt.getFaceSmoothGroup(mesh, i)) for i in range(1, 7)])

# ------------------------------------------------------------------------------------
# throughput
# ------------------------------------------------------------------------------------
print("--- benchmark mesh ---")
rt.delete(box)
big = rt.Sphere(radius=10.0, segments=700, mapCoords=True, pos=rt.Point3(0, 0, 0))
bm = rt.snapshotAsMesh(big)
NV = int(rt.getNumVerts(bm))
NF = int(rt.getNumFaces(bm))
print("  %d verts, %d faces (%.2f M tris)" % (NV, NF, NF / 1e6))


def timed(label, fn):
    t0 = time.perf_counter()
    try:
        note = fn()
    except Exception as exc:
        print("  %-40s FAILED  %s: %s" % (label, type(exc).__name__, exc))
        return None
    dt = time.perf_counter() - t0
    print("  %-40s %8.3f s   %s   (%.2f Mtri/s)" % (label, dt, note, NF / dt / 1e6))
    return dt


# (a) the obvious approach: bulk fetch, then marshal every component into Python
def a_bulk_then_attrs():
    ba = rt.execute("#{1..%d}" % NV)
    arr = rt.meshop.getVerts(bm, ba)
    out = []
    for i in range(1, NV + 1):
        p = arr[i - 1]
        out.append((p.x, p.y, p.z))
    return "%d verts" % len(out)


# (b) flatten to a float array inside MAXScript, marshal one flat sequence
def b_maxscript_flatten():
    rt.execute(
        "fn mmx_flatten m = ("
        "  local n = getNumVerts m"
        "  local out = #()"
        "  out[n*3] = 0.0"
        "  for i = 1 to n do ("
        "    local p = getVert m i"
        "    out[i*3-2] = p.x; out[i*3-1] = p.y; out[i*3] = p.z"
        "  )"
        "  out"
        ")"
    )
    flat = rt.mmx_flatten(bm)
    vals = list(flat)
    return "%d floats" % len(vals)


# (c) have MAXScript write a binary blob and read it back with the filesystem as the
#     transport. Zero per-element marshalling; Python sees one file.
def c_maxscript_binary_file():
    path = os.path.join(tempfile.gettempdir(), "mmx_probe_verts.bin").replace("\\", "/")
    rt.execute(
        "fn mmx_dump m path = ("
        "  local f = fopen path \"wb\""
        "  local n = getNumVerts m"
        "  for i = 1 to n do ("
        "    local p = getVert m i"
        "    WriteFloat f p.x; WriteFloat f p.y; WriteFloat f p.z"
        "  )"
        "  fclose f"
        "  n"
        ")"
    )
    n = int(rt.mmx_dump(bm, path))
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        data = fh.read()
    vals = struct.unpack("<%df" % (len(data) // 4), data)
    os.remove(path)
    return "%d verts, %d bytes, first=%.4f" % (n, size, vals[0])


# (d) same, for faces - the index buffer is the half nobody benchmarks
def d_faces_binary_file():
    path = os.path.join(tempfile.gettempdir(), "mmx_probe_faces.bin").replace("\\", "/")
    rt.execute(
        "fn mmx_dumpf m path = ("
        "  local f = fopen path \"wb\""
        "  local n = getNumFaces m"
        "  for i = 1 to n do ("
        "    local t = getFace m i"
        "    WriteLong f (t.x as integer); WriteLong f (t.y as integer); WriteLong f (t.z as integer)"
        "    WriteLong f (getFaceSmoothGroup m i)"
        "    WriteLong f (getFaceMatID m i)"
        "  )"
        "  fclose f"
        "  n"
        ")"
    )
    n = int(rt.mmx_dumpf(bm, path))
    size = os.path.getsize(path)
    os.remove(path)
    return "%d faces, %d bytes" % (n, size)


timed("(a) bulk getVerts + attribute access", a_bulk_then_attrs)
timed("(b) MAXScript flatten -> float array", b_maxscript_flatten)
timed("(c) MAXScript -> binary file -> struct", c_maxscript_binary_file)
timed("(d) faces+sg+matid -> binary file", d_faces_binary_file)

print("PROBE_COMPLETE")
