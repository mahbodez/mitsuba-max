"""IR → the nested dict that Mitsuba's `mi.load_dict` consumes.

This is one of two backends over the same IR; `core.emit_xml` is the other, and a test
asserts they describe the same scene. The dict path is what the worker renders; the XML
path is what the user exports for reproducibility and for filing bug reports against
Mitsuba itself.

`core` cannot import `mitsuba`, so a transform cannot be a `mi.ScalarTransform4f` here.
Transforms are emitted as tagged placeholder dicts under `TRANSFORM_KEY` and the worker
resolves them in one pass just before loading (`worker.resolve.resolve_transforms`). The
tag is explicit rather than "a list of 16 floats", because guessing which lists are
matrices is how you end up transforming an RGB triple.
"""

import math
from typing import Any, TypeAlias

from core import transform as tf
from core.ir import (
    Camera,
    Environment,
    Light,
    Mat4,
    Material,
    Mesh,
    ParamValue,
    RenderSettings,
    Rgb,
    Scene,
    TextureRef,
)

__all__ = [
    "TRANSFORM_KEY",
    "EmitError",
    "material_to_bsdf",
    "matrix",
    "rgb",
    "scene_to_dict",
    "texture_to_dict",
]

Dict: TypeAlias = dict[str, Any]

TRANSFORM_KEY = "__mitsuba_transform__"
"""Marker key identifying a placeholder that the worker turns into a `ScalarTransform4f`."""


class EmitError(Exception):
    """The IR describes something this backend cannot express."""


# --------------------------------------------------------------------------------------
# small value helpers
# --------------------------------------------------------------------------------------


def rgb(value: Rgb) -> Dict:
    return {"type": "rgb", "value": [float(value[0]), float(value[1]), float(value[2])]}


def matrix(m: Mat4) -> Dict:
    return {TRANSFORM_KEY: {"kind": "matrix", "matrix": [float(x) for x in m]}}


def look_at(origin: tuple[float, float, float], target: tuple[float, float, float],
            up: tuple[float, float, float]) -> Dict:
    return {
        TRANSFORM_KEY: {
            "kind": "look_at",
            "origin": [float(x) for x in origin],
            "target": [float(x) for x in target],
            "up": [float(x) for x in up],
        }
    }


