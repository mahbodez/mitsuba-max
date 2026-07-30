"""Scene extraction: Max nodes → a complete `core.ir.Scene` plus written assets.

`SceneSource` is a protocol rather than a base class so that the USD-backed source in the
post-v1 plan, and the JSON fixtures the golden tests already use, are interchangeable with
`PymxsSource` without either knowing about the other. The exporter below talks only to the
protocol.

The one rule this module enforces above all others: **an unsupported feature never aborts
the export**. A node with an Arnold material becomes grey with a warning; a light class
nobody has written yet is skipped with a warning; a mesh that fails to snapshot is dropped
with a warning. The user gets an image and a list of exactly what was substituted. Failing
the whole export because one of four hundred nodes is odd is the behaviour that makes
people stop using a renderer.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from pymxs import runtime as rt

from core import transform as tf
from core.assets import AssetStore
from core.ir import Camera, Environment, Light, Material, Mesh, RenderSettings, Scene, Warning_
from core.meshbuild import build_groups, write_ply
from core.units import scene_scale_from_decode_value
from max_side import materials as materials_mod
from max_side import mesh as mesh_mod
from max_side.camera import CameraContext, aspect_preserving_resolution, resolve_camera
from max_side.lights import LightContext, translate_light
from max_side.materials import MaterialContext
from max_side.settings import Settings

__all__ = ["ExportResult", "PymxsSource", "SceneSource", "export_scene", "scene_scale"]


@runtime_checkable
class SceneSource(Protocol):
    """Anything that can enumerate a scene's renderable content."""

    def geometry_nodes(self) -> list[object]: ...
    def light_nodes(self) -> list[object]: ...
    def camera_node(self) -> object | None: ...
    def scale_to_meters(self) -> float: ...
    def render_resolution(self) -> tuple[int, int]: ...


def scene_scale() -> float:
    """Metres per system unit, via `rt.units.decodeValue("1.0m")`.

    `rt.units.getMasterScale` does not exist in Max 2027 (probe 01b), and a
    `SystemType`-name lookup table would need extending for every unit Max supports and
    would break silently on generic units. `decodeValue` parses a string carrying an
    explicit unit, so it is correct for centimetres, inches, feet or anything custom.
    """
    return scene_scale_from_decode_value(float(rt.units.decodeValue("1.0m")))


@dataclass
class PymxsSource:
    """The live 3ds Max scene.

    `selection_only` exists because on a heavy scene the fastest way to iterate on one
    material is to render one object, and probe 06c measured face extraction at 0.12 M
    faces/s — 2M triangles is roughly 20 s, which is a long time to wait to look at a sphere.
    """

    selection_only: bool = False

    def _roots(self) -> list[object]:
        return list(rt.selection) if self.selection_only else list(rt.objects)

    def geometry_nodes(self) -> list[object]:
        out: list[object] = []
        for node in self._roots():
            if str(rt.superClassOf(node)) != "GeometryClass":
                continue
            if bool(node.isHidden):
                continue
            # Targets are GeometryClass in Max's taxonomy but are not renderable geometry.
            if str(rt.classOf(node)) == "Targetobject":
                continue
            out.append(node)
        return out

    def light_nodes(self) -> list[object]:
        return [n for n in self._roots()
                if str(rt.superClassOf(n)) == "light" and not bool(n.isHidden)]

    def camera_node(self) -> object | None:
        return rt.getActiveCamera()

    def scale_to_meters(self) -> float:
        return scene_scale()

    def render_resolution(self) -> tuple[int, int]:
        return (int(rt.renderWidth), int(rt.renderHeight))


@dataclass
class ExportResult:
    scene: Scene
    root: Path
    elapsed_s: float
    triangles: int = 0
    assets_written: int = 0
    assets_reused: int = 0
    stats: dict[str, float] = field(default_factory=dict)

    @property
    def warnings(self) -> tuple[Warning_, ...]:
        return self.scene.warnings


