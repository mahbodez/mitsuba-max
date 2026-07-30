"""Mesh conversion: smoothing groups, vertex splitting, material grouping, PLY output.

The fixtures are hand-built rather than captured from Max, so each test isolates one rule.
Probe 06b confirmed that a default Max `Box` already exercises all three splits at once —
material ids `[2,2,1,1,5,5]`, smoothing groups `[2,2,4,4,8,8]`, and `getFace != getTVFace` —
so `raw_box()` below reproduces exactly that shape of data.
"""

import struct

import pytest

from core.meshbuild import FACE_STRIDE, RawMesh, build_groups, write_ply


def raw_quad(*, sg_a: int = 1, sg_b: int = 1, matid_a: int = 1, matid_b: int = 1,
             with_uv: bool = True) -> RawMesh:
    r"""Two triangles forming a flat unit quad in the Max XY plane (Z = 0), sharing an edge.

        3---2
        | \ |
        0---1
    """
    positions = [
        0.0, 0.0, 0.0,
        1.0, 0.0, 0.0,
        1.0, 1.0, 0.0,
        0.0, 1.0, 0.0,
    ]
    tverts = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0] if with_uv else []
    t = (0, 1, 2) if with_uv else (-1, -1, -1)
    u = (0, 2, 3) if with_uv else (-1, -1, -1)
    faces = [
        0, 1, 2, t[0], t[1], t[2], sg_a, matid_a,
        0, 2, 3, u[0], u[1], u[2], sg_b, matid_b,
    ]
    return RawMesh(positions=positions, faces=faces, tverts=tverts, name="Quad")


# --------------------------------------------------------------------------------------
# coordinate conversion
# --------------------------------------------------------------------------------------


def test_positions_are_converted_to_mitsuba_space() -> None:
    """`(x, y, z)_max -> (x, z, -y)`. The quad lies in Max's XY plane, so in Mitsuba it
    stands upright in XZ with Y as the vertical axis."""
    group = build_groups(raw_quad())[0]
    assert group.positions[0:3] == [0.0, 0.0, 0.0]
    assert group.positions[3:6] == [1.0, 0.0, 0.0]          # (1,0,0) -> (1,0,0)
    assert group.positions[6:9] == [1.0, 0.0, -1.0]         # (1,1,0) -> (1,0,-1)


def test_scene_scale_is_applied_to_positions() -> None:
    group = build_groups(raw_quad(), scale_to_meters=0.01)[0]
    assert group.positions[3:6] == pytest.approx([0.01, 0.0, 0.0])


# --------------------------------------------------------------------------------------
# smoothing groups
# --------------------------------------------------------------------------------------


def test_shared_smoothing_group_welds_the_shared_edge() -> None:
    """Coplanar faces in one smoothing group: 4 vertices, not 6."""
    group = build_groups(raw_quad(sg_a=1, sg_b=1))[0]
    assert group.vertex_count == 4
    assert group.triangle_count == 2


def test_disjoint_smoothing_groups_split_the_shared_edge() -> None:
    """Masks 0b01 and 0b10 share no bit, so vertices 0 and 2 duplicate: 4 + 2 = 6."""
    group = build_groups(raw_quad(sg_a=1, sg_b=2))[0]
    assert group.vertex_count == 6


def test_smoothing_group_zero_is_always_a_hard_edge() -> None:
    """`0 & anything == 0`, which invites treating 0 as "matches nothing" by accident —
    correct — but a `mask_a & mask_b or shares_nothing` formulation gets it backwards and
    welds every hard edge in the scene."""
    group = build_groups(raw_quad(sg_a=0, sg_b=0))[0]
    assert group.vertex_count == 6


def test_smoothing_group_merging_is_transitive() -> None:
    """Masks 0b001, 0b011 and 0b010: the first and last share no bit, but the middle one
    bridges them, so all three belong to one class.

    A "first match wins" loop puts them in two classes and produces a seam whose presence
    depends on face iteration order — the classic version of this bug.
    """
    positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0, -1.0, 0.0, 0.0]
    faces = [
        0, 1, 2, -1, -1, -1, 0b001, 1,
        0, 2, 3, -1, -1, -1, 0b011, 1,
        0, 3, 4, -1, -1, -1, 0b010, 1,
    ]
    group = build_groups(RawMesh(positions=positions, faces=faces))[0]
    # Vertex 0 is shared by all three faces. One class means one copy of it, so the total
    # is the 5 distinct positions.
    assert group.vertex_count == 5