def _uv_transform(t: TextureRef) -> Dict | None:
    """`uv_scale` / `uv_offset` as a `to_uv` transform, or None when both are identity."""
    su, sv = t.uv_scale
    ou, ov = t.uv_offset
    if (su, sv, ou, ov) == (1.0, 1.0, 0.0, 0.0):
        return None
    m = (
        su, 0.0, 0.0, ou,
        0.0, sv, 0.0, ov,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    return matrix(m)


# --------------------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------------------


def texture_to_dict(t: TextureRef) -> Dict:
    """A `Bitmaptexture` input as a Mitsuba `bitmap` texture.

    `raw=True` disables the sRGB decode. Non-colour data — roughness, metalness, normal,
    bump, anisotropy — must be raw; getting it wrong applies a gamma curve to a physical
    quantity and produces a result that is wrong everywhere with no obvious visual tell.

    Inversion is deliberately not expressible: Mitsuba has no arithmetic texture node, so
    a glossiness map that needs to become roughness has to be baked at export time by
    `max_side.materials`. Raising here is better than emitting the un-inverted map and
    letting the user discover it by rendering something shiny as matte.
    """
    if t.invert:
        raise EmitError(
            f"texture {t.path!r} is flagged invert=True, which Mitsuba cannot express; "
            "the exporter must bake the inversion into the asset before emitting"
        )
    d: Dict = {"type": "bitmap", "filename": t.path, "raw": t.raw}
    uv = _uv_transform(t)
    if uv is not None:
        d["to_uv"] = uv
    return d


def _param(value: ParamValue) -> Any:
    if isinstance(value, TextureRef):
        return texture_to_dict(value)
    if isinstance(value, tuple):
        return rgb(value)
    return value


# --------------------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------------------

_PRINCIPLED_PARAMS = frozenset(
    {
        "base_color",
        "roughness",
        "anisotropic",
        "metallic",
        "spec_trans",
        "specular",
        "spec_tint",
        "sheen",
        "sheen_tint",
        "flatness",
        "clearcoat",
        "clearcoat_gloss",
    }
)

_DIELECTRIC_PARAMS = frozenset({"alpha", "alpha_u", "alpha_v", "int_ior", "ext_ior",
                                "distribution", "specular_reflectance",
                                "specular_transmittance"})


def material_to_bsdf(mat: Material) -> Dict:
    """A `Material` as a Mitsuba BSDF, wrapped as `twosided { normalmap { ... } }`.

    Wrapping order matters and is confirmed by PROBE 08: `normalmap` must sit *inside*
    `twosided`, because `twosided` flips the shading frame for back-facing hits and the
    normal perturbation has to happen in the already-flipped frame. Inverted, the normal
    map is applied and then flipped out from under itself.

    Dielectrics are **not** wrapped in `twosided`. A transmissive BSDF needs to know which
    side of the interface a ray is on, and `twosided` destroys exactly that information —
    Mitsuba itself warns about this combination. `Material.two_sided` is therefore honoured
    only for opaque kinds.
    """
    # Keys prefixed `__` belong to the shape, not the BSDF: `__sigma_t` describes the
    # interior medium of a transmissive surface, which Mitsuba attaches to the shape rather
    # than to the material. They are carried on the Material because that is where Max
    # authors them, and filtered out here.
    params = {k: v for k, v in sorted(mat.params.items()) if not k.startswith("__")}

    match mat.kind:
        case "principled":
            inner: Dict = {"type": "principled"}
            for k, v in params.items():
                if k not in _PRINCIPLED_PARAMS:
                    raise EmitError(f"{mat.name}: unknown principled parameter {k!r}")
                inner[k] = _param(v)
        case "rough_dielectric":
            inner = {"type": "roughdielectric", "distribution": "ggx"}
            for k, v in params.items():
                if k not in _DIELECTRIC_PARAMS:
                    raise EmitError(f"{mat.name}: unknown roughdielectric parameter {k!r}")
                inner[k] = _param(v)
        case "diffuse_placeholder":
            # 50% gray. Always accompanied by a Warning_ naming the node; see SPEC.md §1.
            refl = mat.params.get("reflectance", (0.5, 0.5, 0.5))
            inner = {"type": "diffuse", "reflectance": _param(refl)}
        case _:
            raise EmitError(f"{mat.name}: unknown material kind {mat.kind!r}")

    node = inner
    if mat.normal_map is not None:
        node = {
            "type": "normalmap",
            "normalmap": texture_to_dict(mat.normal_map),
            "nested": node,
        }

    if mat.two_sided and mat.kind != "rough_dielectric":
        node = {"type": "twosided", "material": node}
    return node


# --------------------------------------------------------------------------------------
# shapes
# --------------------------------------------------------------------------------------


def _mesh_to_dict(mesh: Mesh, mat: Material) -> Dict:
    d: Dict = {
        "type": "ply",
        "filename": mesh.positions_path,
        "to_world": matrix(mesh.to_world),
        "flip_normals": mesh.flip_normals,
        "bsdf": material_to_bsdf(mat),
    }
    if mat.emission is not None:
        d["emitter"] = {"type": "area", "radiance": rgb(mat.emission.radiance_rgb)}
    if mat.kind == "rough_dielectric" and "__sigma_t" in mat.params:
        sigma = mat.params["__sigma_t"]
        if not isinstance(sigma, tuple):
            raise EmitError(f"{mat.name}: __sigma_t must be an RGB triple")
        d["interior"] = {
            "type": "homogeneous",
            "sigma_t": rgb(sigma),
            # Pure absorption: Max's trans_depth is a Beer-Lambert absorption distance and
            # carries no scattering information, so inventing an albedo would be a guess.
            "albedo": rgb((0.0, 0.0, 0.0)),
        }
    return d


# --------------------------------------------------------------------------------------
# lights
# --------------------------------------------------------------------------------------


def light_to_dict(light: Light) -> Dict:
    """A `Light` as a Mitsuba emitter.

    `Light.to_world` is already in Mitsuba space, and by IR convention the emission axis is
    its **+Z column** — that is what Mitsuba's `spot` and `directional` use as their local
    forward. Max points its lights down local −Z, and the flip happens in
    `max_side.lights`, once, where the Max convention is still in scope.
    """
    match light.kind:
        case "point":
            return {
                "type": "point",
                "to_world": matrix(light.to_world),
                "intensity": rgb(light.radiance_rgb),
            }
        case "spot":
            if light.cutoff_angle_deg is None or light.beam_width_deg is None:
                raise EmitError(f"{light.name}: spot light without cone angles")
            return {
                "type": "spot",
                "to_world": matrix(light.to_world),
                "intensity": rgb(light.radiance_rgb),
                "cutoff_angle": light.cutoff_angle_deg,
                "beam_width": light.beam_width_deg,
            }
        case "directional":
            return {
                "type": "directional",
                "to_world": matrix(light.to_world),
                "irradiance": rgb(light.radiance_rgb),
            }
        case "area":
            if light.area_size is None:
                raise EmitError(f"{light.name}: area light without an extent")
            # Mitsuba's `rectangle` spans [-1, 1] in local XY, so the half-extent is the
            # scale. Baking it into to_world keeps the IR's extent in metres, which is what
            # the radiance conversion in core.units needs it to be.
            w, h = light.area_size
            scaled = tf.multiply(light.to_world, tf.scale_matrix(w / 2.0, h / 2.0, 1.0))
            return {
                "type": "rectangle",
                "to_world": matrix(scaled),
                "emitter": {"type": "area", "radiance": rgb(light.radiance_rgb)},
            }
        case _:
            raise EmitError(f"{light.name}: unknown light kind {light.kind!r}")


def environment_to_dict(env: Environment) -> Dict:
    match env.kind:
        case "constant":
            return {"type": "constant", "radiance": rgb(env.radiance_rgb)}
        case "envmap":
            if env.texture is None:
                raise EmitError("envmap environment without a texture")
            d: Dict = {
                "type": "envmap",
                "filename": env.texture.path,
                "scale": env.scale,
            }
            if env.to_world is not None:
                d["to_world"] = matrix(env.to_world)
            return d
        case _:
            raise EmitError(f"unknown environment kind {env.kind!r}")


# --------------------------------------------------------------------------------------
# sensor and integrator
# --------------------------------------------------------------------------------------


def sensor_to_dict(cam: Camera, settings: RenderSettings, *, spp: int | None = None) -> Dict:
    """The camera as a `perspective` or `thinlens` sensor.

    The transform is emitted as a `look_at` triple rather than a raw matrix. Mitsuba's
    `look_at` puts the camera's **left** in the first basis column; handing it a matrix
    built from Max's basis gives a horizontally mirrored image, which on a symmetric scene
    is invisible until someone renders text. Going through `look_at` makes the convention
    explicit and survives a round-trip through the exported XML.
    """
    origin, target, up = tf.look_at_from_matrix(cam.to_world)
    d: Dict = {
        "type": "thinlens" if cam.aperture_radius_m is not None else "perspective",
        "fov": cam.fov_deg,
        "fov_axis": cam.fov_axis,
        "near_clip": cam.near_clip,
        "far_clip": cam.far_clip,
        "to_world": look_at(origin, target, up),
        "film": {
            "type": "hdrfilm",
            "width": cam.film_width,
            "height": cam.film_height,
            "pixel_format": "rgba",
            "component_format": "float32",
            "rfilter": {"type": "gaussian"},
        },
        "sampler": {
            "type": "independent",
            "sample_count": settings.spp_per_pass if spp is None else spp,
        },
    }
    ox, oy = cam.principal_point_offset
    if (ox, oy) != (0.0, 0.0):
        d["principal_point_offset_x"] = ox
        d["principal_point_offset_y"] = oy
    if cam.aperture_radius_m is not None:
        d["aperture_radius"] = cam.aperture_radius_m
        if cam.focus_distance_m is None:
            raise EmitError("thinlens sensor requires a focus distance")
        d["focus_distance"] = cam.focus_distance_m
    return d


def integrator_to_dict(settings: RenderSettings) -> Dict:
    return {
        "type": settings.integrator,
        "max_depth": settings.max_depth,
        "rr_depth": settings.rr_depth,
        "hide_emitters": settings.hide_emitters,
    }


# --------------------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------------------


def scene_to_dict(scene: Scene, *, spp: int | None = None) -> Dict:
    """The whole IR scene as a `mi.load_dict` argument.

    Keys are stable and derived from IR ids, so two exports of an unchanged scene produce
    byte-identical dicts. That is what makes the golden fixtures diffable.
    """
    out: Dict = {
        "type": "scene",
        "integrator": integrator_to_dict(scene.settings),
        "sensor": sensor_to_dict(scene.camera, scene.settings, spp=spp),
    }

    by_id = {m.id: m for m in scene.materials}
    for mesh in scene.meshes:
        mat = by_id.get(mesh.material_id)
        if mat is None:
            raise EmitError(f"mesh {mesh.name!r} references missing material "
                            f"{mesh.material_id!r}")
        key = f"shape_{mesh.id}"
        if key in out:
            raise EmitError(f"duplicate mesh id {mesh.id!r}")
        out[key] = _mesh_to_dict(mesh, mat)

    for light in scene.lights:
        key = f"emitter_{light.id}"
        if key in out:
            raise EmitError(f"duplicate light id {light.id!r}")
        out[key] = light_to_dict(light)

    if scene.environment is not None:
        out["environment"] = environment_to_dict(scene.environment)

    if not scene.lights and scene.environment is None and not _has_emissive(scene):
        raise EmitError(
            "scene has no lights, no environment and no emissive material; "
            "rendering it would produce a black image"
        )
    return out


def _has_emissive(scene: Scene) -> bool:
    return any(m.emission is not None for m in scene.materials)


def fov_from_focal_length(film_width_mm: float, focal_length_mm: float) -> float:
    """`fov_x = 2 atan(w / 2f)`, in degrees.

    Lives here rather than in `max_side` so the golden fixtures can be written in terms of
    a lens rather than an angle, and so the identity has a test.
    """
    if focal_length_mm <= 0.0:
        raise ValueError("focal length must be positive")
    return math.degrees(2.0 * math.atan(film_width_mm / (2.0 * focal_length_mm)))
