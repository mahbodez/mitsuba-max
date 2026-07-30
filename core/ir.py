"""The intermediate representation — the contract between Max and Mitsuba.

Frozen dataclasses, fully JSON-serialisable, containing no Max and no Mitsuba types.
`max_side` writes it by reading the scene; `core.emit_dict` / `core.emit_xml` read it to
produce a renderer scene. Because it is plain data, the entire translation and emission
path is testable from a checked-in JSON fixture with neither application present.

Invariants
----------
* Every geometric quantity is **already in Mitsuba space**: Y-up, metres. `max_side`
  applies `core.transform` and `core.units` before building any node here. Nothing
  downstream of the IR knows that Max is Z-up or that the scene was authored in
  centimetres.
* `Scene.from_json(scene.to_json()) == scene` exactly, for every scene. Golden fixtures
  depend on it.
* Floats are coerced on construction. `pymxs` hands back ints for round values and an
  `int` where a `float` is expected would break round-trip equality.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Any, Literal, TypeAlias

__all__ = [
    "Camera",
    "Emission",
    "Environment",
    "Light",
    "Mat4",
    "Material",
    "Mesh",
    "ParamValue",
    "PhotometricInfo",
    "RenderSettings",
    "Rgb",
    "Scene",
    "TextureRef",
    "Vec2",
    "Warning_",
]

Rgb: TypeAlias = tuple[float, float, float]
Vec2: TypeAlias = tuple[float, float]
Mat4: TypeAlias = tuple[float, ...]
"""16 floats, row-major, i.e. `m[row * 4 + col]`."""

IR_VERSION = 1


# --------------------------------------------------------------------------------------
# coercion helpers
# --------------------------------------------------------------------------------------


def _rgb(v: Iterable[float]) -> Rgb:
    seq = tuple(float(x) for x in v)
    if len(seq) != 3:
        raise ValueError(f"expected 3 components, got {len(seq)}")
    return (seq[0], seq[1], seq[2])


def _vec2(v: Iterable[float]) -> Vec2:
    seq = tuple(float(x) for x in v)
    if len(seq) != 2:
        raise ValueError(f"expected 2 components, got {len(seq)}")
    return (seq[0], seq[1])


def _mat4(v: Iterable[float]) -> Mat4:
    seq = tuple(float(x) for x in v)
    if len(seq) != 16:
        raise ValueError(f"expected a 16-element row-major matrix, got {len(seq)}")
    return seq


def _set(obj: object, name: str, value: object) -> None:
    """Assign to a field of a frozen dataclass from `__post_init__`."""
    object.__setattr__(obj, name, value)


# --------------------------------------------------------------------------------------
# leaves
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TextureRef:
    """A bitmap input to a material parameter.

    `raw` selects the colour-space treatment and is the single most damaging flag in the
    IR to get wrong: a roughness map decoded as sRGB is wrong everywhere with no obvious
    visual tell. True for all non-colour data (roughness, metalness, normal, bump,
    anisotropy), False for albedo and emission colour.
    """

    path: str
    raw: bool
    uv_scale: Vec2 = (1.0, 1.0)
    uv_offset: Vec2 = (0.0, 0.0)
    invert: bool = False

    def __post_init__(self) -> None:
        _set(self, "uv_scale", _vec2(self.uv_scale))
        _set(self, "uv_offset", _vec2(self.uv_offset))

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "raw": self.raw,
            "uv_scale": list(self.uv_scale),
            "uv_offset": list(self.uv_offset),
            "invert": self.invert,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "TextureRef":
        return TextureRef(
            path=str(d["path"]),
            raw=bool(d["raw"]),
            uv_scale=_vec2(d["uv_scale"]),
            uv_offset=_vec2(d["uv_offset"]),
            invert=bool(d["invert"]),
        )


ParamValue: TypeAlias = "float | bool | str | Rgb | TextureRef"
"""A material parameter: scalar, flag, enum string, RGB triple, or texture."""


def _param_to_json(v: ParamValue) -> Any:
    # bool first: `isinstance(True, int)` is True and a bool must not become 1.0.
    if isinstance(v, bool):
        return {"k": "bool", "v": v}
    if isinstance(v, float | int):
        return {"k": "float", "v": float(v)}
    if isinstance(v, str):
        return {"k": "str", "v": v}
    if isinstance(v, TextureRef):
        return {"k": "tex", "v": v.to_dict()}
    if isinstance(v, tuple):
        return {"k": "rgb", "v": list(_rgb(v))}
    raise TypeError(f"unsupported material parameter type: {type(v).__name__}")


def _param_from_json(d: dict[str, Any]) -> ParamValue:
    kind = d["k"]
    match kind:
        case "bool":
            return bool(d["v"])
        case "float":
            return float(d["v"])
        case "str":
            return str(d["v"])
        case "rgb":
            return _rgb(d["v"])
        case "tex":
            return TextureRef.from_dict(d["v"])
        case _:
            raise ValueError(f"unknown parameter kind {kind!r}")


@dataclass(frozen=True, slots=True)
class Emission:
    """Surface emission attached to a material, in radiometric units.

    `radiance_rgb` is W/(sr·m²). Max authors emission as luminance in cd/m²; the
    conversion lives in `core.units.luminance_to_radiance` and the original value is kept
    in `source_luminance_cd_m2` so the UI can explain the number it used.
    """

    radiance_rgb: Rgb
    source_luminance_cd_m2: float | None = None
    efficacy_lm_per_w: float | None = None

    def __post_init__(self) -> None:
        _set(self, "radiance_rgb", _rgb(self.radiance_rgb))

    def to_dict(self) -> dict[str, Any]:
        return {
            "radiance_rgb": list(self.radiance_rgb),
            "source_luminance_cd_m2": self.source_luminance_cd_m2,
            "efficacy_lm_per_w": self.efficacy_lm_per_w,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Emission":
        lum = d["source_luminance_cd_m2"]
        eff = d["efficacy_lm_per_w"]
        return Emission(
            radiance_rgb=_rgb(d["radiance_rgb"]),
            source_luminance_cd_m2=None if lum is None else float(lum),
            efficacy_lm_per_w=None if eff is None else float(eff),
        )


@dataclass(frozen=True, slots=True)
class PhotometricInfo:
    """Provenance for a photometric → radiometric light conversion.

    Kept so the UI can state *why* a light has the intensity it has. `I_e = I_v / η`
    where η is the luminous efficacy of radiation in lm/W (SPEC.md §6).
    """

    intensity_cd: float
    efficacy_lm_per_w: float
    max_light_type: str

    def __post_init__(self) -> None:
        _set(self, "intensity_cd", float(self.intensity_cd))
        _set(self, "efficacy_lm_per_w", float(self.efficacy_lm_per_w))

    def to_dict(self) -> dict[str, Any]:
        return {
            "intensity_cd": self.intensity_cd,
            "efficacy_lm_per_w": self.efficacy_lm_per_w,
            "max_light_type": self.max_light_type,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "PhotometricInfo":
        return PhotometricInfo(
            intensity_cd=float(d["intensity_cd"]),
            efficacy_lm_per_w=float(d["efficacy_lm_per_w"]),
            max_light_type=str(d["max_light_type"]),
        )


# --------------------------------------------------------------------------------------
# scene nodes
# --------------------------------------------------------------------------------------

MaterialKind: TypeAlias = Literal["principled", "rough_dielectric", "diffuse_placeholder"]


@dataclass(frozen=True, slots=True)
class Material:
    """A surface. `kind` selects the Mitsuba BSDF; `params` carries its inputs.

    `diffuse_placeholder` is what an unsupported Max material becomes: 50% gray, always
    accompanied by a `Warning_` naming the node and the class. Substituting silently is a
    defect (SPEC.md §1).
    """

    id: str
    name: str
    kind: MaterialKind
    params: dict[str, ParamValue]
    normal_map: TextureRef | None = None
    two_sided: bool = True
    emission: Emission | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "params": {k: _param_to_json(v) for k, v in self.params.items()},
            "normal_map": None if self.normal_map is None else self.normal_map.to_dict(),
            "two_sided": self.two_sided,
            "emission": None if self.emission is None else self.emission.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Material":
        nm = d["normal_map"]
        em = d["emission"]
        kind: MaterialKind = d["kind"]
        return Material(
            id=str(d["id"]),
            name=str(d["name"]),
            kind=kind,
            params={k: _param_from_json(v) for k, v in d["params"].items()},
            normal_map=None if nm is None else TextureRef.from_dict(nm),
            two_sided=bool(d["two_sided"]),
            emission=None if em is None else Emission.from_dict(em),
        )


@dataclass(frozen=True, slots=True)
class Mesh:
    """One triangulated group of faces sharing a single material.

    A Max node with several face material IDs becomes several `Mesh` entries: Mitsuba has
    no per-face material, so the split has to happen at export (SPEC.md §8.5).

    `positions_path` is relative to the export root and names a content-hashed binary PLY
    written by `max_side.mesh`. `to_world` is the basis change `C` alone in v1, because
    vertices are baked in world space; the object-space path arrives with instancing.
    """

    id: str
    name: str
    material_id: str
    positions_path: str
    to_world: Mat4
    flip_normals: bool = False

    def __post_init__(self) -> None:
        _set(self, "to_world", _mat4(self.to_world))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "material_id": self.material_id,
            "positions_path": self.positions_path,
            "to_world": list(self.to_world),
            "flip_normals": self.flip_normals,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Mesh":
        return Mesh(
            id=str(d["id"]),
            name=str(d["name"]),
            material_id=str(d["material_id"]),
            positions_path=str(d["positions_path"]),
            to_world=_mat4(d["to_world"]),
            flip_normals=bool(d["flip_normals"]),
        )


LightKind: TypeAlias = Literal["point", "spot", "directional", "area"]


@dataclass(frozen=True, slots=True)
class Light:
    """A non-geometric light source, already converted to radiometric units.

    `radiance_rgb` means radiant intensity in W/sr for `point` and `spot`, irradiance in
    W/m² for `directional`, and radiance in W/(sr·m²) for `area`. The unit differs by kind
    because the Mitsuba plugins differ; `photometric_source` records where the number came
    from so the UI can show the working.

    Cone angles are **half** angles in degrees — Max stores full angles and the halving
    happens in `core.units.spot_angles`.
    """

    id: str
    name: str
    kind: LightKind
    to_world: Mat4
    radiance_rgb: Rgb
    cutoff_angle_deg: float | None = None
    beam_width_deg: float | None = None
    area_size: Vec2 | None = None
    photometric_source: PhotometricInfo | None = None

    def __post_init__(self) -> None:
        _set(self, "to_world", _mat4(self.to_world))
        _set(self, "radiance_rgb", _rgb(self.radiance_rgb))
        if self.area_size is not None:
            _set(self, "area_size", _vec2(self.area_size))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "to_world": list(self.to_world),
            "radiance_rgb": list(self.radiance_rgb),
            "cutoff_angle_deg": self.cutoff_angle_deg,
            "beam_width_deg": self.beam_width_deg,
            "area_size": None if self.area_size is None else list(self.area_size),
            "photometric_source": (
                None if self.photometric_source is None else self.photometric_source.to_dict()
            ),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Light":
        cut = d["cutoff_angle_deg"]
        beam = d["beam_width_deg"]
        size = d["area_size"]
        ph = d["photometric_source"]
        kind: LightKind = d["kind"]
        return Light(
            id=str(d["id"]),
            name=str(d["name"]),
            kind=kind,
            to_world=_mat4(d["to_world"]),
            radiance_rgb=_rgb(d["radiance_rgb"]),
            cutoff_angle_deg=None if cut is None else float(cut),
            beam_width_deg=None if beam is None else float(beam),
            area_size=None if size is None else _vec2(size),
            photometric_source=None if ph is None else PhotometricInfo.from_dict(ph),
        )


FovAxis: TypeAlias = Literal["x", "y", "diagonal", "smaller", "larger"]


@dataclass(frozen=True, slots=True)
class Camera:
    """The sensor. `to_world` is already in Mitsuba space and metres.

    Emission prefers `look_at` over a raw matrix: Mitsuba's `look_at` puts the camera's
    **left** in the first basis column, so copying Max's basis directly yields a
    horizontally mirrored image (SPEC.md §5). `core.transform.look_at_from_matrix`
    decomposes `to_world` into the origin/target/up triple that avoids the trap.

    `aperture_radius_m` non-None selects `thinlens` over `perspective`.
    """

    to_world: Mat4
    fov_deg: float
    fov_axis: FovAxis = "x"
    near_clip: float = 1e-2
    far_clip: float = 1e4
    principal_point_offset: Vec2 = (0.0, 0.0)
    film_width: int = 1280
    film_height: int = 720
    aperture_radius_m: float | None = None
    focus_distance_m: float | None = None
    exposure_scale: float = 1.0

    def __post_init__(self) -> None:
        _set(self, "to_world", _mat4(self.to_world))
        _set(self, "principal_point_offset", _vec2(self.principal_point_offset))
        _set(self, "fov_deg", float(self.fov_deg))
        _set(self, "near_clip", float(self.near_clip))
        _set(self, "far_clip", float(self.far_clip))
        _set(self, "film_width", int(self.film_width))
        _set(self, "film_height", int(self.film_height))
        _set(self, "exposure_scale", float(self.exposure_scale))

    def to_dict(self) -> dict[str, Any]:
        return {
            "to_world": list(self.to_world),
            "fov_deg": self.fov_deg,
            "fov_axis": self.fov_axis,
            "near_clip": self.near_clip,
            "far_clip": self.far_clip,
            "principal_point_offset": list(self.principal_point_offset),
            "film_width": self.film_width,
            "film_height": self.film_height,
            "aperture_radius_m": self.aperture_radius_m,
            "focus_distance_m": self.focus_distance_m,
            "exposure_scale": self.exposure_scale,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Camera":
        ar = d["aperture_radius_m"]
        fd = d["focus_distance_m"]
        axis: FovAxis = d["fov_axis"]
        return Camera(
            to_world=_mat4(d["to_world"]),
            fov_deg=float(d["fov_deg"]),
            fov_axis=axis,
            near_clip=float(d["near_clip"]),
            far_clip=float(d["far_clip"]),
            principal_point_offset=_vec2(d["principal_point_offset"]),
            film_width=int(d["film_width"]),
            film_height=int(d["film_height"]),
            aperture_radius_m=None if ar is None else float(ar),
            focus_distance_m=None if fd is None else float(fd),
            exposure_scale=float(d["exposure_scale"]),
        )


EnvKind: TypeAlias = Literal["envmap", "constant"]


@dataclass(frozen=True, slots=True)
class Environment:
    """World lighting. `constant` is uniform radiance; `envmap` needs `texture`.

    The white-furnace golden scene uses `constant` with radiance 1.0 and is the cheapest
    single number that catches energy, unit and `twosided` bugs at once.
    """

    kind: EnvKind
    radiance_rgb: Rgb = (1.0, 1.0, 1.0)
    texture: TextureRef | None = None
    scale: float = 1.0
    to_world: Mat4 | None = None

    def __post_init__(self) -> None:
        _set(self, "radiance_rgb", _rgb(self.radiance_rgb))
        _set(self, "scale", float(self.scale))
        if self.to_world is not None:
            _set(self, "to_world", _mat4(self.to_world))
        if self.kind == "envmap" and self.texture is None:
            raise ValueError("Environment(kind='envmap') requires a texture")

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "radiance_rgb": list(self.radiance_rgb),
            "texture": None if self.texture is None else self.texture.to_dict(),
            "scale": self.scale,
            "to_world": None if self.to_world is None else list(self.to_world),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Environment":
        tex = d["texture"]
        tw = d["to_world"]
        kind: EnvKind = d["kind"]
        return Environment(
            kind=kind,
            radiance_rgb=_rgb(d["radiance_rgb"]),
            texture=None if tex is None else TextureRef.from_dict(tex),
            scale=float(d["scale"]),
            to_world=None if tw is None else _mat4(tw),
        )


IntegratorKind: TypeAlias = Literal["path", "volpath", "direct"]


@dataclass(frozen=True, slots=True)
class RenderSettings:
    """Sampling and integration budget.

    The sample budget is split into `passes` of `spp_per_pass` because `mi.render()`
    cannot be interrupted; a pending cancel is checked between passes, so worst-case
    cancel latency is one pass (SPEC.md §10).
    """

    integrator: IntegratorKind = "path"
    max_depth: int = 8
    rr_depth: int = 5
    spp_per_pass: int = 16
    passes: int = 32
    seed: int = 0
    hide_emitters: bool = False

    def __post_init__(self) -> None:
        for f in ("max_depth", "rr_depth", "spp_per_pass", "passes", "seed"):
            _set(self, f, int(getattr(self, f)))
        if self.spp_per_pass < 1 or self.passes < 1:
            raise ValueError("spp_per_pass and passes must both be >= 1")

    @property
    def total_spp(self) -> int:
        return self.spp_per_pass * self.passes

    def to_dict(self) -> dict[str, Any]:
        return {
            "integrator": self.integrator,
            "max_depth": self.max_depth,
            "rr_depth": self.rr_depth,
            "spp_per_pass": self.spp_per_pass,
            "passes": self.passes,
            "seed": self.seed,
            "hide_emitters": self.hide_emitters,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "RenderSettings":
        integrator: IntegratorKind = d["integrator"]
        return RenderSettings(
            integrator=integrator,
            max_depth=int(d["max_depth"]),
            rr_depth=int(d["rr_depth"]),
            spp_per_pass=int(d["spp_per_pass"]),
            passes=int(d["passes"]),
            seed=int(d["seed"]),
            hide_emitters=bool(d["hide_emitters"]),
        )


@dataclass(frozen=True, slots=True)
class Warning_:
    """One thing the exporter could not translate faithfully.

    Every substitution produces one of these, naming the node and the reason. The UI shows
    the list; an empty list is the only clean export. Named with a trailing underscore to
    avoid shadowing the builtin.
    """

    node: str
    reason: str
    category: str = "unsupported"

    def to_dict(self) -> dict[str, Any]:
        return {"node": self.node, "reason": self.reason, "category": self.category}

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Warning_":
        return Warning_(
            node=str(d["node"]), reason=str(d["reason"]), category=str(d["category"])
        )


# --------------------------------------------------------------------------------------
# root
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scene:
    """The complete exported scene.

    `scene_scale_to_meters` is recorded rather than applied twice: geometry and light
    positions are already in metres by the time they reach the IR, but the factor is shown
    in the UI because a scene authored in centimetres and treated as metres is wrong by
    10⁴ in irradiance and the user needs to be able to see which one they got.
    """

    camera: Camera
    settings: RenderSettings = RenderSettings()
    meshes: tuple[Mesh, ...] = ()
    materials: tuple[Material, ...] = ()
    lights: tuple[Light, ...] = ()
    environment: Environment | None = None
    scene_scale_to_meters: float = 1.0
    warnings: tuple[Warning_, ...] = ()

    def __post_init__(self) -> None:
        _set(self, "scene_scale_to_meters", float(self.scene_scale_to_meters))
        _set(self, "meshes", tuple(self.meshes))
        _set(self, "materials", tuple(self.materials))
        _set(self, "lights", tuple(self.lights))
        _set(self, "warnings", tuple(self.warnings))

    # -- lookup ------------------------------------------------------------------------

    def material(self, material_id: str) -> Material:
        for m in self.materials:
            if m.id == material_id:
                return m
        raise KeyError(f"no material with id {material_id!r}")

    def with_warnings(self, *extra: Warning_) -> "Scene":
        return replace(self, warnings=self.warnings + extra)

    # -- serialisation -----------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "ir_version": IR_VERSION,
            "camera": self.camera.to_dict(),
            "settings": self.settings.to_dict(),
            "meshes": [m.to_dict() for m in self.meshes],
            "materials": [m.to_dict() for m in self.materials],
            "lights": [light.to_dict() for light in self.lights],
            "environment": None if self.environment is None else self.environment.to_dict(),
            "scene_scale_to_meters": self.scene_scale_to_meters,
            "warnings": [w.to_dict() for w in self.warnings],
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Scene":
        version = int(d.get("ir_version", IR_VERSION))
        if version != IR_VERSION:
            raise ValueError(
                f"IR version mismatch: file is {version}, code expects {IR_VERSION}"
            )
        env = d["environment"]
        return Scene(
            camera=Camera.from_dict(d["camera"]),
            settings=RenderSettings.from_dict(d["settings"]),
            meshes=tuple(Mesh.from_dict(x) for x in d["meshes"]),
            materials=tuple(Material.from_dict(x) for x in d["materials"]),
            lights=tuple(Light.from_dict(x) for x in d["lights"]),
            environment=None if env is None else Environment.from_dict(env),
            scene_scale_to_meters=float(d["scene_scale_to_meters"]),
            warnings=tuple(Warning_.from_dict(x) for x in d["warnings"]),
        )

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Scene":
        obj: dict[str, Any] = json.loads(text)
        return Scene.from_dict(obj)
