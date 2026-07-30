"""Scene emission: the dict backend, the XML backend, and their agreement.

The two backends must describe the same scene. They are tested together rather than
separately because "the XML I exported does not match what you rendered" is the failure
this arrangement exists to prevent.
"""

import xml.etree.ElementTree as ET

import pytest

from core import transform as tf
from core.emit_dict import (
    TRANSFORM_KEY,
    EmitError,
    fov_from_focal_length,
    material_to_bsdf,
    scene_to_dict,
    texture_to_dict,
)
from core.emit_xml import XmlError, scene_to_xml
from core.ir import (
    Camera,
    Emission,
    Environment,
    Light,
    Material,
    Mesh,
    RenderSettings,
    Scene,
    TextureRef,
)

# --------------------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------------------


def test_raw_flag_is_passed_through() -> None:
    assert texture_to_dict(TextureRef(path="r.png", raw=True))["raw"] is True
    assert texture_to_dict(TextureRef(path="c.png", raw=False))["raw"] is False


def test_identity_uv_transform_is_omitted() -> None:
    """No `to_uv` for the common case, so the emitted scene stays readable."""
    assert "to_uv" not in texture_to_dict(TextureRef(path="t.png", raw=True))


def test_uv_transform_is_emitted_when_needed() -> None:
    d = texture_to_dict(TextureRef(path="t.png", raw=True, uv_scale=(2.0, 3.0),
                                   uv_offset=(0.25, 0.5)))
    m = d["to_uv"][TRANSFORM_KEY]["matrix"]
    assert m[0] == 2.0 and m[5] == 3.0
    assert m[3] == 0.25 and m[7] == 0.5


def test_invert_is_refused_loudly() -> None:
    """Mitsuba has no arithmetic texture node, so inversion must be baked at export.

    Emitting the un-inverted map instead would render a glossiness map as roughness — shiny
    becomes matte, everywhere, with nothing pointing at the cause.
    """
    with pytest.raises(EmitError, match="bake the inversion"):
        texture_to_dict(TextureRef(path="g.png", raw=True, invert=True))


# --------------------------------------------------------------------------------------
# materials
# --------------------------------------------------------------------------------------


def test_opaque_material_is_wrapped_twosided_outside_normalmap() -> None:
    """`twosided { normalmap { principled } }` — the order matters (PROBE 08)."""
    mat = Material(id="m", name="M", kind="principled", params={"roughness": 0.3},
                   normal_map=TextureRef(path="n.png", raw=True))
    bsdf = material_to_bsdf(mat)
    assert bsdf["type"] == "twosided"
    assert bsdf["material"]["type"] == "normalmap"
    assert bsdf["material"]["nested"]["type"] == "principled"


def test_dielectric_is_never_wrapped_twosided() -> None:
    """A transmissive BSDF needs to know which side of the interface a ray is on, and
    `twosided` destroys exactly that. Mitsuba warns about the combination."""
    mat = Material(id="g", name="Glass", kind="rough_dielectric",
                   params={"alpha": 0.01}, two_sided=True)
    assert material_to_bsdf(mat)["type"] == "roughdielectric"


def test_placeholder_is_fifty_percent_gray() -> None:
    mat = Material(id="p", name="P", kind="diffuse_placeholder", params={})
    bsdf = material_to_bsdf(mat)["material"]
    assert bsdf["type"] == "diffuse"
    assert bsdf["reflectance"]["value"] == [0.5, 0.5, 0.5]


def test_unknown_principled_parameter_is_refused() -> None:
    """A typo'd parameter is silently ignored by `load_dict`, so catch it here.

    `metalness` instead of `metallic` renders a plausible non-metal and nothing complains.
    """
    mat = Material(id="m", name="M", kind="principled", params={"metalness": 1.0})
    with pytest.raises(EmitError, match="unknown principled parameter"):
        material_to_bsdf(mat)


def test_material_without_normal_map_has_no_normalmap_node() -> None:
    mat = Material(id="m", name="M", kind="principled", params={})
    assert material_to_bsdf(mat)["material"]["type"] == "principled"


