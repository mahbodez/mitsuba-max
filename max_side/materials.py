"""`PhysicalMaterial` → IR. Reads Max, emits IR, and knows nothing about Mitsuba.

Every property name here comes from probe 07's full dump of a default `PhysicalMaterial`
(120 properties) — none of it is written from memory, and there are no `getattr` fallback
chains. Where the probe showed a name from `SPEC.md` §9 does not exist (`aniso_angle`,
`trans_rough_inv`, `coat_rough_inv`), the real name is used instead.

Two facts from the probe shape most of this file:

* **Max colours are 0–255 floats**, not 0–1. A default `base_color` reads
  `(127.5, 127.5, 127.5)`. The division happens once, in `_color`.
* **`roughness_inv` is real.** When set, `roughness` holds *glossiness* and must be
  inverted. Mitsuba has no arithmetic texture node, so an inverted *map* has to be baked
  into a new image at export — see `_bake_inverted`.

Anything that is not a `PhysicalMaterial` with `Bitmaptexture` inputs becomes a 50% grey
placeholder plus a `Warning_` naming the node and the class. Silent substitution is a
defect (SPEC §1).
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pymxs import runtime as rt

from core.assets import AssetStore
from core.ir import Emission, Material, TextureRef, Warning_
from core.registry import MATERIALS, material
from core.units import (
    DEFAULT_EFFICACY_LM_PER_W,
    luminance_to_radiance,
    sigma_t_from_transmission,
)

__all__ = ["MaterialContext", "translate_material"]


# --------------------------------------------------------------------------------------
# slot table
# --------------------------------------------------------------------------------------

RAW_SLOTS = frozenset({
    "roughness_map", "metalness_map", "bump_map", "coat_bump_map", "anisotropy_map",
    "aniso_angle_map", "coat_rough_map", "coat_aniso_map", "coat_aniso_angle_map",
    "trans_rough_map", "sheen_rough_map", "displacement_map", "cutout_map",
    "base_weight_map", "reflectivity_map", "transparency_map", "scattering_map",
    "sss_scale_map", "emission_map", "coat_map", "sheen_map", "thin_film_map",
    "thin_film_ior_map", "trans_ior_map", "diff_rough_map",
})
"""Map slots carrying non-colour data, which must be sampled with `raw = True`.

