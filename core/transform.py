"""Coordinate conversion between 3ds Max and Mitsuba.

Max is right-handed and Z-up. Mitsuba imposes no global up axis, but its `envmap`
parameterisation, its `look_at` and every convention in its documentation assume Y-up.
The conversion happens exactly once, at the Max → IR boundary; nothing downstream ever
sees a Z-up quantity.

Pure functions, no I/O, **stdlib only**. The stdlib restriction is not stylistic: probe 06c
established that 3ds Max 2027 ships no numpy (its site-packages holds PySide6, pymxs,
qtmax and shiboken6, and nothing else), so anything on the export path that imports numpy
cannot run inside Max at all. Sixteen floats do not need a linear algebra library.

Every matrix here is a length-16 row-major tuple matching `core.ir.Mat4`, so `m[r * 4 + c]`
is row `r`, column `c` and column vectors transform as `p' = M @ p`.
"""

import math
from typing import TypeAlias

from core.ir import Mat4, Vec2

__all__ = [
    "BASIS_MAX_TO_MITSUBA",
    "IDENTITY",
    "compose_trs",
    "conjugate",
    "determinant3",
    "identity",
    "inverse",
    "is_mirrored",
    "look_at_from_matrix",
    "look_at_matrix",
    "multiply",
    "point_max_to_mitsuba",
    "principal_point_offset_from_shift_mm",
    "rows",
    "transform_point",
    "vector_max_to_mitsuba",
]

Vec3: TypeAlias = tuple[float, float, float]

IDENTITY: Mat4 = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

BASIS_MAX_TO_MITSUBA: Mat4 = (
    1.0,  0.0, 0.0, 0.0,
    0.0,  0.0, 1.0, 0.0,
    0.0, -1.0, 0.0, 0.0,
    0.0,  0.0, 0.0, 1.0,
)
"""`C`: maps `(x, y, z)_max` to `(x, z, -y)_mitsuba`.

`det(C) = +1`, so handedness, triangle winding and normal orientation are all preserved by
the change of basis alone. Any winding flip observed downstream comes from the node
transform, never from `C`.
"""


# --------------------------------------------------------------------------------------
# small vector helpers
# --------------------------------------------------------------------------------------


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _norm(a: Vec3) -> float:
    return math.sqrt(_dot(a, a))


def _normalize(a: Vec3, what: str) -> Vec3:
    n = _norm(a)
    if n < 1e-12:
        raise ValueError(f"cannot normalise a degenerate {what}")
    return (a[0] / n, a[1] / n, a[2] / n)


# --------------------------------------------------------------------------------------
# matrix algebra
# --------------------------------------------------------------------------------------


def identity() -> Mat4:
    return IDENTITY


def rows(m: Mat4) -> tuple[tuple[float, ...], ...]:
    """The matrix as four row tuples. For readable assertions and error messages."""
    return tuple(tuple(m[r * 4: r * 4 + 4]) for r in range(4))


def multiply(a: Mat4, b: Mat4) -> Mat4:
    out = [0.0] * 16
    for r in range(4):
        ar = r * 4
        for c in range(4):
            out[ar + c] = (
                a[ar] * b[c]
                + a[ar + 1] * b[4 + c]
                + a[ar + 2] * b[8 + c]
                + a[ar + 3] * b[12 + c]
            )
    return tuple(out)


def inverse(m: Mat4) -> Mat4:
    """General 4x4 inverse by Gauss-Jordan elimination with partial pivoting.

    General rather than the affine shortcut because `conjugate` must stay correct if a
    projective row ever appears, and because a wrong inverse here is invisible until a node
    is both rotated and off-origin.
    """
    aug = [[m[r * 4 + c] for c in range(4)] + [1.0 if r == c else 0.0 for c in range(4)]
           for r in range(4)]

    for col in range(4):
        pivot = max(range(col, 4), key=lambda r: abs(aug[r][col]))
        if abs(aug[pivot][col]) < 1e-14:
            raise ValueError("matrix is singular and cannot be inverted")
        aug[col], aug[pivot] = aug[pivot], aug[col]

        scale = aug[col][col]
        aug[col] = [v / scale for v in aug[col]]

        for r in range(4):
            if r == col:
                continue
            factor = aug[r][col]
            if factor != 0.0:
                aug[r] = [v - factor * p for v, p in zip(aug[r], aug[col], strict=True)]

    return tuple(aug[r][4 + c] for r in range(4) for c in range(4))


