"""Unit conversions: scene scale, photometry, cone angles, Beer–Lambert extinction.

Pure functions, no I/O. Every function states its source and target units, and every
physical conversion has a worked example in its docstring that the tests assert against.

Why this file is not cosmetic: irradiance from a point emitter goes as `E = I / r²`, so a
scene authored in centimetres but rendered as though it were metres is wrong by a factor
of 10⁴. Getting the scale right is the difference between "the lighting looks a bit off"
and "the image is black".
"""

import math

from core.ir import Rgb

__all__ = [
    "DEFAULT_EFFICACY_LM_PER_W",
    "K_M",
    "area_radiance_from_flux",
    "candela_to_watts_per_sr",
    "exposure_scale",
    "flux_from_point_intensity",
    "luminance",
    "luminance_to_radiance",
    "scene_scale_from_decode_value",
    "sigma_t_from_transmission",
    "split_rgb_preserving_luminance",
    "spot_angles",
]

K_M = 683.0
"""Maximum luminous efficacy of radiation, lm/W, at 555 nm."""

DEFAULT_EFFICACY_LM_PER_W = 250.0
"""Default luminous efficacy of radiation for a white source.

`I_v = K_m ∫ V(λ) I_e,λ(λ) dλ` cannot be evaluated in an RGB variant — there is no
spectrum to integrate. The standard workaround is the luminous efficacy of radiation
`η = 683 ∫ V(λ) s(λ) dλ` for the source's normalised SPD, giving `I_e = I_v / η`.
Typical white sources land in 200–350 lm/W. This is exposed in the UI as
"luminous efficacy (lm/W)" with a tooltip saying it approximates a spectral integral,
because the honest answer is that RGB rendering cannot do better.
"""

_LUMINANCE_WEIGHTS = (0.2126, 0.7152, 0.0722)
"""Rec. 709 / sRGB luminance coefficients."""


# --------------------------------------------------------------------------------------
# scene scale
# --------------------------------------------------------------------------------------


def scene_scale_from_decode_value(system_units_per_meter: float) -> float:
    """Max system units per metre → metres per system unit.

    The caller obtains the argument from `rt.units.decodeValue("1.0m")`, which is the only
    reliable source: `rt.units.getMasterScale` does not exist in Max 2027 (probe 01b), and
    a `SystemType`-name lookup table would need extending for every unit Max supports and
    would break silently on generic units.

    A scene in centimetres reports 100.0 and this returns 0.01.

    Raises on anything outside `0 < s < 1e6`, deliberately loudly: a wrong scale is
    invisible in the viewport and catastrophic in the render.
    """
    v = float(system_units_per_meter)
    if not math.isfinite(v) or v <= 0.0:
        raise ValueError(f"decodeValue('1.0m') returned a nonsensical {v!r}")
    scale = 1.0 / v
    if not (0.0 < scale < 1e6):
        raise ValueError(f"scene_scale_to_meters {scale!r} is outside the plausible range")
    return scale


# --------------------------------------------------------------------------------------
# photometry
# --------------------------------------------------------------------------------------


def luminance(rgb: Rgb) -> float:
    """Rec. 709 relative luminance of a linear RGB triple."""
    r, g, b = rgb
    wr, wg, wb = _LUMINANCE_WEIGHTS
    return wr * float(r) + wg * float(g) + wb * float(b)


def candela_to_watts_per_sr(
    intensity_cd: float, efficacy_lm_per_w: float = DEFAULT_EFFICACY_LM_PER_W
) -> float:
    """Luminous intensity in cd (lm/sr) → radiant intensity in W/sr.

    `I_e = I_v / η`. With `I_v = 1000 cd` and `η = 250 lm/W` this is exactly `4.0 W/sr`.

    Approximate: η stands in for `683 ∫ V(λ) s(λ) dλ`, which an RGB renderer cannot
    evaluate. The error is a single global multiplier per light, not a spatial artefact.
    """
    eta = float(efficacy_lm_per_w)
    if not math.isfinite(eta) or eta <= 0.0:
        raise ValueError(f"luminous efficacy must be positive, got {eta!r}")
    return float(intensity_cd) / eta


def luminance_to_radiance(
    luminance_cd_per_m2: float, efficacy_lm_per_w: float = DEFAULT_EFFICACY_LM_PER_W
) -> float:
    """Luminance in cd/m² → radiance in W/(sr·m²).

    Same division as `candela_to_watts_per_sr`; both sides of the ratio just carry an extra
    per-area factor. Used for `PhysicalMaterial.emit_luminance` and for area lights.
    """
    return candela_to_watts_per_sr(luminance_cd_per_m2, efficacy_lm_per_w)


