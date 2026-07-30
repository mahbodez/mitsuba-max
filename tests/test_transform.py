"""Coordinate conversion. The conjugation test is the important one.

`C @ T` and `C @ T @ inv(C)` agree for anything at the origin, anything unrotated, and
anything uniformly scaled — which is to say, for every scene anyone builds while writing
the exporter. They diverge the moment a node is both off-origin and rotated. So this is
tested over random transforms rather than spot values.

The tests may use numpy freely; `core.transform` may not, because probe 06c showed that
Max ships no numpy and the export path has to run inside Max. Having the test check the
stdlib implementation against numpy is a feature, not an accident.
"""

import math
import random

import numpy as np
import pytest

from core import transform as tf
from core.ir import Mat4

RNG = random.Random(20260730)


def random_transform(rng: random.Random, *, allow_mirror: bool = True) -> Mat4:
    scale = [rng.uniform(0.2, 5.0) for _ in range(3)]
    if allow_mirror and rng.random() < 0.3:
        scale[rng.randrange(3)] *= -1.0
    return tf.compose_trs(
        (rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-500, 500)),
        (rng.uniform(-180, 180), rng.uniform(-180, 180), rng.uniform(-180, 180)),
        (scale[0], scale[1], scale[2]),
    )


def as_array(m: Mat4) -> np.ndarray:
    return np.asarray(m, dtype=np.float64).reshape(4, 4)


# --------------------------------------------------------------------------------------
# the stdlib matrix algebra, checked against numpy
# --------------------------------------------------------------------------------------


def test_multiply_matches_numpy() -> None:
    for _ in range(64):
        a, b = random_transform(RNG), random_transform(RNG)
        assert as_array(tf.multiply(a, b)) == pytest.approx(as_array(a) @ as_array(b))


def test_inverse_matches_numpy() -> None:
    for _ in range(64):
        m = random_transform(RNG)
        assert as_array(tf.inverse(m)) == pytest.approx(
            np.linalg.inv(as_array(m)), abs=1e-9
        )


def test_inverse_round_trip() -> None:
    for _ in range(64):
        m = random_transform(RNG)
        assert tf.multiply(m, tf.inverse(m)) == pytest.approx(tf.IDENTITY, abs=1e-9)


def test_singular_matrix_is_rejected() -> None:
    flat = tf.compose_trs((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 0.0))
    with pytest.raises(ValueError, match="singular"):
        tf.inverse(flat)


def test_determinant3_matches_numpy() -> None:
    for _ in range(64):
        m = random_transform(RNG)
        assert tf.determinant3(m) == pytest.approx(float(np.linalg.det(as_array(m)[:3, :3])))


# --------------------------------------------------------------------------------------
# the basis change itself
# --------------------------------------------------------------------------------------


def test_basis_determinant_is_positive_one() -> None:
    """det(C) = +1, so C alone never flips winding. Any flip comes from the node."""
    assert tf.determinant3(tf.BASIS_MAX_TO_MITSUBA) == pytest.approx(1.0)


def test_basis_maps_z_up_to_y_up() -> None:
    assert tf.point_max_to_mitsuba((0.0, 0.0, 1.0)) == pytest.approx((0.0, 1.0, 0.0))
    assert tf.point_max_to_mitsuba((0.0, 1.0, 0.0)) == pytest.approx((0.0, 0.0, -1.0))
    assert tf.point_max_to_mitsuba((1.0, 0.0, 0.0)) == pytest.approx((1.0, 0.0, 0.0))


def test_basis_helper_matches_the_matrix() -> None:
    """The helper and the matrix must agree — two spellings of one convention."""
    for _ in range(64):
        p = (RNG.uniform(-100, 100), RNG.uniform(-100, 100), RNG.uniform(-100, 100))
        assert tf.point_max_to_mitsuba(p) == pytest.approx(
            tf.transform_point(tf.BASIS_MAX_TO_MITSUBA, p)
        )