Probe 09 settled why this table exists rather than a gamma check: `bt.bitmap.gamma` reports
the *file's* gamma, 2.2 for an ordinary PNG, whether that PNG holds albedo or roughness. A
gamma-only rule would decode every roughness map as sRGB — wrong everywhere, with no
obvious visual tell. The slot is the only reliable signal, with an explicit gamma of 1.0
able to force raw on top.
"""

COLOR_SLOTS = frozenset({
    "base_color_map", "refl_color_map", "trans_color_map", "emit_color_map",
    "coat_color_map", "sss_color_map", "sheen_color_map",
})


@dataclass
class MaterialContext:
    """Everything a translator needs that is not the material itself."""

    assets: AssetStore
    scene_scale_to_meters: float = 1.0
    luminous_efficacy: float = DEFAULT_EFFICACY_LM_PER_W
    warnings: list[Warning_] = field(default_factory=list)
    _by_handle: dict[int, Material] = field(default_factory=dict)

    def warn(self, node: str, reason: str, category: str = "material") -> None:
        self.warnings.append(Warning_(node=node, reason=reason, category=category))


# --------------------------------------------------------------------------------------
# small readers
# --------------------------------------------------------------------------------------


def _color(c) -> tuple[float, float, float]:
    """Max `Color` (0–255) → linear RGB (0–1).

    Approximate in one respect worth naming: Max stores these as display-referred sRGB
    values and this divides without applying the sRGB decode, matching how Max's own
    physical material feeds them to the renderer. Applying a decode here would darken every
    material by roughly 2.2 gamma and look like an exposure bug.
    """
    return (float(c.r) / 255.0, float(c.g) / 255.0, float(c.b) / 255.0)


def _slot(mat, name: str):
    """The texmap in `<name>` if it is present and its `<name>_on` flag is set.

    Both halves matter: Max keeps the map assigned when the artist unticks the checkbox, so
    reading the slot alone renders maps the artist has switched off.
    """
    tex = getattr(mat, name)
    if tex is None:
        return None
    if not bool(getattr(mat, f"{name}_on")):
        return None
    return tex


def _bake_inverted(ctx: MaterialContext, path: Path, mat_name: str) -> Path | None:
    """Write `1 - image` next to the export and return its path.

    Needed because Max stores glossiness in `roughness` when `roughness_inv` is set, and
    Mitsuba has no texture node that can subtract. The inversion runs entirely inside
    MAXScript — `getPixels` returns a list of `Color` wrappers, and marshalling four million
    of them into Python to flip three bytes each would take longer than the render.
    """
    dst = ctx.assets.root / "textures" / f"inv_{uuid.uuid4().hex[:16]}.png"
    dst.parent.mkdir(parents=True, exist_ok=True)
    rt.execute("\n".join((
        "fn mmx_invert_bitmap src dst = (",
        "  local b = openBitMap src",
        "  local o = bitmap b.width b.height filename:dst",
        "  for y = 0 to (b.height - 1) do (",
        "    local row = getPixels b [0, y] b.width",
        "    for i = 1 to row.count do (",
        "      local c = row[i]",
        "      row[i] = color (255 - c.r) (255 - c.g) (255 - c.b) c.a",
        "    )",
        "    setPixels o [0, y] row",
        "  )",
        "  save o",
        "  close o",
        "  close b",
        "  true",
        ")",
    )))
    try:
        rt.mmx_invert_bitmap(str(path).replace("\\", "/"), str(dst).replace("\\", "/"))
    except Exception as exc:  # noqa: BLE001
        ctx.warn(mat_name, f"could not invert the glossiness map {path.name}: {exc}")
        return None
    if not dst.is_file():
        ctx.warn(mat_name, f"inverting {path.name} produced no file")
        return None
    return dst


def _texture(ctx: MaterialContext, mat, slot: str, mat_name: str, *,
             invert: bool = False) -> TextureRef | None:
    """One map slot as a `TextureRef`, or `None` with a warning if it is not a bitmap."""
    tex = _slot(mat, slot)
    if tex is None:
        return None

    cls = str(rt.classOf(tex))
    if cls != "Bitmaptexture":
        ctx.warn(mat_name,
                 f"map slot {slot} holds a {cls}; v1 supports Bitmaptexture only, "
                 "so the slot was ignored and the constant value used instead")
        return None

    path = Path(str(tex.filename))
    if not str(path):
        ctx.warn(mat_name, f"map slot {slot} has no file assigned")
        return None
    if not path.is_file():
        ctx.warn(mat_name, f"map slot {slot} points at a missing file: {path}",
                 category="asset")
        return None

    if invert:
        inverted = _bake_inverted(ctx, path, mat_name)
        if inverted is None:
            return None
        path = inverted

    raw = slot in RAW_SLOTS
    # An explicit gamma of 1.0 means the artist has told Max this file is linear data, which
    # overrides the slot's default. Probe 09: gamma alone cannot decide, but it can force.
    try:
        if float(tex.bitmap.gamma) == 1.0:
            raw = True
    except Exception:  # noqa: BLE001 - `bitmap` is undefined until the file loads
        pass

    coords = tex.coords
    rel = ctx.assets.add_file(path, subdir="textures", source=f"{mat_name} {slot}")
    return TextureRef(
        path=rel,
        raw=raw,
        # Approximate: Max applies offset and tiling through a full UV transform that can
        # also rotate and mirror. Only the scale and offset are carried; a non-zero
        # U_Angle/V_Angle warns below.
        uv_scale=(float(coords.U_Tiling), float(coords.V_Tiling)),
        uv_offset=(float(coords.U_Offset), float(coords.V_Offset)),
    )


def _check_uv_extras(ctx: MaterialContext, mat, slot: str, mat_name: str) -> None:
    tex = _slot(mat, slot)
    if tex is None or str(rt.classOf(tex)) != "Bitmaptexture":
        return
    coords = tex.coords
    if float(coords.U_Angle) or float(coords.V_Angle) or float(coords.W_angle):
        ctx.warn(mat_name, f"{slot}: UV rotation is not supported and was ignored")
    if bool(coords.U_Mirror) or bool(coords.V_Mirror):
        ctx.warn(mat_name, f"{slot}: UV mirroring is not supported and was ignored")
    if int(coords.mapChannel) != 1:
        ctx.warn(mat_name,
                 f"{slot}: map channel {int(coords.mapChannel)} is not supported; "
                 "v1 exports channel 1 only")


def _scalar_or_map(ctx: MaterialContext, mat, prop: str, slot: str, mat_name: str, *,
                   invert: bool = False) -> float | TextureRef:
    """A parameter's map if there is one, otherwise its constant value."""
    tex = _texture(ctx, mat, slot, mat_name, invert=invert)
    if tex is not None:
        _check_uv_extras(ctx, mat, slot, mat_name)
        return tex
    value = float(getattr(mat, prop))
    return 1.0 - value if invert else value


