"""Max lights → IR. Reads Max, emits IR, knows nothing about Mitsuba.

Two probe findings dominate this file.

**Max lights emit along local −Z.** A `targetSpot` at `[0, 0, 100]` aimed at the origin
reports `transform.row3 = [0, 0, 1]` — its local +Z points *away* from the target
(probe 03b). Mitsuba's `spot` and `directional` emit along local **+Z**. The flip happens
here, exactly once, while the Max convention is still in scope; `core.emit_dict` then treats
`Light.to_world`'s +Z column as the emission axis without qualification.

**Photometric enums are unresolved.** `intensityType` and `distribution` are plain integers
whose meanings probe 03b could not pin down: assigning a `Name` maps every name — including
nonsense ones — to the same integer, and no observable property changes when the integer
does. Only the defaults are treated as known (`intensityType = 1` is candela, matching Max's
documented 1500 cd default; `distribution = 0` is isotropic). Anything else converts as if
it were the default **and warns**, because guessing an enum is how a light ends up 683 times
too bright with nothing to point at.
"""

from dataclasses import dataclass, field

from pymxs import runtime as rt

from core import transform as tf
from core.ir import Light, PhotometricInfo, Warning_
from core.registry import LIGHTS, light
from core.units import (
    DEFAULT_EFFICACY_LM_PER_W,
    candela_to_watts_per_sr,
    split_rgb_preserving_luminance,
    spot_angles,
)

__all__ = ["LIGHT_GUARD", "LightContext", "translate_light"]

# Stamp printed at export so a Max session running a stale in-memory `lights.py` is
# obvious. After probe 03d the power switch is `on` only — never `enabled`.
LIGHT_GUARD = "on-only-03d"

INTENSITY_TYPE_CANDELA = 1
"""The only `intensityType` value probe 03b could confirm. See the module docstring."""

DISTRIBUTION_ISOTROPIC = 0
"""The only `distribution` value probe 03b could confirm."""

_STANDARD_MULTIPLIER_TO_CANDELA = 1000.0
"""Standard (non-photometric) lights carry a unitless `multiplier`, not a photometric
quantity. Max's own renderers treat `multiplier = 1` as a nominal full-strength light, so
one multiplier unit is mapped to 1000 cd — bright enough to light a room at a few metres in
a scene authored in metres.

This is a convention, not a measurement, and it is the one number in this file with no
physical justification. It is stated in `docs/MATERIAL_MAPPING.md` as approximate, and it is
why photometric lights are the supported path: they carry real candela.
"""


@dataclass
class LightContext:
    scene_scale_to_meters: float = 1.0
    luminous_efficacy: float = DEFAULT_EFFICACY_LM_PER_W
    warnings: list[Warning_] = field(default_factory=list)

    def warn(self, node: str, reason: str, category: str = "light") -> None:
        self.warnings.append(Warning_(node=node, reason=reason, category=category))


# --------------------------------------------------------------------------------------
# shared readers
# --------------------------------------------------------------------------------------


def _color(c) -> tuple[float, float, float]:
    """Max `Color` (0–255) → RGB (0–1). Probe 03b: `omni.rgb` reads back `[255, 128, 0]`."""
    return (float(c.r) / 255.0, float(c.g) / 255.0, float(c.b) / 255.0)


def _emission_frame(node, scale_to_meters: float) -> tuple[float, ...]:
    """Node transform → a Mitsuba-space matrix whose **+Z column is the emission axis**.

    Max's `transform.rowN` are the local axes expressed in world space, which are the
    *columns* of the column-vector matrix `core.transform` uses. The Z axis is negated on
    the way through, because Max lights emit along local −Z (probe 03b).

    The X axis is negated too, so the basis stays right-handed. Without that the matrix has
    negative determinant, which is harmless for a point light and produces a mirrored
    projection for anything with a texture or a non-circular profile later.
    """
    m = node.transform
    x = tf.vector_max_to_mitsuba((float(m.row1.x), float(m.row1.y), float(m.row1.z)))
    y = tf.vector_max_to_mitsuba((float(m.row2.x), float(m.row2.y), float(m.row2.z)))
    z = tf.vector_max_to_mitsuba((float(m.row3.x), float(m.row3.y), float(m.row3.z)))
    origin = tf.point_max_to_mitsuba(
        (float(m.row4.x), float(m.row4.y), float(m.row4.z)), scale_to_meters
    )
    return tf.from_axes((-x[0], -x[1], -x[2]), y, (-z[0], -z[1], -z[2]), origin)


def _light_id(node) -> str:
    return f"light_{int(rt.getHandleByAnim(node))}"


# --------------------------------------------------------------------------------------
# standard lights
# --------------------------------------------------------------------------------------