def test_scene_scale_applies_to_points_not_vectors() -> None:
    p = (100.0, 0.0, 0.0)
    assert tf.point_max_to_mitsuba(p, 0.01) == pytest.approx((1.0, 0.0, 0.0))
    assert tf.vector_max_to_mitsuba(p) == pytest.approx((100.0, 0.0, 0.0))


# --------------------------------------------------------------------------------------
# the conjugation
# --------------------------------------------------------------------------------------


def test_conjugation_equivalence() -> None:
    """`C @ (T @ p) == (C @ T @ inv(C)) @ (C @ p)` for random T and p.

    Required by core/CLAUDE.md. Transforming a point in Max space and then converting must
    equal converting the point and applying the converted transform, or object-space
    geometry and its node transform disagree.
    """
    for _ in range(400):
        t_max = random_transform(RNG)
        p = (RNG.uniform(-300, 300), RNG.uniform(-300, 300), RNG.uniform(-300, 300))

        left = tf.point_max_to_mitsuba(tf.transform_point(t_max, p))
        right = tf.transform_point(tf.conjugate(t_max), tf.point_max_to_mitsuba(p))
        assert left == pytest.approx(right, abs=1e-9)


def test_naive_left_multiplication_is_wrong() -> None:
    """The bug this whole module exists to prevent, pinned as a test.

    If someone "simplifies" `conjugate` to `C @ T`, the equivalence above still passes for
    origin-centred nodes. This asserts that the naive form genuinely differs on a node that
    is both rotated and translated, so the simplification cannot look harmless.
    """
    t_max = tf.compose_trs((10.0, 20.0, 30.0), (90.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    naive = tf.multiply(tf.BASIS_MAX_TO_MITSUBA, t_max)
    assert as_array(naive) != pytest.approx(as_array(tf.conjugate(t_max)))


def test_conjugation_preserves_determinant_sign() -> None:
    """Conjugation is a similarity transform, so it cannot introduce or remove a mirror."""
    for _ in range(200):
        t_max = random_transform(RNG)
        assert tf.is_mirrored(t_max) == tf.is_mirrored(tf.conjugate(t_max))


def test_conjugate_identity_is_identity() -> None:
    assert tf.conjugate(tf.IDENTITY) == pytest.approx(tf.IDENTITY)


# --------------------------------------------------------------------------------------
# mirroring
# --------------------------------------------------------------------------------------


def test_negative_scale_is_detected() -> None:
    mirrored = tf.compose_trs((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (-1.0, 1.0, 1.0))
    assert tf.is_mirrored(mirrored)
    assert tf.determinant3(mirrored) == pytest.approx(-1.0)


def test_two_negative_scales_are_not_mirrored() -> None:
    """Scaling two axes by -1 is a 180 degree rotation, not a mirror.

    Worth pinning: a "does any scale component go negative" check would wrongly flip
    winding here and produce inside-out geometry on a perfectly ordinary node.
    """
    rotated = tf.compose_trs((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (-1.0, -1.0, 1.0))
    assert not tf.is_mirrored(rotated)


# --------------------------------------------------------------------------------------
# camera basis
# --------------------------------------------------------------------------------------


def test_look_at_puts_left_in_the_first_column() -> None:
    """Mitsuba's first basis vector points LEFT. Getting this backwards mirrors the image.

    Camera at +Z looking at the origin with +Y up: the view direction is -Z, and the
    camera's left is therefore -X.
    """
    m = tf.look_at_matrix((0.0, 0.0, 5.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    a = as_array(m)
    assert a[:3, 0] == pytest.approx([-1.0, 0.0, 0.0])   # left
    assert a[:3, 1] == pytest.approx([0.0, 1.0, 0.0])    # up
    assert a[:3, 2] == pytest.approx([0.0, 0.0, -1.0])   # forward
    assert a[:3, 3] == pytest.approx([0.0, 0.0, 5.0])    # origin


def test_look_at_round_trip() -> None:
    """Decomposing a look_at matrix and rebuilding it must be a no-op.

    This is what lets `emit_dict` emit a `look_at` triple instead of a raw matrix without
    changing the scene.
    """
    up = (0.0, 1.0, 0.0)
    checked = 0
    for _ in range(400):
        origin = (RNG.uniform(-50, 50), RNG.uniform(-50, 50), RNG.uniform(-50, 50))
        target = (RNG.uniform(-50, 50), RNG.uniform(-50, 50), RNG.uniform(-50, 50))
        d = np.subtract(target, origin)
        if np.linalg.norm(d) < 1e-3:
            continue
        d = d / np.linalg.norm(d)
        if abs(float(np.dot(d, up))) > 0.999:
            continue

        m = tf.look_at_matrix(origin, target, up)
        o2, t2, u2 = tf.look_at_from_matrix(m)
        assert tf.look_at_matrix(o2, t2, u2) == pytest.approx(m, abs=1e-9)
        checked += 1
    assert checked > 100


def test_look_at_rejects_degenerate_input() -> None:
    with pytest.raises(ValueError, match="coincide"):
        tf.look_at_matrix((1.0, 1.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 0.0))
    with pytest.raises(ValueError, match="parallel"):
        tf.look_at_matrix((0.0, 0.0, 0.0), (0.0, 5.0, 0.0), (0.0, 1.0, 0.0))


def test_from_axes_places_axes_in_columns() -> None:
    """Max's `transform.rowN` are the local axes as *rows*; this puts them in columns.

    Feeding Max's rows in as rows gives a transposed matrix that still looks right for an
    axis-aligned node, which is how the mistake survives testing on a default scene.
    """
    m = tf.from_axes((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, -1.0, 0.0), (5.0, 6.0, 7.0))
    a = as_array(m)
    assert a[:3, 1] == pytest.approx([0.0, 0.0, 1.0])
    assert a[:3, 3] == pytest.approx([5.0, 6.0, 7.0])


# --------------------------------------------------------------------------------------
# lens shift
# --------------------------------------------------------------------------------------


def test_principal_point_offset_is_a_film_fraction() -> None:
    """A shift of half the film width is an offset of 0.5 in normalised film coordinates."""
    ox, oy = tf.principal_point_offset_from_shift_mm((18.0, 0.0), 36.0, 16.0 / 9.0)
    assert ox == pytest.approx(0.5)
    assert oy == pytest.approx(0.0)


def test_principal_point_offset_vertical_uses_film_height() -> None:
    # 36 mm wide at 16:9 is 20.25 mm tall, so a 10.125 mm shift is half of it.
    _, oy = tf.principal_point_offset_from_shift_mm((0.0, 10.125), 36.0, 16.0 / 9.0)
    assert oy == pytest.approx(0.5)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def test_compose_trs_translation_lands_in_the_last_column() -> None:
    m = tf.compose_trs((1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert tf.transform_point(m, (0.0, 0.0, 0.0)) == pytest.approx((1.0, 2.0, 3.0))


def test_compose_trs_rotation_is_right_handed() -> None:
    """+90 degrees about Z takes +X to +Y in a right-handed frame."""
    m = tf.compose_trs((0.0, 0.0, 0.0), (90.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert tf.transform_point(m, (1.0, 0.0, 0.0)) == pytest.approx((0.0, 1.0, 0.0), abs=1e-12)


def test_transform_vector_ignores_translation() -> None:
    m = tf.compose_trs((100.0, 200.0, 300.0), (0.0, 0.0, 0.0), (2.0, 2.0, 2.0))
    assert tf.transform_vector(m, (1.0, 0.0, 0.0)) == pytest.approx((2.0, 0.0, 0.0))


def test_matrix_is_row_major() -> None:
    """m[r*4+c] is row r, column c — asserted so a future refactor cannot quietly transpose.

    A transposed convention still produces a plausible image for rotations about a single
    axis, which is exactly how it survives review.
    """
    m = tf.compose_trs((7.0, 8.0, 9.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0))
    assert m[3] == pytest.approx(7.0)
    assert m[7] == pytest.approx(8.0)
    assert m[11] == pytest.approx(9.0)
    assert math.isclose(m[15], 1.0)


def test_rows_helper() -> None:
    assert tf.rows(tf.IDENTITY)[1] == (0.0, 1.0, 0.0, 0.0)
