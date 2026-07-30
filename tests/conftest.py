"""Shared fixtures. Scene builders live here so every test describes one behaviour."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import transform as tf  # noqa: E402
from core.ir import (  # noqa: E402
    Camera,
    Emission,
    Environment,
    Light,
    Material,
    Mesh,
    RenderSettings,
    Scene,
    TextureRef,
    Warning_,
)

GOLDEN_DIR = ROOT / "tests" / "golden"


@pytest.fixture
def golden_dir() -> Path:
    return GOLDEN_DIR


@pytest.fixture
def simple_camera() -> Camera:
    return Camera(
        to_world=tf.look_at_matrix((0.0, 1.0, 5.0), (0.0, 1.0, 0.0), (0.0, 1.0, 0.0)),
        fov_deg=39.6,
        fov_axis="x",
        film_width=64,
        film_height=48,
    )


@pytest.fixture
def kitchen_sink_scene(simple_camera: Camera) -> Scene:
    """A scene exercising every IR feature at once.

    Deliberately not physically sensible — its job is to make the round-trip and emission
    tests touch every branch, including the ones (area lights, media, normal maps) that a
    realistic minimal scene would skip.
    """
    tex = TextureRef(path="textures/abc123.png", raw=False, uv_scale=(2.0, 3.0),
                     uv_offset=(0.1, -0.2))
    normal = TextureRef(path="textures/def456.png", raw=True)

    principled = Material(
        id="mat_metal",
        name="Brushed Steel",
        kind="principled",
        params={
            "base_color": tex,
            "roughness": 0.35,
            "metallic": 1.0,
            "specular": 0.5,
            "anisotropic": 0.4,
            "clearcoat": 0.2,
            "clearcoat_gloss": 0.8,
        },
        normal_map=normal,
        two_sided=True,
    )
    glass = Material(
        id="mat_glass",
        name="Tinted Glass",
        kind="rough_dielectric",
        params={
            "alpha": 0.02,
            "int_ior": 1.52,
            "ext_ior": 1.0,
            "__sigma_t": (0.6, 0.2, 0.1),
        },
        two_sided=True,
    )
    placeholder = Material(
        id="mat_missing",
        name="Some Arnold Thing",
        kind="diffuse_placeholder",
        params={"reflectance": (0.5, 0.5, 0.5)},
    )
    emissive = Material(
        id="mat_emit",
        name="Panel",
        kind="principled",
        params={"base_color": (0.0, 0.0, 0.0), "roughness": 1.0},
        emission=Emission(radiance_rgb=(6.0, 6.0, 5.4), source_luminance_cd_m2=1500.0,
                          efficacy_lm_per_w=250.0),
    )

    return Scene(
        camera=simple_camera,
        settings=RenderSettings(integrator="volpath", max_depth=12, spp_per_pass=4, passes=2),
        meshes=(
            Mesh(id="m0", name="Sphere001", material_id="mat_metal",
                 positions_path="meshes/aaaa.ply", to_world=tf.BASIS_MAX_TO_MITSUBA),
            Mesh(id="m1", name="Glass001", material_id="mat_glass",
                 positions_path="meshes/bbbb.ply", to_world=tf.BASIS_MAX_TO_MITSUBA,
                 flip_normals=True),
            Mesh(id="m2", name="Weird001", material_id="mat_missing",
                 positions_path="meshes/cccc.ply", to_world=tf.BASIS_MAX_TO_MITSUBA),
            Mesh(id="m3", name="Panel001", material_id="mat_emit",
                 positions_path="meshes/dddd.ply", to_world=tf.BASIS_MAX_TO_MITSUBA),
        ),
        materials=(principled, glass, placeholder, emissive),
        lights=(
            Light(id="l0", name="Omni001", kind="point",
                  to_world=tf.compose_trs((0.0, 3.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
                  radiance_rgb=(4.0, 4.0, 4.0)),
            Light(id="l1", name="Spot001", kind="spot",
                  to_world=tf.look_at_matrix((2.0, 4.0, 2.0), (0.0, 0.0, 0.0),
                                             (0.0, 1.0, 0.0)),
                  radiance_rgb=(10.0, 9.0, 8.0),
                  cutoff_angle_deg=30.0, beam_width_deg=22.5),
            Light(id="l2", name="Sun001", kind="directional",
                  to_world=tf.look_at_matrix((0.0, 10.0, 0.0), (0.0, 0.0, 0.0),
                                             (0.0, 0.0, 1.0)),
                  radiance_rgb=(3.0, 3.0, 2.8)),
            Light(id="l3", name="Area001", kind="area",
                  to_world=tf.compose_trs((0.0, 2.0, -2.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)),
                  radiance_rgb=(2.0, 2.0, 2.0), area_size=(1.5, 0.75)),
        ),
        environment=Environment(kind="constant", radiance_rgb=(0.2, 0.25, 0.35)),
        scene_scale_to_meters=0.01,
        warnings=(
            Warning_(node="Weird001", reason="unsupported material class ai_standard_surface",
                     category="material"),
            Warning_(node="Camera001", reason="vertical_tilt_correction is non-zero",
                     category="camera"),
        ),
    )


@pytest.fixture
def furnace_scene() -> Scene:
    """White furnace: constant environment of radiance 1, one white Lambertian sphere.

    A correct renderer produces exactly 1.0 everywhere and the sphere vanishes. Any
    deviation is an energy, unit or `twosided` bug reduced to a single number.
    """
    return Scene(
        camera=Camera(
            to_world=tf.look_at_matrix((0.0, 0.0, 4.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            fov_deg=45.0,
            film_width=32,
            film_height=32,
        ),
        settings=RenderSettings(integrator="path", max_depth=64, rr_depth=64,
                                spp_per_pass=64, passes=1),
        meshes=(
            Mesh(id="sphere", name="FurnaceSphere", material_id="white",
                 positions_path="meshes/unit_sphere.ply", to_world=tf.IDENTITY),
        ),
        materials=(
            Material(id="white", name="White", kind="diffuse_placeholder",
                     params={"reflectance": (1.0, 1.0, 1.0)}, two_sided=True),
        ),
        environment=Environment(kind="constant", radiance_rgb=(1.0, 1.0, 1.0)),
    )