def split_rgb_preserving_luminance(magnitude: float, color: Rgb) -> Rgb:
    """Distribute a scalar radiometric quantity across RGB without changing its luminance.

    Emits `magnitude * c / Y` where `Y = 0.2126 R + 0.7152 G + 0.0722 B`. The result has
    luminance exactly `magnitude`, so tinting a light changes its colour but not its
    photometric output — which is what an artist setting a light to "4 W/sr, warm white"
    expects.

    A colour of pure black has no direction to point the energy in; `Y <= 1e-6` is treated
    as neutral white rather than raising, because a black light is a plausible thing for a
    user to have left in a scene and should not abort the export.
    """
    y = luminance(color)
    if y <= 1e-6:
        m = float(magnitude)
        return (m, m, m)
    k = float(magnitude) / y
    return (float(color[0]) * k, float(color[1]) * k, float(color[2]) * k)


def flux_from_point_intensity(intensity_w_per_sr: float) -> float:
    """Radiant intensity of an isotropic point emitter → total flux. `Φ = 4π I`."""
    return 4.0 * math.pi * float(intensity_w_per_sr)


def area_radiance_from_flux(flux_w: float, area_m2: float) -> float:
    """Total flux of a Lambertian area emitter → its radiance. `L = Φ / (π A)`.

    The π, not 2π: integrating `L cos θ` over the hemisphere gives `π L`, not `2π L`.
    Dropping the cosine is the single most common factor-of-two error in emitter code.
    """
    a = float(area_m2)
    if a <= 0.0:
        raise ValueError(f"area must be positive, got {a!r}")
    return float(flux_w) / (math.pi * a)


# --------------------------------------------------------------------------------------
# spot cones
# --------------------------------------------------------------------------------------


def spot_angles(hotspot_full_deg: float, falloff_full_deg: float) -> tuple[float, float]:
    """Max hotspot/falloff (**full** cone angles) → Mitsuba `(cutoff_angle, beam_width)`.

    Both Mitsuba parameters are **half** angles, so `cutoff = falloff / 2` and
    `beam_width = hotspot / 2`. Max permits hotspot > falloff in some edit states; the
    result is clamped so `beam_width <= cutoff`, which Mitsuba requires.

    Approximate: Max's penumbra falls off linearly in the angle, Mitsuba's is a smooth
    cubic in the cosine. The cone edges coincide, the gradient between them does not.
    Documented as approximate in `docs/MATERIAL_MAPPING.md`.
    """
    cutoff = float(falloff_full_deg) / 2.0
    beam = float(hotspot_full_deg) / 2.0
    if cutoff <= 0.0:
        raise ValueError(f"falloff angle must be positive, got {falloff_full_deg!r}")
    return (cutoff, min(beam, cutoff))


# --------------------------------------------------------------------------------------
# participating media
# --------------------------------------------------------------------------------------


def sigma_t_from_transmission(
    trans_color: Rgb, trans_depth_scene_units: float, scene_scale_to_meters: float
) -> Rgb:
    """Max `trans_color` / `trans_depth` → per-channel extinction `σ_t` in 1/m.

    Beer–Lambert: a ray travelling `d` through the medium is attenuated by `exp(-σ_t d)`,
    and Max defines `trans_depth` as the distance at which the transmitted colour equals
    `trans_color`. Hence `σ_t = -ln(trans_color) / d`.

    `d` arrives in system units and `σ_t` is per unit length, so the depth is converted to
    metres first — omitting that gives an extinction wrong by the same factor as the scene
    scale, i.e. 100x on a centimetre scene.

    Channels are clamped away from the singularities: a channel at 1.0 means "never
    absorbs" and would give `σ_t = 0`, which is fine, but a channel at 0.0 means "opaque at
    any distance" and would give infinity. Clamped to `[1e-4, 1 - 1e-6]`.
    """
    d_m = float(trans_depth_scene_units) * float(scene_scale_to_meters)
    if d_m <= 0.0:
        raise ValueError(f"trans_depth must be positive, got {trans_depth_scene_units!r}")
    out: list[float] = []
    for c in trans_color:
        cc = min(max(float(c), 1e-4), 1.0 - 1e-6)
        out.append(-math.log(cc) / d_m)
    return (out[0], out[1], out[2])


# --------------------------------------------------------------------------------------
# exposure
# --------------------------------------------------------------------------------------


def exposure_scale(iso: float, exposure_value: float, calibration_k: float) -> float:
    """Physical camera exposure settings → a multiplier for the host exposure slider.

    `scale = (ISO / 100) * 2^(-EV) * K`.

    This is *not* baked into the render. Mitsuba's film has no exposure control and this
    project tone-maps on the host from the cached float buffer, so the camera's
    photographic settings set the slider's starting position and remain live afterwards.

    `calibration_k` depends on the metering convention and has no correct a-priori value;
    `docs/MATERIAL_MAPPING.md` records the fit procedure and the fitted number. Passing a
    guess here is worse than passing 1.0, because a guess looks calibrated.
    """
    stops: float = 2.0 ** -float(exposure_value)
    return (float(iso) / 100.0) * stops * float(calibration_k)