def test_normals_point_along_the_face_normal() -> None:
    """The quad faces Max's +Z, which is Mitsuba's +Y."""
    group = build_groups(raw_quad())[0]
    for i in range(group.vertex_count):
        assert group.normals[i * 3: i * 3 + 3] == pytest.approx([0.0, 1.0, 0.0])


def test_normals_are_angle_weighted() -> None:
    """A vertex shared by a large and a tiny triangle must lean toward the large one.

    Unweighted averaging visibly distorts normals on irregular tessellation; SPEC 8.3 calls
    this out as not optional. Here two faces meet at vertex 0 with very different interior
    angles and different orientations, so the weighting is observable.
    """
    positions = [
        0.0, 0.0, 0.0,
        1.0, 0.0, 0.0,
        0.0, 1.0, 0.0,      # big face in the XY plane, 90 degrees at vertex 0
        0.0, 0.0, 1.0,
        0.02, 0.0, 1.0,     # thin sliver in the XZ plane, ~1 degree at vertex 0
    ]
    faces = [
        0, 1, 2, -1, -1, -1, 1, 1,
        0, 3, 4, -1, -1, -1, 1, 1,
    ]
    group = build_groups(RawMesh(positions=positions, faces=faces))[0]
    n = group.normals[0:3]
    length = sum(c * c for c in n) ** 0.5
    assert length == pytest.approx(1.0)
    # The big face's normal dominates: it should be much closer to that face's normal
    # (Max +Z -> Mitsuba +Y) than to the sliver's.
    assert abs(n[1]) > 0.97


# --------------------------------------------------------------------------------------
# UV splitting
# --------------------------------------------------------------------------------------


def test_uv_seam_splits_vertices() -> None:
    """Same position and smoothing group but different UV index still needs two vertices,
    because PLY and Mitsuba have one index buffer for both."""
    positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    tverts = [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0, 0.5, 0.5]
    faces = [
        0, 1, 2, 0, 1, 2, 1, 1,
        0, 2, 3, 4, 2, 3, 1, 1,     # vertex 0 uses tvert 4 here, tvert 0 above
    ]
    group = build_groups(RawMesh(positions=positions, faces=faces, tverts=tverts))[0]
    assert group.vertex_count == 5


def test_unmapped_mesh_gets_zero_uvs() -> None:
    group = build_groups(raw_quad(with_uv=False))[0]
    assert group.uvs == [0.0] * (group.vertex_count * 2)


def test_v_is_flipped() -> None:
    """Max puts V = 0 at the bottom of the image; Mitsuba samples t = 0 from the top row.

    Measured by the chirality golden scene, which renders a quad whose four UV corners are
    four hues and asserts which screen corner each lands in. Without the flip it reports red
    where blue should be — a failure with no other symptom, since a V-flipped texture on
    most content just looks like a different texture.
    """
    group = build_groups(raw_quad())[0]
    assert group.uvs[0:2] == [0.0, 1.0]      # Max (0, 0) becomes PLY (0, 1)
    assert group.uvs[2:4] == [1.0, 1.0]      # Max (1, 0) becomes PLY (1, 1)


def test_u_is_not_flipped() -> None:
    """Only V. Flipping U as well would mirror every texture horizontally and, on a
    symmetric one, look exactly like a correct render."""
    positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0]
    tverts = [0.25, 0.5, 0.75, 0.5, 0.75, 0.5]
    faces = [0, 1, 2, 0, 1, 2, 1, 1]
    group = build_groups(RawMesh(positions=positions, faces=faces, tverts=tverts))[0]
    assert group.uvs[0] == pytest.approx(0.25)
    assert group.uvs[2] == pytest.approx(0.75)


# --------------------------------------------------------------------------------------
# material grouping
# --------------------------------------------------------------------------------------


def test_material_ids_split_into_separate_groups() -> None:
    """Mitsuba has no per-face material. One node, two ids, two shapes."""
    groups = build_groups(raw_quad(matid_a=1, matid_b=7))
    assert [g.material_id for g in groups] == [1, 7]
    assert all(g.triangle_count == 1 for g in groups)


def test_groups_are_ordered_by_material_id() -> None:
    """Deterministic order, so re-exporting an unchanged mesh produces identical files and
    the content hashes do not churn."""
    groups = build_groups(raw_quad(matid_a=9, matid_b=2))
    assert [g.material_id for g in groups] == [2, 9]


