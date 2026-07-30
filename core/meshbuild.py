"""Raw Max mesh arrays → per-material indexed meshes → binary PLY.

Everything here is **stdlib only**, and that is a hard requirement rather than a taste:
probe 06c established that 3ds Max 2027 ships no numpy, so any part of the export path
that reaches for it simply cannot run. See `docs/PROBE_RESULTS.md`, probe 06c.

The module takes what Max can hand over cheaply — flat vertex, UV and face arrays — and
does the three things Max will not do for you:

1. **Normals from smoothing groups.** Max stores a 32-bit smoothing-group mask per face,
   not vertex normals. Two faces share a smooth vertex normal iff their masks share a bit.
2. **Vertex splitting.** Max keeps separate index arrays for positions and UVs; PLY and
   Mitsuba need one unified index buffer.
3. **Splitting by material ID.** Mitsuba has no per-face material, so one Max node with
   three face material IDs becomes three shapes.

All three have to happen together, because the split key is the triple
`(position index, uv index, smoothing class)` and none of the three components can be
resolved without the other two.
"""

import struct
from dataclasses import dataclass, field
from math import acos, sqrt

__all__ = ["FACE_STRIDE", "MeshGroup", "RawMesh", "build_groups", "write_ply"]

FACE_STRIDE = 8
"""Ints per face in `RawMesh.faces`: v0 v1 v2, t0 t1 t2, smoothing group, material id."""

_VERTEX = struct.Struct("<8f")
_FACE = struct.Struct("<B3i")


@dataclass(slots=True)
class RawMesh:
    """Exactly what `max_side.mesh` reads out of a `TriMesh`, in Max's own conventions.

    `positions` and `tverts` are flat and 0-based here; the 1-based indices Max uses are
    converted at the boundary, once, by the reader. `faces` is flat with `FACE_STRIDE` ints
    per face — flat rather than a list of tuples because at 2M triangles the tuple objects
    alone cost more than the extraction did.
    """

    positions: list[float]
    """Flat x,y,z triples in Max space (Z-up), in system units."""
    faces: list[int]
    """Flat, `FACE_STRIDE` ints per face; UV indices are -1 when the mesh has no UVs."""
    tverts: list[float] = field(default_factory=list)
    """Flat u,v pairs. Empty when the mesh is unmapped."""
    name: str = ""

    @property
    def face_count(self) -> int:
        return len(self.faces) // FACE_STRIDE

    @property
    def vertex_count(self) -> int:
        return len(self.positions) // 3


@dataclass(slots=True)
class MeshGroup:
    """One material's worth of a node, ready to be written as PLY."""

    material_id: int
    positions: list[float]
    """Flat x,y,z in Mitsuba space (Y-up), in metres."""
    normals: list[float]
    uvs: list[float]
    indices: list[int]
    """Flat triples into the arrays above."""

    @property
    def vertex_count(self) -> int:
        return len(self.positions) // 3

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 3


# --------------------------------------------------------------------------------------
# smoothing groups
# --------------------------------------------------------------------------------------


def _smoothing_classes(masks: list[int]) -> list[int]:
    """Partition one vertex's incident faces into smooth groups.

    Two faces belong together iff their smoothing masks share at least one bit. A face with
    mask 0 is always alone — that is Max's way of spelling "hard edge", and treating it as
    "matches everything" (which `0 & x` invitingly suggests) welds every hard edge in the
    scene into mush.

    Returns a class index per input face. The merging is transitive: masks 0b001, 0b011 and
    0b010 form a single class even though the first and last share no bit, because the
    middle one bridges them. Handling that with a simple "first match wins" loop is the
    classic bug — it produces a seam that appears and disappears depending on face order.
    """
    n = len(masks)
    classes: list[int] = [-1] * n
    # Per open class: the union of the masks of its members.
    class_masks: list[int] = []

    for i, mask in enumerate(masks):
        if mask == 0:
            classes[i] = len(class_masks)
            class_masks.append(0)
            continue

        hits = [c for c, cm in enumerate(class_masks) if cm & mask]
        if not hits:
            classes[i] = len(class_masks)
            class_masks.append(mask)
            continue

        keep = hits[0]
        class_masks[keep] |= mask
        for other in hits[1:]:
            class_masks[keep] |= class_masks[other]
            class_masks[other] = 0
            for j in range(i):
                if classes[j] == other:
                    classes[j] = keep
        classes[i] = keep

    # Compact so class indices are dense, which keeps the split-key dict small.
    remap: dict[int, int] = {}
    for i, c in enumerate(classes):
        if c not in remap:
            remap[c] = len(remap)
        classes[i] = remap[c]
    return classes