def determinant3(m: Mat4) -> float:
    """Determinant of the upper-left 3x3 block, i.e. of the linear part."""
    a, b, c = m[0], m[1], m[2]
    d, e, f = m[4], m[5], m[6]
    g, h, i = m[8], m[9], m[10]
    return a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)


def transform_point(m: Mat4, p: Vec3) -> Vec3:
    """Apply a 4x4 to a point (w = 1), dividing through by w if it is not 1."""
    x = m[0] * p[0] + m[1] * p[1] + m[2] * p[2] + m[3]
    y = m[4] * p[0] + m[5] * p[1] + m[6] * p[2] + m[7]
    z = m[8] * p[0] + m[9] * p[1] + m[10] * p[2] + m[11]
    w = m[12] * p[0] + m[13] * p[1] + m[14] * p[2] + m[15]
    if w not in (0.0, 1.0):
        return (x / w, y / w, z / w)
    return (x, y, z)


def transform_vector(m: Mat4, v: Vec3) -> Vec3:
    """Apply only the linear part — for directions, which ignore translation."""
    return (
        m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
        m[4] * v[0] + m[5] * v[1] + m[6] * v[2],
        m[8] * v[0] + m[9] * v[1] + m[10] * v[2],
    )


# --------------------------------------------------------------------------------------
# basis change
# --------------------------------------------------------------------------------------


def conjugate(t_max: Mat4) -> Mat4:
    """Express a Max node transform in Mitsuba space: `C @ T @ inv(C)`.

    Conjugation, not left-multiplication. `C @ T` looks correct for anything at the origin
    or unrotated, and starts producing wrong results the moment a node is both off-origin
    and rotated — which is why `tests/test_transform.py` asserts the equivalence
    `C @ (T @ p) == (C @ T @ inv(C)) @ (C @ p)` over random inputs rather than spot values.

    Only meaningful when vertices have themselves been mapped through `C`. In v1 geometry
    is baked in world space and meshes carry `C` alone; this function is what the M5
    object-space instancing path uses.
    """
    return multiply(multiply(BASIS_MAX_TO_MITSUBA, t_max), inverse(BASIS_MAX_TO_MITSUBA))


def point_max_to_mitsuba(p: Vec3, scale_to_meters: float = 1.0) -> Vec3:
    """`(x, y, z)_max` → `(x, z, -y)_mitsuba`, optionally converting system units to metres."""
    x, y, z = (float(v) * scale_to_meters for v in p)
    return (x, z, -y)


def vector_max_to_mitsuba(v: Vec3) -> Vec3:
    """As `point_max_to_mitsuba` but never scaled — for directions and normals."""
    x, y, z = (float(c) for c in v)
    return (x, z, -y)


def is_mirrored(m: Mat4) -> bool:
    """True when the transform has negative determinant and so reverses triangle winding.

    A mirrored node needs both `Mesh.flip_normals` and a reversed index triple. They fix
    different failure modes — the flag corrects shading normals, the reversal corrects
    geometric normals and backface culling — so do both, always.
    """
    return determinant3(m) < 0.0


# --------------------------------------------------------------------------------------
# construction
# --------------------------------------------------------------------------------------


def compose_trs(translate: Vec3, rotate_zyx_deg: Vec3, scale: Vec3) -> Mat4:
    """Build a row-major matrix from translation, intrinsic Z-Y-X Euler degrees, and scale.

    Test scaffolding, mainly: it is how the transform-torture fixtures are written without
    hand-typing sixteen numbers per node.
    """
    rz, ry, rx = (math.radians(a) for a in rotate_zyx_deg)
    cz, sz = math.cos(rz), math.sin(rz)
    cy, sy = math.cos(ry), math.sin(ry)
    cx, sx = math.cos(rx), math.sin(rx)

    # R = Rz @ Ry @ Rx, written out rather than multiplied three times.
    r00 = cz * cy
    r01 = cz * sy * sx - sz * cx
    r02 = cz * sy * cx + sz * sx
    r10 = sz * cy
    r11 = sz * sy * sx + cz * cx
    r12 = sz * sy * cx - cz * sx
    r20 = -sy
    r21 = cy * sx
    r22 = cy * cx

    sxx, syy, szz = (float(s) for s in scale)
    return (
        r00 * sxx, r01 * syy, r02 * szz, float(translate[0]),
        r10 * sxx, r11 * syy, r12 * szz, float(translate[1]),
        r20 * sxx, r21 * syy, r22 * szz, float(translate[2]),
        0.0, 0.0, 0.0, 1.0,
    )