# --------------------------------------------------------------------------------------
# shapes, lights, environment
# --------------------------------------------------------------------------------------


def test_emissive_material_puts_an_area_emitter_on_the_shape(
    kitchen_sink_scene: Scene,
) -> None:
    d = scene_to_dict(kitchen_sink_scene)
    assert d["shape_m3"]["emitter"] == {"type": "area",
                                        "radiance": {"type": "rgb",
                                                     "value": [6.0, 6.0, 5.4]}}
    assert "emitter" not in d["shape_m0"]


def test_dielectric_shape_gets_an_interior_medium(kitchen_sink_scene: Scene) -> None:
    interior = scene_to_dict(kitchen_sink_scene)["shape_m1"]["interior"]
    assert interior["type"] == "homogeneous"
    assert interior["sigma_t"]["value"] == [0.6, 0.2, 0.1]
    assert interior["albedo"]["value"] == [0.0, 0.0, 0.0]


def test_flip_normals_reaches_the_shape(kitchen_sink_scene: Scene) -> None:
    d = scene_to_dict(kitchen_sink_scene)
    assert d["shape_m1"]["flip_normals"] is True
    assert d["shape_m0"]["flip_normals"] is False


def test_spot_cone_angles_are_emitted(kitchen_sink_scene: Scene) -> None:
    spot = scene_to_dict(kitchen_sink_scene)["emitter_l1"]
    assert spot["type"] == "spot"
    assert spot["cutoff_angle"] == 30.0
    assert spot["beam_width"] == 22.5


def test_spot_without_angles_is_refused() -> None:
    light = Light(id="l", name="Spot", kind="spot", to_world=tf.IDENTITY,
                  radiance_rgb=(1.0, 1.0, 1.0))
    scene = Scene(camera=_camera(), lights=(light,))
    with pytest.raises(EmitError, match="without cone angles"):
        scene_to_dict(scene)


def test_area_light_bakes_its_half_extent_into_the_transform(
    kitchen_sink_scene: Scene,
) -> None:
    """Mitsuba's `rectangle` spans [-1, 1] locally, so a 1.5 x 0.75 m panel scales by
    0.75 x 0.375. Emitting the full extent would double the light's area and its power."""
    rect = scene_to_dict(kitchen_sink_scene)["emitter_l3"]
    m = rect["to_world"][TRANSFORM_KEY]["matrix"]
    assert m[0] == pytest.approx(0.75)
    assert m[5] == pytest.approx(0.375)
    assert m[10] == pytest.approx(1.0)


def test_constant_environment(kitchen_sink_scene: Scene) -> None:
    env = scene_to_dict(kitchen_sink_scene)["environment"]
    assert env == {"type": "constant",
                   "radiance": {"type": "rgb", "value": [0.2, 0.25, 0.35]}}


def test_envmap_environment() -> None:
    scene = Scene(
        camera=_camera(),
        environment=Environment(kind="envmap",
                                texture=TextureRef(path="textures/sky.exr", raw=True),
                                scale=2.5),
    )
    env = scene_to_dict(scene)["environment"]
    assert env["type"] == "envmap"
    assert env["filename"] == "textures/sky.exr"
    assert env["scale"] == 2.5


# --------------------------------------------------------------------------------------
# sensor
# --------------------------------------------------------------------------------------


def test_sensor_emits_look_at_not_a_matrix(kitchen_sink_scene: Scene) -> None:
    """A `look_at` triple is immune to Mitsuba's left-handed first basis column."""
    sensor = scene_to_dict(kitchen_sink_scene)["sensor"]
    assert sensor["to_world"][TRANSFORM_KEY]["kind"] == "look_at"


