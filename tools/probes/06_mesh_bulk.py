"""Probe 06 (+11) - mesh bulk accessors: signatures, return types, index base, speed.

Builds its own geometry. Writes nothing. Loads nothing.

    python tools/maxbatch.py tools/probes/06_mesh_bulk.py

Answers:
  06  exact signatures and return types of the meshop / mesh accessors, whether indices
      are 1-based, and what bulk extraction actually costs per million triangles
  11  whether rt.getHandleByAnim works on a node's baseObject (the M5 instancing key)
  8.2 whether snapshotAsMesh really returns world-space vertices
"""

import time

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-38s %r" % (label + ":", v))
        return v
    except Exception as exc:
        print("%-38s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))
        return None


def kind(label, fn):
    try:
        v = fn()
        head = None
        try:
            head = [v[i] for i in range(min(3, len(v)))]
        except Exception:
            head = "(not indexable)"
        print("%-38s type=%-22s len=%-9s head=%s"
              % (label + ":", type(v).__name__, _len(v), head))
        return v
    except Exception as exc:
        print("%-38s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))
        return None


def _len(v):
    try:
        return len(v)
    except Exception:
        return "?"


rt.resetMaxFile(rt.name("noPrompt"))

# Off-origin and rotated: the only way to tell world space from object space.
teapot = rt.Teapot(radius=10.0, segments=4, pos=rt.Point3(100.0, 20.0, 5.0))
rt.rotate(teapot, rt.eulerAngles(0, 0, 30))

print("--- snapshotAsMesh ---")
mesh = rt.snapshotAsMesh(teapot)
show("classOf(mesh)", lambda: str(rt.classOf(mesh)))
show("node.pos", lambda: [float(teapot.pos.x), float(teapot.pos.y), float(teapot.pos.z)])
show("getVert(mesh, 1)", lambda: [float(c) for c in rt.getVert(mesh, 1)])
print("  NOTE: if getVert(1) sits near node.pos, the snapshot is world space (SPEC 8.2).")

print("--- counts ---")
nv = show("getNumVerts", lambda: int(rt.getNumVerts(mesh)))
nf = show("getNumFaces", lambda: int(rt.getNumFaces(mesh)))
show("getNumTVerts", lambda: int(rt.getNumTVerts(mesh)))
show("meshop.getNumMaps", lambda: int(rt.meshop.getNumMaps(mesh)))
show("getNumCPVVerts", lambda: int(rt.getNumCPVVerts(mesh)))

print("--- index base ---")
show("getVert index 0 (expect fail)", lambda: rt.getVert(mesh, 0))
show("getVert index nv (last)", lambda: [float(c) for c in rt.getVert(mesh, nv)])
show("getVert index nv+1 (expect fail)", lambda: rt.getVert(mesh, nv + 1))

print("--- bitarray literal ---")
allv = show("execute #{1..nv}", lambda: rt.execute("#{1..%d}" % nv))
show("bitarray numberSet", lambda: int(allv.numberSet))

print("--- bulk accessors ---")
kind("meshop.getVerts(mesh, allv)", lambda: rt.meshop.getVerts(mesh, allv))
kind("meshop.getVertsUsingFace", lambda: rt.meshop.getVertsUsingFace(mesh, rt.execute("#{1..1}")))
allf = rt.execute("#{1..%d}" % nf)
kind("meshop.getFacesUsingVert", lambda: rt.meshop.getFacesUsingVert(mesh, allv))
kind("meshop.getMapVerts(mesh,1,all)",
     lambda: rt.meshop.getMapVerts(mesh, 1, rt.execute("#{1..%d}" % int(rt.getNumTVerts(mesh)))))
kind("meshop.getMapFaces(mesh,1,allf)", lambda: rt.meshop.getMapFaces(mesh, 1, allf))

print("--- per-face accessors ---")
show("getFace(mesh, 1)", lambda: [int(c) for c in rt.getFace(mesh, 1)])
show("getTVFace(mesh, 1)", lambda: [int(c) for c in rt.getTVFace(mesh, 1)])
show("getFaceSmoothGroup(mesh, 1)", lambda: int(rt.getFaceSmoothGroup(mesh, 1)))
show("getFaceMatID(mesh, 1)", lambda: int(rt.getFaceMatID(mesh, 1)))
show("getFaceNormal(mesh, 1)", lambda: [float(c) for c in rt.getFaceNormal(mesh, 1)])
show("getTVert(mesh, 1)", lambda: [float(c) for c in rt.getTVert(mesh, 1)])
show("meshop.getFaceArea(mesh, 1)", lambda: float(rt.meshop.getFaceArea(mesh, 1)))

print("--- smoothing groups across the mesh ---")


def _sg_histogram():
    seen = {}
    for i in range(1, min(nf, 400) + 1):
        sg = int(rt.getFaceSmoothGroup(mesh, i))
        seen[sg] = seen.get(sg, 0) + 1
    return sorted(seen.items())[:8]


show("smoothing groups (first 400 faces)", _sg_histogram)

print("--- benchmark ---")
rt.delete(teapot)
big = rt.Sphere(radius=10.0, segments=200, pos=rt.Point3(0, 0, 0))
bmesh = rt.snapshotAsMesh(big)
bnv = int(rt.getNumVerts(bmesh))
bnf = int(rt.getNumFaces(bmesh))
print("  benchmark mesh: %d verts, %d faces" % (bnv, bnf))


def _timed(label, fn):
    t0 = time.perf_counter()
    try:
        n = fn()
    except Exception as exc:
        print("  %-34s FAILED  %s: %s" % (label, type(exc).__name__, exc))
        return
    dt = time.perf_counter() - t0
    rate = (bnf / dt / 1e6) if dt > 0 else float("inf")
    print("  %-34s %7.3f s   (%s)   %.2f Mtri/s-equivalent" % (label, dt, n, rate))


def _bulk_verts():
    ba = rt.execute("#{1..%d}" % bnv)
    arr = rt.meshop.getVerts(bmesh, ba)
    return "array len %d" % len(arr)


def _bulk_verts_to_python():
    ba = rt.execute("#{1..%d}" % bnv)
    arr = rt.meshop.getVerts(bmesh, ba)
    out = []
    for p in arr:
        out.append((p.x, p.y, p.z))
    return "%d tuples" % len(out)


def _loop_verts():
    n = min(bnv, 20000)
    out = []
    for i in range(1, n + 1):
        p = rt.getVert(bmesh, i)
        out.append((p.x, p.y, p.z))
    return "%d of %d verts one at a time" % (n, bnv)


def _loop_faces():
    n = min(bnf, 20000)
    out = []
    for i in range(1, n + 1):
        f = rt.getFace(bmesh, i)
        out.append((int(f.x), int(f.y), int(f.z)))
    return "%d of %d faces one at a time" % (n, bnf)


_timed("bulk getVerts (no python convert)", _bulk_verts)
_timed("bulk getVerts + python tuples", _bulk_verts_to_python)
_timed("per-vert getVert loop (20k)", _loop_verts)
_timed("per-face getFace loop (20k)", _loop_faces)

print("--- probe 11: instancing keys ---")
show("getHandleByAnim(node)", lambda: int(rt.getHandleByAnim(big)))
show("getHandleByAnim(node.baseObject)", lambda: int(rt.getHandleByAnim(big.baseObject)))
show("classOf(node.baseObject)", lambda: str(rt.classOf(big.baseObject)))
inst = rt.instance(big)
show("instance baseObject handle equal",
     lambda: int(rt.getHandleByAnim(big.baseObject)) == int(rt.getHandleByAnim(inst.baseObject)))
show("copy baseObject handle equal",
     lambda: int(rt.getHandleByAnim(big.baseObject)) == int(rt.getHandleByAnim(rt.copy(big).baseObject)))
show("node.objectTransform present", lambda: [float(x) for x in rt.getRow(big.objectTransform, 3)])

print("PROBE_COMPLETE")