# --------------------------------------------------------------------------------------
# the translator
# --------------------------------------------------------------------------------------


@material("PhysicalMaterial")
def translate_physical_material(mat, ctx: MaterialContext) -> Material:
    """`PhysicalMaterial` → `principled`, or `roughdielectric` when it is transmissive.

    Max source parameters and their Mitsuba targets are tabulated in
    `docs/MATERIAL_MAPPING.md` with an exact / approximate / unsupported classification per
    row. The transmission branch is the one structural decision made here rather than
    there: `principled`'s `spec_trans` cannot express `trans_depth`, which is a
    Beer–Lambert absorption distance, so a transmissive material becomes a
    `roughdielectric` with an interior `homogeneous` medium instead.
    """
    name = str(mat.name)
    mat_id = f"mat_{int(rt.getHandleByAnim(mat))}"

    _warn_unsupported_features(ctx, mat, name)

    transparency = float(mat.transparency)
    if transparency > 0.0 and not bool(mat.thin_walled):
        return _translate_transmissive(mat, ctx, mat_id, name)

    params: dict[str, object] = {}

    # base_color x base_weight. Max multiplies the two; Mitsuba's principled has one slot,
    # so the weight is folded into the colour when it is constant, and warned about when it
    # is mapped (a per-texel product needs an arithmetic node Mitsuba does not have).
    base_map = _texture(ctx, mat, "base_color_map", name)
    weight = float(mat.base_weight)
    if base_map is not None:
        _check_uv_extras(ctx, mat, "base_color_map", name)
        params["base_color"] = base_map
        if weight != 1.0:
            ctx.warn(name, f"base_weight {weight:g} cannot be combined with a mapped "
                           "base_color and was ignored")
    else:
        r, g, b = _color(mat.base_color)
        params["base_color"] = (r * weight, g * weight, b * weight)

    params["roughness"] = _scalar_or_map(ctx, mat, "roughness", "roughness_map", name,
                                         invert=bool(mat.roughness_inv))
    params["metallic"] = _scalar_or_map(ctx, mat, "metalness", "metalness_map", name)

    # reflectivity -> specular. Approximate: Mitsuba's `specular = 0.5` corresponds to
    # eta = 1.5, i.e. F0 = 0.04, which is what Max's reflectivity = 1.0 means for a
    # dielectric. `specular` and `eta` are mutually exclusive in Mitsuba; only `specular` is
    # ever emitted.
    params["specular"] = _scalar_or_map(ctx, mat, "reflectivity", "reflectivity_map", name)

    anisotropy = float(mat.anisotropy)
    if anisotropy != 0.0:
        params["anisotropic"] = _scalar_or_map(ctx, mat, "anisotropy", "anisotropy_map", name)
        if float(mat.anisoangle) != 0.25:
            ctx.warn(name, "anisotropy angle has no Mitsuba equivalent and was ignored")

    coating = float(mat.coating)
    if coating != 0.0:
        params["clearcoat"] = _scalar_or_map(ctx, mat, "coating", "coat_map", name)
        coat_rough = float(mat.coat_roughness)
        if bool(mat.coat_roughness_inv):
            coat_rough = 1.0 - coat_rough
        # Approximate: `clearcoat_gloss` is the complement of the coat roughness.
        params["clearcoat_gloss"] = 1.0 - coat_rough
        if float(mat.coat_ior) != 1.52:
            ctx.warn(name, f"coat_ior {float(mat.coat_ior):g} is fixed at 1.5 by "
                           "Mitsuba's clearcoat lobe and was ignored")

    sheen = float(mat.sheen)
    if sheen != 0.0:
        params["sheen"] = sheen
        sr, sg, sb = _color(mat.sheen_color)
        # `sheen_tint` blends between white and the base colour, so a coloured sheen can
        # only be approximated by how far it is from white.
        params["sheen_tint"] = 1.0 - min(sr, sg, sb)

    emission = _emission(ctx, mat, name)
    normal_map = _texture(ctx, mat, "bump_map", name)
    if normal_map is not None and float(mat.bump_map_amt) != 1.0:
        ctx.warn(name, f"bump amount {float(mat.bump_map_amt):g} is not applied; Mitsuba's "
                       "normalmap has no strength control")

    return Material(
        id=mat_id,
        name=name,
        kind="principled",
        params=params,
        normal_map=normal_map,
        two_sided=True,
        emission=emission,
    )