def test_sensor_look_at_round_trips_to_the_original_matrix() -> None:
    to_world = tf.look_at_matrix((1.0, 2.0, 3.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    scene = Scene(camera=Camera(to_world=to_world, fov_deg=45.0),
                  lights=(_point_light(),))
    spec = scene_to_dict(scene)["sensor"]["to_world"][TRANSFORM_KEY]
    rebuilt = tf.look_at_matrix(tuple(spec["origin"]), tuple(spec["target"]),
                                tuple(spec["up"]))
    assert rebuilt == pytest.approx(to_world, abs=1e-9)


def test_perspective_by_default_thinlens_with_aperture() -> None:
    scene = Scene(camera=_camera(), lights=(_point_light(),))
    assert scene_to_dict(scene)["sensor"]["type"] == "perspective"

    dof = Scene(
        camera=Camera(to_world=_camera().to_world, fov_deg=45.0,
                      aperture_radius_m=0.003125, focus_distance_m=2.0),
        lights=(_point_light(),),
    )
    sensor = scene_to_dict(dof)["sensor"]
    assert sensor["type"] == "thinlens"
    assert sensor["aperture_radius"] == 0.003125
    assert sensor["focus_distance"] == 2.0


def test_thinlens_without_focus_distance_is_refused() -> None:
    scene = Scene(
        camera=Camera(to_world=_camera().to_world, fov_deg=45.0, aperture_radius_m=0.01),
        lights=(_point_light(),),
    )
    with pytest.raises(EmitError, match="focus distance"):
        scene_to_dict(scene)


def test_principal_point_offset_is_omitted_when_zero() -> None:
    scene = Scene(camera=_camera(), lights=(_point_light(),))
    assert "principal_point_offset_x" not in scene_to_dict(scene)["sensor"]


def test_sample_count_defaults_to_one_pass(kitchen_sink_scene: Scene) -> None:
    """The sensor's sampler holds *one pass* worth of samples, not the whole budget.

    The worker calls `mi.render(spp=...)` per pass and accumulates; baking the full budget
    into the sampler would make the first pass take as long as the entire render and defeat
    both progressive display and cancellation.
    """
    d = scene_to_dict(kitchen_sink_scene)
    assert d["sensor"]["sampler"]["sample_count"] == 4
    assert scene_to_dict(kitchen_sink_scene, spp=1)["sensor"]["sampler"]["sample_count"] == 1


def test_fov_from_focal_length() -> None:
    """36 mm film, 18 mm lens: exactly 90 degrees. Confirmed against Max in probe 01d."""
    assert fov_from_focal_length(36.0, 18.0) == pytest.approx(90.0)
    assert fov_from_focal_length(36.0, 50.0) == pytest.approx(39.5978, abs=1e-4)


# --------------------------------------------------------------------------------------
# scene-level validation
# --------------------------------------------------------------------------------------


def test_missing_material_reference_is_refused() -> None:
    scene = Scene(
        camera=_camera(),
        meshes=(Mesh(id="m", name="Box", material_id="nope",
                     positions_path="a.ply", to_world=tf.IDENTITY),),
        lights=(_point_light(),),
    )
    with pytest.raises(EmitError, match="missing material"):
        scene_to_dict(scene)


def test_unlit_scene_is_refused() -> None:
    """Rendering it would produce a black image and look like a crash."""
    with pytest.raises(EmitError, match="black image"):
        scene_to_dict(Scene(camera=_camera()))


def test_emissive_material_counts_as_lighting() -> None:
    scene = Scene(
        camera=_camera(),
        meshes=(Mesh(id="m", name="Panel", material_id="e", positions_path="a.ply",
                     to_world=tf.IDENTITY),),
        materials=(Material(id="e", name="E", kind="principled", params={},
                            emission=Emission(radiance_rgb=(5.0, 5.0, 5.0))),),
    )
    assert "shape_m" in scene_to_dict(scene)


def test_integrator_carries_the_settings() -> None:
    scene = Scene(camera=_camera(), lights=(_point_light(),),
                  settings=RenderSettings(integrator="volpath", max_depth=24, rr_depth=9))
    integrator = scene_to_dict(scene)["integrator"]
    assert integrator == {"type": "volpath", "max_depth": 24, "rr_depth": 9,
                          "hide_emitters": False}


def test_emission_is_deterministic(kitchen_sink_scene: Scene) -> None:
    """Byte-identical output for an unchanged scene is what makes fixtures diffable."""
    assert scene_to_dict(kitchen_sink_scene) == scene_to_dict(kitchen_sink_scene)
    assert scene_to_xml(kitchen_sink_scene) == scene_to_xml(kitchen_sink_scene)


# --------------------------------------------------------------------------------------
# XML backend
# --------------------------------------------------------------------------------------


def test_xml_is_well_formed_and_versioned(kitchen_sink_scene: Scene) -> None:
    root = ET.fromstring(scene_to_xml(kitchen_sink_scene))
    assert root.tag == "scene"
    assert root.attrib["version"] == "3.0.0"


def test_xml_categorises_plugins_correctly(kitchen_sink_scene: Scene) -> None:
    root = ET.fromstring(scene_to_xml(kitchen_sink_scene))
    tags = {child.tag for child in root}
    assert tags == {"integrator", "sensor", "shape", "emitter"}


def test_xml_and_dict_describe_the_same_scene(kitchen_sink_scene: Scene) -> None:
    """Every plugin in the dict appears in the XML with the same type, and vice versa.

    Compared as a multiset of type strings: structural drift between the backends shows up
    here rather than as a puzzled bug report about an exported file.
    """
    def types_in_dict(node: object) -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            if TRANSFORM_KEY in node:
                return found
            t = node.get("type")
            if isinstance(t, str) and t != "rgb":
                found.append(t)
            for v in node.values():
                found.extend(types_in_dict(v))
        return found

    root = ET.fromstring(scene_to_xml(kitchen_sink_scene))
    xml_types = [el.attrib["type"] for el in root.iter() if "type" in el.attrib]
    dict_types = types_in_dict(scene_to_dict(kitchen_sink_scene))
    dict_types.remove("scene")
    assert sorted(xml_types) == sorted(dict_types)


def test_xml_uses_lookat_for_the_sensor(kitchen_sink_scene: Scene) -> None:
    root = ET.fromstring(scene_to_xml(kitchen_sink_scene))
    sensor = root.find("sensor")
    assert sensor is not None
    lookat = sensor.find("transform/lookat")
    assert lookat is not None
    assert set(lookat.attrib) == {"origin", "target", "up"}


def test_xml_emits_typed_value_tags(kitchen_sink_scene: Scene) -> None:
    """Mitsuba's XML parser is type-directed: `<float>` and `<integer>` are not
    interchangeable, and a boolean written as `<integer value="1"/>` is rejected."""
    root = ET.fromstring(scene_to_xml(kitchen_sink_scene))
    film = root.find("sensor/film")
    assert film is not None
    assert film.find("integer[@name='width']") is not None
    assert film.find("string[@name='pixel_format']") is not None
    shape = root.find("shape")
    assert shape is not None
    assert shape.find("boolean[@name='flip_normals']") is not None


def test_xml_ids_come_from_the_ir(kitchen_sink_scene: Scene) -> None:
    root = ET.fromstring(scene_to_xml(kitchen_sink_scene))
    ids = {el.attrib["id"] for el in root if "id" in el.attrib}
    assert "shape_m0" in ids
    assert "emitter_l0" in ids


def test_xml_rejects_an_unknown_plugin_category() -> None:
    """The tag table is the one thing the XML backend knows that the dict backend does not,
    so an unmapped plugin must fail loudly rather than emit a guessed tag."""
    from core import emit_xml

    root = ET.Element("scene")
    with pytest.raises(XmlError, match="no XML category"):
        emit_xml._add_plugin(root, {"type": "sunsky"})


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _camera() -> Camera:
    return Camera(
        to_world=tf.look_at_matrix((0.0, 0.0, 5.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        fov_deg=45.0,
    )


def _point_light() -> Light:
    return Light(id="l", name="L", kind="point", to_world=tf.IDENTITY,
                 radiance_rgb=(1.0, 1.0, 1.0))
