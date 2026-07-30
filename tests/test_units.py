"""Unit conversions, with hand-computed expected values.

core/CLAUDE.md asks specifically for this style: `I_e = I_v / η` with `I_v = 1000 cd` and
`η = 250 lm/W` gives `4.0 W/sr`. A conversion function that only agrees with itself is not
tested at all.
"""

import math

import pytest

from core import units

# --------------------------------------------------------------------------------------
# scene scale
# --------------------------------------------------------------------------------------


def test_centimetre_scene() -> None:
    """`decodeValue("1.0m")` returns 100 in a centimetre scene, so the factor is 0.01."""
    assert units.scene_scale_from_decode_value(100.0) == pytest.approx(0.01)


def test_metre_scene() -> None:
    assert units.scene_scale_from_decode_value(1.0) == pytest.approx(1.0)


def test_inch_scene() -> None:
    """39.3701 system units per metre means each unit is 25.4 mm."""
    assert units.scene_scale_from_decode_value(39.3700787) == pytest.approx(0.0254, rel=1e-6)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_nonsense_scale_raises(bad: float) -> None:
    with pytest.raises(ValueError):
        units.scene_scale_from_decode_value(bad)


def test_absurdly_small_scale_raises() -> None:
    """1e-7 units per metre would mean one unit is 10 000 km. Fail loudly, not silently."""
    with pytest.raises(ValueError, match="plausible range"):
        units.scene_scale_from_decode_value(1e-7)


# --------------------------------------------------------------------------------------
# photometry
# --------------------------------------------------------------------------------------


def test_candela_to_watts_per_sr_worked_example() -> None:
    """1000 cd at 250 lm/W is exactly 4 W/sr."""
    assert units.candela_to_watts_per_sr(1000.0, 250.0) == pytest.approx(4.0)


def test_candela_uses_the_default_efficacy() -> None:
    assert units.DEFAULT_EFFICACY_LM_PER_W == 250.0
    assert units.candela_to_watts_per_sr(1500.0) == pytest.approx(6.0)


def test_efficacy_must_be_positive() -> None:
    with pytest.raises(ValueError, match="efficacy"):
        units.candela_to_watts_per_sr(100.0, 0.0)


def test_luminance_to_radiance_matches_intensity_conversion() -> None:
    """Both are the same division; the per-area factor cancels."""
    assert units.luminance_to_radiance(1500.0, 250.0) == pytest.approx(6.0)


def test_luminance_weights_sum_to_one() -> None:
    assert units.luminance((1.0, 1.0, 1.0)) == pytest.approx(1.0)


def test_luminance_of_pure_green_is_the_green_weight() -> None:
    assert units.luminance((0.0, 1.0, 0.0)) == pytest.approx(0.7152)


def test_rgb_split_preserves_luminance() -> None:
    """The whole point: tinting a light changes its colour, not its photometric output."""
    for color in [(1.0, 1.0, 1.0), (1.0, 0.5, 0.2), (0.1, 0.9, 0.4), (0.0, 0.0, 1.0)]:
        out = units.split_rgb_preserving_luminance(4.0, color)  # type: ignore[arg-type]
        assert units.luminance(out) == pytest.approx(4.0)


def test_rgb_split_of_white_is_uniform() -> None:
    assert units.split_rgb_preserving_luminance(4.0, (1.0, 1.0, 1.0)) == pytest.approx(
        (4.0, 4.0, 4.0)
    )


def test_rgb_split_of_black_falls_back_to_neutral() -> None:
    """A black light has no direction to point energy in. Do not raise on it — a user can
    plausibly have one in a scene and it must not abort the whole export."""
    assert units.split_rgb_preserving_luminance(2.0, (0.0, 0.0, 0.0)) == (2.0, 2.0, 2.0)


def test_point_emitter_flux_identity() -> None:
    """Φ = 4π I. A 1 W/sr isotropic point emitter radiates 4π W."""
    assert units.flux_from_point_intensity(1.0) == pytest.approx(4.0 * math.pi)