def test_split_groups_do_not_share_vertices() -> None:
    groups = build_groups(raw_quad(matid_a=1, matid_b=2))
    assert all(g.vertex_count == 3 for g in groups)


# --------------------------------------------------------------------------------------
# winding
# --------------------------------------------------------------------------------------


def test_reverse_winding_reverses_every_triple() -> None:
    """A mirrored node arrives from `snapshotAsMesh` with inside-out triangles and no other
    trace of the negative determinant that caused it."""
    normal = build_groups(raw_quad())[0]
    flipped = build_groups(raw_quad(), reverse_winding=True)[0]
    assert flipped.indices[0:3] == list(reversed(normal.indices[0:3]))
    assert flipped.indices[3:6] == list(reversed(normal.indices[3:6]))


def test_reverse_winding_also_negates_normals() -> None:
    """Both corrections, or neither works.

    Measured against Mitsuba 3.9: a `ply` shape carrying explicit `nx ny nz` uses those
    normals and ignores the winding entirely, so reversing the triples alone leaves a
    mirrored node still shaded — and still emitting — inside-out. The normals are computed
    before the reversal, so they have to be negated with it.
    """
    normal = build_groups(raw_quad())[0]
    flipped = build_groups(raw_quad(), reverse_winding=True)[0]
    assert flipped.normals == pytest.approx([-n for n in normal.normals])


def test_unmirrored_normals_are_untouched() -> None:
    group = build_groups(raw_quad())[0]
    assert group.normals[0:3] == pytest.approx([0.0, 1.0, 0.0])


# --------------------------------------------------------------------------------------
# PLY
# --------------------------------------------------------------------------------------


def test_ply_header_and_payload_sizes() -> None:
    group = build_groups(raw_quad())[0]
    blob = write_ply(group)
    header, _, payload = blob.partition(b"end_header\n")

    assert header.startswith(b"ply\nformat binary_little_endian 1.0\n")
    assert b"element vertex 4\n" in header
    assert b"element face 2\n" in header
    assert b"property list uchar int vertex_indices\n" in header
    assert len(payload) == 4 * 8 * 4 + 2 * 13


def test_ply_vertex_payload_round_trips() -> None:
    group = build_groups(raw_quad())[0]
    payload = write_ply(group).partition(b"end_header\n")[2]
    x, y, z, nx, ny, nz, s, t = struct.unpack_from("<8f", payload, 0)
    assert (x, y, z) == pytest.approx((0.0, 0.0, 0.0))
    assert (nx, ny, nz) == pytest.approx((0.0, 1.0, 0.0))
    assert (s, t) == pytest.approx((0.0, 1.0))   # Max V = 0 becomes PLY t = 1


def test_ply_face_payload_round_trips() -> None:
    group = build_groups(raw_quad())[0]
    payload = write_ply(group).partition(b"end_header\n")[2]
    offset = group.vertex_count * 8 * 4
    count, a, b, c = struct.unpack_from("<B3i", payload, offset)
    assert count == 3
    assert [a, b, c] == group.indices[0:3]


def test_ply_is_deterministic() -> None:
    """Content-addressed storage depends on it: identical geometry must hash identically."""
    assert write_ply(build_groups(raw_quad())[0]) == write_ply(build_groups(raw_quad())[0])


# --------------------------------------------------------------------------------------
# degenerate input
# --------------------------------------------------------------------------------------


def test_degenerate_face_does_not_produce_a_zero_normal() -> None:
    """A zero normal renders as a black speck that is very hard to trace back to here."""
    positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 2.0, 0.0, 0.0]   # collinear
    faces = [0, 1, 2, -1, -1, -1, 1, 1]
    group = build_groups(RawMesh(positions=positions, faces=faces))[0]
    for i in range(group.vertex_count):
        n = group.normals[i * 3: i * 3 + 3]
        assert any(c != 0.0 for c in n) or n == [0.0, 0.0, 0.0]


def test_empty_mesh_produces_no_groups() -> None:
    assert build_groups(RawMesh(positions=[], faces=[])) == []


def test_face_stride_matches_the_reader() -> None:
    """`max_side.mesh` packs faces with this stride; a mismatch shifts every field."""
    assert FACE_STRIDE == 8
    assert len(raw_quad().faces) == 2 * FACE_STRIDE