@light("Omnilight")
def translate_omni(node, ctx: LightContext) -> Light | None:
    """Standard omni → Mitsuba `point`.

    Approximate: `multiplier` is unitless, so the conversion goes through the documented
    `_STANDARD_MULTIPLIER_TO_CANDELA` convention rather than a measurement.
    """
    name = str(node.name)
    _warn_attenuation(node, ctx, name)
    intensity_cd = float(node.multiplier) * _STANDARD_MULTIPLIER_TO_CANDELA
    watts = candela_to_watts_per_sr(intensity_cd, ctx.luminous_efficacy)
    return Light(
        id=_light_id(node),
        name=name,
        kind="point",
        to_world=_emission_frame(node, ctx.scene_scale_to_meters),
        radiance_rgb=split_rgb_preserving_luminance(watts, _color(node.rgb)),
        photometric_source=PhotometricInfo(
            intensity_cd=intensity_cd,
            efficacy_lm_per_w=ctx.luminous_efficacy,
            max_light_type="Omnilight (multiplier convention)",
        ),
    )


@light("freeSpot", "targetSpot")
def translate_spot(node, ctx: LightContext) -> Light | None:
    """Standard spot → Mitsuba `spot`.

    `hotspot` and `falloff` are **full** cone angles; Mitsuba wants half angles, and
    `core.units.spot_angles` does the halving. Approximate: Max's penumbra falls off
    linearly in the angle and Mitsuba's is a smooth cubic in the cosine, so the cone edges
    coincide but the gradient between them does not.
    """
    name = str(node.name)
    _warn_attenuation(node, ctx, name)
    if bool(getattr(node, "overShoot", False)):
        ctx.warn(name, "overshoot makes the light omnidirectional outside its cone, "
                       "which Mitsuba's spot cannot express; it was ignored")
    if int(getattr(node, "coneShape", 0)) != 0:
        ctx.warn(name, "a rectangular light cone is not supported; a circular cone "
                       "was used instead")

    cutoff, beam = spot_angles(float(node.hotspot), float(node.falloff))
    intensity_cd = float(node.multiplier) * _STANDARD_MULTIPLIER_TO_CANDELA
    watts = candela_to_watts_per_sr(intensity_cd, ctx.luminous_efficacy)
    return Light(
        id=_light_id(node),
        name=name,
        kind="spot",
        to_world=_emission_frame(node, ctx.scene_scale_to_meters),
        radiance_rgb=split_rgb_preserving_luminance(watts, _color(node.rgb)),
        cutoff_angle_deg=cutoff,
        beam_width_deg=beam,
        photometric_source=PhotometricInfo(
            intensity_cd=intensity_cd,
            efficacy_lm_per_w=ctx.luminous_efficacy,
            max_light_type=f"{rt.classOf(node)} (multiplier convention)",
        ),
    )


@light("Directionallight", "targetDirectionallight")
def translate_directional(node, ctx: LightContext) -> Light | None:
    """Standard directional → Mitsuba `directional`, whose parameter is irradiance in W/m².

    Approximate twice over: the multiplier convention, and the fact that Max's directional
    light is a finite cylinder with hotspot/falloff while Mitsuba's is infinite and
    unbounded. Nodes relying on the cylinder's edge will differ.
    """
    name = str(node.name)
    irradiance = (float(node.multiplier) * _STANDARD_MULTIPLIER_TO_CANDELA
                  / ctx.luminous_efficacy)
    ctx.warn(name, "a Max directional light is a bounded cylinder; Mitsuba's directional "
                   "emitter is unbounded, so the hotspot/falloff extent was ignored")
    return Light(
        id=_light_id(node),
        name=name,
        kind="directional",
        to_world=_emission_frame(node, ctx.scene_scale_to_meters),
        radiance_rgb=split_rgb_preserving_luminance(irradiance, _color(node.rgb)),
    )


# --------------------------------------------------------------------------------------
# photometric lights
# --------------------------------------------------------------------------------------