def from_axes(x_axis: Vec3, y_axis: Vec3, z_axis: Vec3, origin: Vec3) -> Mat4:
    """Assemble a matrix from basis vectors placed in **columns** and an origin.

    Max's `node.transform.row1..row4` are the local axes and origin expressed in world
    space — that is, Max stores them as rows of a row-vector-convention matrix, which are
    the columns of the column-vector-convention matrix used here. Passing Max's rows
    straight in as rows produces a transposed matrix that still looks plausible for
    axis-aligned nodes.
    """
    return (
        x_axis[0], y_axis[0], z_axis[0], origin[0],
        x_axis[1], y_axis[1], z_axis[1], origin[1],
        x_axis[2], y_axis[2], z_axis[2], origin[2],
        0.0, 0.0, 0.0, 1.0,
    )


def scale_matrix(sx: float, sy: float, sz: float) -> Mat4:
    return (
        float(sx), 0.0, 0.0, 0.0,
        0.0, float(sy), 0.0, 0.0,
        0.0, 0.0, float(sz), 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


# --------------------------------------------------------------------------------------
# camera basis
# --------------------------------------------------------------------------------------


def look_at_matrix(origin: Vec3, target: Vec3, up: Vec3) -> Mat4:
    """Reproduce Mitsuba's `ScalarTransform4f.look_at` exactly.

    Mitsuba builds `dir = normalize(target - origin)`, `left = normalize(cross(up, dir))`,
    `new_up = cross(dir, left)` and places them as matrix **columns** `[left, new_up, dir]`.
    The first column therefore points to the camera's *left*, not its right. Copying Max's
    camera basis straight into a sensor matrix produces a horizontally mirrored image that
    is easy to miss on a symmetric scene — which is exactly what the chirality golden scene
    exists to catch.

    Having this in `core` means the emitters can round-trip a matrix through
    `look_at_from_matrix` and assert they get the same thing back, with no renderer present.
    """
    d = _sub(target, origin)
    if _norm(d) < 1e-12:
        raise ValueError("look_at: origin and target coincide")
    d = _normalize(d, "view direction")

    left_raw = _cross(up, d)
    if _norm(left_raw) < 1e-9:
        raise ValueError("look_at: up vector is parallel to the view direction")
    left = _normalize(left_raw, "left axis")

    new_up = _cross(d, left)
    return from_axes(left, new_up, d, origin)


def look_at_from_matrix(m: Mat4, distance: float = 1.0) -> tuple[Vec3, Vec3, Vec3]:
    """Decompose a camera-to-world matrix into `(origin, target, up)` for `look_at`.

    The inverse of `look_at_matrix` up to the arbitrary choice of `distance`. Emitters use
    this rather than writing the matrix directly, because a `look_at` triple is immune to
    the column-order trap documented above and reads correctly in an exported XML.
    """
    origin: Vec3 = (m[3], m[7], m[11])
    forward = _normalize((m[2], m[6], m[10]), "camera forward axis")
    up = _normalize((m[1], m[5], m[9]), "camera up axis")
    target: Vec3 = (
        origin[0] + forward[0] * distance,
        origin[1] + forward[1] * distance,
        origin[2] + forward[2] * distance,
    )
    return origin, target, up


def principal_point_offset_from_shift_mm(shift_mm: Vec2, film_width_mm: float,
                                         aspect: float) -> Vec2:
    """Max lens shift in millimetres → Mitsuba `principal_point_offset_x/y`.

    Mitsuba's offsets are in normalised film coordinates where the full film width spans
    1.0, so a horizontal shift of `s` mm on a film `w` mm wide is `s / w`. The vertical film
    extent is `w / aspect`, hence the aspect factor on the second component.

    The ratio is exact once the unit is known; PROBE 05 — whether Max's `horizontal_shift`
    is millimetres or already a film fraction, and its sign — needs a render and is manual
    check M3-2.
    """
    w = float(film_width_mm)
    if w <= 0.0:
        raise ValueError("film_width_mm must be positive")
    if aspect <= 0.0:
        raise ValueError("aspect must be positive")
    return (float(shift_mm[0]) / w, float(shift_mm[1]) / (w / aspect))