# --------------------------------------------------------------------------------------
# the build
# --------------------------------------------------------------------------------------


def build_groups(raw: RawMesh, *, scale_to_meters: float = 1.0,
                 reverse_winding: bool = False) -> list[MeshGroup]:
    """Convert a raw Max mesh into one `MeshGroup` per material id.

    Vertices are converted to Mitsuba space here — `(x, y, z)_max → (x, z, -y)` — and
    scaled to metres. Doing it once at this boundary is why nothing downstream has to know
    Max is Z-up.

    `reverse_winding` handles nodes whose world transform has negative determinant.
    `snapshotAsMesh` bakes the transform into the vertices and leaves the face order alone,
    so a mirrored node arrives inside-out with no other trace of what happened. It reverses
    every index triple **and negates every normal** — both are needed, and the emitted PLY
    is then correct on its own, with no dependence on a renderer-side flag. See the comment
    on the normal computation for the measurement that settled this.
    """
    nf = raw.face_count
    faces = raw.faces
    pos = raw.positions
    tv = raw.tverts
    has_uv = bool(tv)

    # ---- positions in Mitsuba space -------------------------------------------------
    s = float(scale_to_meters)
    mx: list[float] = [0.0] * (raw.vertex_count * 3)
    for i in range(raw.vertex_count):
        j = i * 3
        mx[j] = pos[j] * s
        mx[j + 1] = pos[j + 2] * s
        mx[j + 2] = -pos[j + 1] * s

    # ---- per-face geometric normals and per-corner angles ----------------------------
    face_nx: list[float] = [0.0] * nf
    face_ny: list[float] = [0.0] * nf
    face_nz: list[float] = [0.0] * nf
    corner_angle: list[float] = [0.0] * (nf * 3)

    for f in range(nf):
        base = f * FACE_STRIDE
        a, b, c = faces[base] * 3, faces[base + 1] * 3, faces[base + 2] * 3
        ax, ay, az = mx[a], mx[a + 1], mx[a + 2]
        bx, by, bz = mx[b], mx[b + 1], mx[b + 2]
        cx, cy, cz = mx[c], mx[c + 1], mx[c + 2]

        ux, uy, uz = bx - ax, by - ay, bz - az
        vx, vy, vz = cx - ax, cy - ay, cz - az
        nx = uy * vz - uz * vy
        ny = uz * vx - ux * vz
        nz = ux * vy - uy * vx
        ln = sqrt(nx * nx + ny * ny + nz * nz)
        if ln > 1e-20:
            # Negated together with the winding reversal below. `snapshotAsMesh` bakes a
            # mirrored node's transform into its vertices while leaving the face order
            # alone, so the normals computed here point *inward*. Reversing the index
            # triples alone does not fix that: the normals are computed before the
            # reversal, and — measured against Mitsuba 3.9 — a `ply` shape carrying
            # explicit `nx ny nz` uses those normals and ignores the winding entirely.
            sign = -1.0 if reverse_winding else 1.0
            face_nx[f] = sign * nx / ln
            face_ny[f] = sign * ny / ln
            face_nz[f] = sign * nz / ln

        # Angle-weighted averaging. Weighting by area or not at all visibly distorts
        # normals on irregular tessellation, which is the usual tell for a bad exporter.
        cb = f * 3
        corner_angle[cb] = _angle(ax, ay, az, bx, by, bz, cx, cy, cz)
        corner_angle[cb + 1] = _angle(bx, by, bz, cx, cy, cz, ax, ay, az)
        corner_angle[cb + 2] = _angle(cx, cy, cz, ax, ay, az, bx, by, bz)

    # ---- incident faces per vertex ---------------------------------------------------
    incident: list[list[int]] = [[] for _ in range(raw.vertex_count)]
    for f in range(nf):
        base = f * FACE_STRIDE
        for k in range(3):
            incident[faces[base + k]].append(f * 3 + k)   # encode (face, corner)

    # ---- smoothing class per corner, and the averaged normal per (vertex, class) -----
    corner_class: list[int] = [0] * (nf * 3)
    class_normal: dict[tuple[int, int], tuple[float, float, float]] = {}

    for v, corners in enumerate(incident):
        if not corners:
            continue
        masks = [faces[(fc // 3) * FACE_STRIDE + 6] for fc in corners]
        cls = _smoothing_classes(masks)

        acc: dict[int, list[float]] = {}
        for local, fc in enumerate(corners):
            corner_class[fc] = cls[local]
            f = fc // 3
            w = corner_angle[fc]
            slot = acc.setdefault(cls[local], [0.0, 0.0, 0.0])
            slot[0] += face_nx[f] * w
            slot[1] += face_ny[f] * w
            slot[2] += face_nz[f] * w

        for c, (nx, ny, nz) in acc.items():
            ln = sqrt(nx * nx + ny * ny + nz * nz)
            if ln > 1e-20:
                class_normal[(v, c)] = (nx / ln, ny / ln, nz / ln)
            else:
                # Every incident face cancelled out — degenerate fan. Fall back to the
                # first face's normal rather than emitting a zero normal, which renders as
                # a black speck that is very hard to trace back to here.
                f0 = corners[0] // 3
                class_normal[(v, c)] = (face_nx[f0], face_ny[f0], face_nz[f0])

    # ---- split and group by material id ----------------------------------------------
    groups: dict[int, MeshGroup] = {}
    keymaps: dict[int, dict[tuple[int, int, int], int]] = {}

    for f in range(nf):
        base = f * FACE_STRIDE
        matid = faces[base + 7]
        group = groups.get(matid)
        if group is None:
            group = MeshGroup(material_id=matid, positions=[], normals=[], uvs=[],
                              indices=[])
            groups[matid] = group
            keymaps[matid] = {}
        keymap = keymaps[matid]

        tri: list[int] = []
        for k in range(3):
            v = faces[base + k]
            t = faces[base + 3 + k] if has_uv else -1
            smooth_class = corner_class[f * 3 + k]
            key = (v, t, smooth_class)
            idx = keymap.get(key)
            if idx is None:
                idx = len(group.positions) // 3
                keymap[key] = idx
                j = v * 3
                group.positions.append(mx[j])
                group.positions.append(mx[j + 1])
                group.positions.append(mx[j + 2])
                nrm = class_normal[(v, smooth_class)]
                group.normals.append(nrm[0])
                group.normals.append(nrm[1])
                group.normals.append(nrm[2])
                if t >= 0:
                    group.uvs.append(tv[t * 2])
                    # V is flipped. Max puts V = 0 at the bottom of the image, as every
                    # OpenGL-descended tool does; Mitsuba's `bitmap` texture samples
                    # t = 0 from the image's FIRST (top) row. Measured, not assumed: the
                    # chirality golden scene renders a quad whose four UV corners are four
                    # hues and asserts which screen corner each lands in, and without this
                    # line it reports red where blue should be.
                    group.uvs.append(1.0 - tv[t * 2 + 1])
                else:
                    group.uvs.append(0.0)
                    group.uvs.append(0.0)
            tri.append(idx)

        if reverse_winding:
            tri.reverse()
        group.indices.extend(tri)

    return [groups[k] for k in sorted(groups)]


def _angle(ax: float, ay: float, az: float,
           bx: float, by: float, bz: float,
           cx: float, cy: float, cz: float) -> float:
    """Interior angle at `a` in the triangle `a, b, c`."""
    ux, uy, uz = bx - ax, by - ay, bz - az
    vx, vy, vz = cx - ax, cy - ay, cz - az
    lu = sqrt(ux * ux + uy * uy + uz * uz)
    lv = sqrt(vx * vx + vy * vy + vz * vz)
    if lu < 1e-20 or lv < 1e-20:
        return 0.0
    cosine = (ux * vx + uy * vy + uz * vz) / (lu * lv)
    if cosine <= -1.0:
        return 3.141592653589793
    if cosine >= 1.0:
        return 0.0
    return acos(cosine)


# --------------------------------------------------------------------------------------
# PLY
# --------------------------------------------------------------------------------------


def write_ply(group: MeshGroup) -> bytes:
    """Binary little-endian PLY with `x y z nx ny nz s t` and a `uchar int` face list.

    Returned as bytes rather than written to a path, because the caller names the file by
    the hash of these very bytes (`core.assets`). Naming a file before you know its content
    hash is not possible, and hashing a file you just wrote is a wasted round trip.
    """
    nv = group.vertex_count
    nf = group.triangle_count

    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        "comment written by mitsuba-max\n"
        f"element vertex {nv}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        "property float nx\n"
        "property float ny\n"
        "property float nz\n"
        "property float s\n"
        "property float t\n"
        f"element face {nf}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    ).encode("ascii")

    out = bytearray(header)
    pos, nrm, uv, idx = group.positions, group.normals, group.uvs, group.indices
    pack_vertex = _VERTEX.pack
    for i in range(nv):
        p, n, t = i * 3, i * 3, i * 2
        out += pack_vertex(pos[p], pos[p + 1], pos[p + 2],
                           nrm[n], nrm[n + 1], nrm[n + 2],
                           uv[t], uv[t + 1])
    pack_face = _FACE.pack
    for i in range(nf):
        j = i * 3
        out += pack_face(3, idx[j], idx[j + 1], idx[j + 2])
    return bytes(out)