def _translate_transmissive(mat, ctx: MaterialContext, mat_id: str,
                            name: str) -> Material:
    """A transmissive PhysicalMaterial as `roughdielectric` plus an absorbing medium.

    `principled`'s `spec_trans` is a single scalar and cannot express `trans_depth`, which
    is the distance at which the transmitted colour equals `trans_color`. Beer–Lambert gives
    `sigma_t = -ln(trans_color) / depth`, carried on the material as `__sigma_t` and
    attached to the shape as an interior `homogeneous` medium by `core.emit_dict`.
    """
    trans_rough = float(mat.trans_roughness)
    if bool(mat.trans_roughness_inv):
        trans_rough = 1.0 - trans_rough
    if bool(mat.trans_roughness_lock):
        trans_rough = float(mat.roughness)
        if bool(mat.roughness_inv):
            trans_rough = 1.0 - trans_rough

    params: dict[str, object] = {
        "alpha": max(trans_rough * trans_rough, 1e-4),
        "int_ior": float(mat.trans_ior),
        "ext_ior": 1.0,
    }

    depth = float(mat.trans_depth)
    if depth > 0.0:
        params["__sigma_t"] = sigma_t_from_transmission(
            _color(mat.trans_color), depth, ctx.scene_scale_to_meters
        )
    else:
        # trans_depth = 0 means "no absorption at any distance" in Max, so trans_color
        # tints nothing. Say so rather than quietly producing clear glass.
        tint = _color(mat.trans_color)
        if tint != (1.0, 1.0, 1.0):
            ctx.warn(name, "trans_color is set but trans_depth is 0, so Max applies no "
                           "absorption; the glass was exported untinted")

    if float(mat.transparency) < 1.0:
        ctx.warn(name, f"transparency {float(mat.transparency):g} is partial; Mitsuba's "
                       "roughdielectric is fully transmissive and the value was ignored")
    if float(mat.dispersion) != 0.0:
        ctx.warn(name, "dispersion is not supported and was ignored")

    return Material(
        id=mat_id,
        name=name,
        kind="rough_dielectric",
        params=params,
        normal_map=_texture(ctx, mat, "bump_map", name),
        two_sided=False,   # a dielectric must know which side of the interface it is on
        emission=_emission(ctx, mat, name),
    )