def export_scene(source: SceneSource, root: Path, settings: Settings,
                 *, environment: Environment | None = None) -> ExportResult:
    """Walk the scene and produce a `Scene` plus every file it references.

    Ordering is deliberate: materials are translated lazily, as each mesh asks for one, and
    memoised on the material's Max handle. Two nodes sharing a material therefore reference
    one IR material and produce one warning between them, not one each.
    """
    started = time.perf_counter()
    root.mkdir(parents=True, exist_ok=True)
    assets = AssetStore(root=root)

    scale = source.scale_to_meters()
    width, height = aspect_preserving_resolution(*source.render_resolution(),
                                                 settings.resolution_scale)

    mat_ctx = MaterialContext(assets=assets, scene_scale_to_meters=scale,
                              luminous_efficacy=settings.luminous_efficacy)
    light_ctx = LightContext(scene_scale_to_meters=scale,
                             luminous_efficacy=settings.luminous_efficacy)
    cam_ctx = CameraContext(scene_scale_to_meters=scale, width=width, height=height)

    meshes: list[Mesh] = []
    materials: dict[str, Material] = {}
    triangles = 0

    geometry_started = time.perf_counter()
    for node in source.geometry_nodes():
        node_name = str(node.name)
        try:
            raw = mesh_mod.extract_raw_mesh(node)
            mirrored = mesh_mod.node_is_mirrored(node)
        except Exception as exc:  # noqa: BLE001 - one bad node must not kill the export
            mat_ctx.warn(node_name, f"could not be converted to a mesh: {exc}",
                         category="geometry")
            continue

        if raw.face_count == 0:
            continue

        material = materials_mod.translate_material(node.material, mat_ctx, node_name)
        materials[material.id] = material

        groups = build_groups(raw, scale_to_meters=scale, reverse_winding=mirrored)
        if len(groups) > 1:
            # Not an error, but worth surfacing: v1 resolves every face material id of a
            # node to the same Material, so a Multi/Sub-Object assignment silently loses its
            # per-face variety even though the geometry splits correctly.
            mat_ctx.warn(node_name,
                         f"the mesh has {len(groups)} face material ids; v1 applies one "
                         "material to all of them (Multi/Sub-Object is post-v1)",
                         category="material")

        for group in groups:
            blob = write_ply(group)
            rel = assets.add_bytes(blob, subdir="meshes", ext=".ply",
                                   source=f"{node_name} matid {group.material_id}")
            triangles += group.triangle_count
            meshes.append(Mesh(
                id=f"{int(rt.getHandleByAnim(node))}_{group.material_id}",
                name=node_name,
                material_id=material.id,
                positions_path=rel,
                # v1 bakes vertices in world space, so the node transform is already
                # applied and the shape carries the basis change alone. The object-space
                # path (C·T·C⁻¹) arrives with instancing in M5.
                to_world=tf.IDENTITY,
                # NOT `mirrored`. `build_groups(reverse_winding=...)` has already
                # reversed the triples and negated the normals, so the PLY is correct;
                # setting the renderer flag on top would undo it. Measured against
                # Mitsuba 3.9: `flip_normals` inverts the emitting side of an area
                # emitter even on an unmirrored shape.
                flip_normals=False,
            ))
    geometry_elapsed = time.perf_counter() - geometry_started

    lights: list[Light] = []
    for node in source.light_nodes():
        translated = translate_light(node, light_ctx)
        if translated is not None:
            lights.append(translated)

    camera: Camera = resolve_camera(cam_ctx)

    scene = Scene(
        camera=camera,
        settings=RenderSettings(
            integrator=settings.integrator,          # type: ignore[arg-type]
            max_depth=settings.max_depth,
            rr_depth=settings.rr_depth,
            spp_per_pass=settings.spp_per_pass,
            passes=settings.passes,
        ),
        meshes=tuple(meshes),
        materials=tuple(materials.values()),
        lights=tuple(lights),
        environment=environment,
        scene_scale_to_meters=scale,
        warnings=tuple(mat_ctx.warnings) + tuple(light_ctx.warnings)
        + tuple(cam_ctx.warnings),
    )

    assets.write_manifest()
    elapsed = time.perf_counter() - started
    return ExportResult(
        scene=scene,
        root=root,
        elapsed_s=elapsed,
        triangles=triangles,
        assets_written=assets.written,
        assets_reused=assets.reused,
        stats={
            "geometry_s": geometry_elapsed,
            "triangles_per_s": triangles / geometry_elapsed if geometry_elapsed else 0.0,
        },
    )