def test_area_emitter_radiance_identity() -> None:
    """L = Φ / (π A), not 2π A. Integrating L·cosθ over the hemisphere gives πL."""
    assert units.area_radiance_from_flux(math.pi, 1.0) == pytest.approx(1.0)
    assert units.area_radiance_from_flux(4.0 * math.pi, 2.0) == pytest.approx(2.0)


def test_area_emitter_rejects_degenerate_area() -> None:
    with pytest.raises(ValueError, match="area must be positive"):
        units.area_radiance_from_flux(1.0, 0.0)


# --------------------------------------------------------------------------------------
# spot cones
# --------------------------------------------------------------------------------------


def test_spot_angles_are_halved() -> None:
    """Max stores full cone angles; Mitsuba wants half angles."""
    cutoff, beam = units.spot_angles(hotspot_full_deg=30.0, falloff_full_deg=60.0)
    assert cutoff == pytest.approx(30.0)
    assert beam == pytest.approx(15.0)


def test_spot_beam_is_clamped_to_the_cutoff() -> None:
    """Max allows hotspot > falloff mid-edit; Mitsuba requires beam_width <= cutoff."""
    cutoff, beam = units.spot_angles(hotspot_full_deg=90.0, falloff_full_deg=45.0)
    assert cutoff == pytest.approx(22.5)
    assert beam == pytest.approx(22.5)


def test_spot_rejects_zero_falloff() -> None:
    with pytest.raises(ValueError, match="falloff"):
        units.spot_angles(10.0, 0.0)


# --------------------------------------------------------------------------------------
# participating media
# --------------------------------------------------------------------------------------


def test_sigma_t_beer_lambert() -> None:
    """A channel transmitting 1/e over 1 m has σ_t = 1.

    exp(-1) = 0.36788, so `-ln(0.36788) / 1 m` is 1.0 per metre.
    """
    sigma = units.sigma_t_from_transmission((math.exp(-1.0),) * 3, 1.0, 1.0)
    assert sigma == pytest.approx((1.0, 1.0, 1.0))


def test_sigma_t_applies_scene_scale() -> None:
    """A 100-unit depth in a centimetre scene is 1 m, not 100 m.

    Skipping the scale conversion here gives an extinction wrong by exactly the scene
    scale — 100x on a centimetre scene, which turns tinted glass into clear glass.
    """
    scaled = units.sigma_t_from_transmission((math.exp(-1.0),) * 3, 100.0, 0.01)
    assert scaled == pytest.approx((1.0, 1.0, 1.0))


def test_sigma_t_clamps_opaque_channels() -> None:
    """A channel at 0.0 means "opaque at any distance" and would give infinity."""
    sigma = units.sigma_t_from_transmission((0.0, 0.5, 1.0), 1.0, 1.0)
    assert all(math.isfinite(s) for s in sigma)
    assert sigma[0] > sigma[1] > sigma[2]
    assert sigma[2] == pytest.approx(0.0, abs=1e-5)


def test_sigma_t_rejects_zero_depth() -> None:
    with pytest.raises(ValueError, match="trans_depth"):
        units.sigma_t_from_transmission((0.5, 0.5, 0.5), 0.0, 1.0)


# --------------------------------------------------------------------------------------
# exposure
# --------------------------------------------------------------------------------------


def test_exposure_scale_doubles_per_stop() -> None:
    base = units.exposure_scale(iso=100.0, exposure_value=0.0, calibration_k=1.0)
    one_stop_brighter = units.exposure_scale(iso=100.0, exposure_value=-1.0,
                                             calibration_k=1.0)
    assert base == pytest.approx(1.0)
    assert one_stop_brighter == pytest.approx(2.0)


def test_exposure_scale_is_linear_in_iso() -> None:
    assert units.exposure_scale(400.0, 0.0, 1.0) == pytest.approx(4.0)