@light(
    "Free_Light", "Target_Light",       # point (Create-panel default / Free_Point)
    "Free_Sphere", "Target_Sphere",
    "Free_Disc", "Target_Disc",
    "Free_Area", "Target_Area",
    "Free_Cylinder", "Target_Cylinder",
)
def translate_photometric(node, ctx: LightContext) -> Light | None:
    """Photometric light → `point` or `spot`, with a real candela → W/sr conversion.

    This is the supported path, because `intensity` is a genuine photometric quantity
    rather than a unitless multiplier. `I_e = I_v / eta`, and the provenance is recorded on
    the IR node so the UI can show its working.

    Max 2027 exposes one class per emitter shape (probe 03g): `Free_Area`, `Target_Disc`,
    `Free_Sphere`, … all share the same photometric properties as `Free_Light`. v1 keeps
    the candela→W/sr conversion and approximates every shape as a point/spot; the physical
    extent (`light_Width` / `light_length` / `light_Radius`) is not emitted yet and is
    warned when the class is not the point form.
    """
    name = str(node.name)
    if not bool(node.on):
        return None

    cls = str(rt.classOf(node))
    if cls not in ("Free_Light", "Target_Light"):
        ctx.warn(name,
                 f"{cls} has a finite emitter shape; v1 approximates it as a point/spot "
                 "and ignores light_Width/light_length/light_Radius")

    intensity_type = int(node.intensityType)
    if intensity_type != INTENSITY_TYPE_CANDELA:
        ctx.warn(name,
                 f"intensityType {intensity_type} could not be identified (probe 03c is "
                 "open); the value was treated as candela, which may be wrong by a large "
                 "factor if it is lumens or lux")

    distribution = int(node.distribution)
    if distribution != DISTRIBUTION_ISOTROPIC:
        ctx.warn(name,
                 f"distribution {distribution} could not be identified (probe 03c is "
                 "open); an isotropic distribution was used")
    if str(node.webFile):
        ctx.warn(name, f"the photometric web {node.webFile!s} is not supported; "
                       "an isotropic distribution was used instead")

    intensity_cd = float(node.intensity)
    if bool(node.useMultiplier):
        # `multiplier` is a percentage dimmer on photometric lights, defaulting to 100.
        intensity_cd *= float(node.multiplier) / 100.0

    watts = candela_to_watts_per_sr(intensity_cd, ctx.luminous_efficacy)
    color = _color(node.rgbFilter)
    if bool(node.useKelvin):
        ctx.warn(name, f"colour temperature {float(node.kelvin):g}K is not converted to "
                       "RGB; the filter colour was used as authored")

    provenance = PhotometricInfo(
        intensity_cd=intensity_cd,
        efficacy_lm_per_w=ctx.luminous_efficacy,
        max_light_type=cls,
    )
    to_world = _emission_frame(node, ctx.scene_scale_to_meters)
    radiance = split_rgb_preserving_luminance(watts, color)

    # A photometric light keeps hotspot/falloff regardless of distribution, and they are
    # only meaningful for the spotlight distribution. Since the distribution enum is
    # unresolved, the safe reading is the confirmed default: isotropic.
    if distribution == DISTRIBUTION_ISOTROPIC:
        return Light(id=_light_id(node), name=name, kind="point", to_world=to_world,
                     radiance_rgb=radiance, photometric_source=provenance)

    cutoff, beam = spot_angles(float(node.hotspot), float(node.falloff))
    return Light(id=_light_id(node), name=name, kind="spot", to_world=to_world,
                 radiance_rgb=radiance, cutoff_angle_deg=cutoff, beam_width_deg=beam,
                 photometric_source=provenance)


# --------------------------------------------------------------------------------------
# shared warnings and dispatch
# --------------------------------------------------------------------------------------


def _warn_attenuation(node, ctx: LightContext, name: str) -> None:
    """Max's near/far attenuation is an artistic falloff with no physical counterpart.

    Mitsuba's emitters are strictly inverse-square. Clamping a light's range is a lighting
    decision the artist made deliberately, so it gets a warning rather than being ignored
    in silence.
    """
    if bool(getattr(node, "useFarAtten", False)):
        ctx.warn(name, "far attenuation is not physical and is not supported; the light "
                       "falls off as 1/r^2 without a cutoff")
    if bool(getattr(node, "useNearAtten", False)):
        ctx.warn(name, "near attenuation is not supported and was ignored")
    if int(getattr(node, "attenDecay", 0)) not in (0, 2):
        ctx.warn(name, "a non-inverse-square decay type is not supported; the light "
                       "falls off as 1/r^2")


def translate_light(node, ctx: LightContext) -> Light | None:
    """Dispatch on `classOf`. Unsupported light classes are skipped with a warning.

    Skipping rather than substituting, because there is no honest placeholder for a light:
    a guessed stand-in changes the whole image, whereas a missing one is obvious and
    correctly attributed by the warnings panel.

    The on/off switch is `node.on` only. Photometric lights (`Free_Light`, `Target_Light`)
    also expose an `enabled` property that defaults to **False** while the light is on
    (probe 03d); treating that as the power switch silently dropped every photometric
    light in the scene.
    """
    import importlib

    import max_side.lights as lights_mod

    # Re-import is cheap and fixes the stale-registry case where `core` was reloaded but
    # this module's decorator side effects did not run against the new LIGHTS object.
    if len(LIGHTS) == 0:
        importlib.reload(lights_mod)

    name = str(node.name)
    # Sun_Positioner has no `on`; default True so an unsupported class still reaches the
    # warning path below rather than vanishing without a trace.
    if hasattr(node, "on") and not bool(node.on):
        ctx.warn(name, "light is off (node.on = false) and was skipped")
        return None

    cls = str(rt.classOf(node))
    handler = LIGHTS.lookup(cls)
    if handler is None:
        ctx.warn(name, f"light class {cls} is not supported in v1 and was skipped")
        return None
    return handler(node, ctx)