def _emission(ctx: MaterialContext, mat, name: str) -> Emission | None:
    """`emission` x `emit_color` x `emit_luminance` → radiometric `Emission`.

    Max authors emission as luminance in cd/m^2; Mitsuba's area emitter wants radiance in
    W/(sr*m^2). The conversion is `L_e = L_v / eta` with the same luminous efficacy used for
    photometric lights, and the original luminance is kept on the IR node so the UI can show
    where the number came from.
    """
    weight = float(mat.emission)
    color = _color(mat.emit_color)
    if weight <= 0.0 or color == (0.0, 0.0, 0.0):
        return None

    luminance_cd = float(mat.emit_luminance) * weight
    radiance = luminance_to_radiance(luminance_cd, ctx.luminous_efficacy)

    if _slot(mat, "emit_color_map") is not None:
        ctx.warn(name, "a mapped emission colour is not supported; the constant "
                       "emit_color was used instead")
    if bool(getattr(mat, "emit_kelvin", 0.0)) and float(mat.emit_kelvin) != 6500.0:
        ctx.warn(name, f"emit_kelvin {float(mat.emit_kelvin):g}K is not converted to RGB; "
                       "emit_color was used as authored")

    return Emission(
        radiance_rgb=(radiance * color[0], radiance * color[1], radiance * color[2]),
        source_luminance_cd_m2=luminance_cd,
        efficacy_lm_per_w=ctx.luminous_efficacy,
    )


def _warn_unsupported_features(ctx: MaterialContext, mat, name: str) -> None:
    """One warning per feature Max has and this build does not. Never silent."""
    if float(mat.scattering) != 0.0:
        ctx.warn(name, "subsurface scattering (scattering / sss_*) is not supported in v1")
    if float(mat.thin_film) != 0.0:
        ctx.warn(name, "thin film interference is not supported in v1")
    if float(mat.diff_roughness) != 0.0:
        ctx.warn(name, "diffuse roughness (Oren-Nayar) has no principled equivalent "
                       "and was ignored")
    if _slot(mat, "displacement_map") is not None:
        ctx.warn(name, "displacement mapping is not supported in v1; the map was ignored")
    if _slot(mat, "cutout_map") is not None:
        ctx.warn(name, "cutout / opacity mapping is not supported in v1; the map was "
                       "ignored and the surface is fully opaque")
    if _slot(mat, "coat_bump_map") is not None:
        ctx.warn(name, "a separate coating bump map is not supported and was ignored")
    if not bool(mat.brdf_mode):
        ctx.warn(name, "the material uses the legacy BRDF mode, which is not modelled; "
                       "the metalness/roughness parameters were used as-is")


# --------------------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------------------


def placeholder(mat, ctx: MaterialContext, node_name: str) -> Material:
    """The documented substitute for anything unsupported: 50% grey, plus a warning."""
    cls = "None" if mat is None else str(rt.classOf(mat))
    name = node_name if mat is None else str(mat.name)
    handle = 0 if mat is None else int(rt.getHandleByAnim(mat))
    ctx.warn(node_name,
             f"material {name!r} is a {cls}, which v1 does not support; "
             "a 50% grey diffuse placeholder was substituted")
    return Material(
        id=f"mat_placeholder_{handle}",
        name=name,
        kind="diffuse_placeholder",
        params={"reflectance": (0.5, 0.5, 0.5)},
    )


def translate_material(mat, ctx: MaterialContext, node_name: str) -> Material:
    """Dispatch on `classOf`, memoised on the material's Max handle.

    Memoising matters for more than speed: two nodes sharing a material must reference the
    same IR id, or the exported scene carries two BSDFs where Max had one and the warnings
    list repeats itself once per node.
    """
    if mat is None:
        return placeholder(None, ctx, node_name)

    handle = int(rt.getHandleByAnim(mat))
    cached = ctx._by_handle.get(handle)
    if cached is not None:
        return cached

    handler = MATERIALS.lookup(str(rt.classOf(mat)))
    result = placeholder(mat, ctx, node_name) if handler is None else handler(mat, ctx)
    ctx._by_handle[handle] = result
    return result
