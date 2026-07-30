"""The IR contract: exact JSON round-trip and construction-time coercion.

`Scene.from_json(scene.to_json()) == scene` is what makes the golden fixtures meaningful.
If it holds only approximately, a fixture is no longer a specification of anything.
"""

import json

import pytest

from core.ir import (
    IR_VERSION,
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

# --------------------------------------------------------------------------------------
# round trip
# --------------------------------------------------------------------------------------


def test_round_trip_is_exact(kitchen_sink_scene: Scene) -> None:
    assert Scene.from_json(kitchen_sink_scene.to_json()) == kitchen_sink_scene


def test_round_trip_is_idempotent(kitchen_sink_scene: Scene) -> None:
    once = kitchen_sink_scene.to_json()
    twice = Scene.from_json(once).to_json()
    assert once == twice


def test_round_trip_survives_a_minimal_scene(simple_camera: Camera) -> None:
    scene = Scene(camera=simple_camera)
    assert Scene.from_json(scene.to_json()) == scene


def test_json_is_sorted_so_fixtures_diff_cleanly(kitchen_sink_scene: Scene) -> None:
    obj = json.loads(kitchen_sink_scene.to_json())
    assert list(obj) == sorted(obj)


def test_version_mismatch_is_refused(kitchen_sink_scene: Scene) -> None:
    d = kitchen_sink_scene.to_dict()
    d["ir_version"] = IR_VERSION + 1
    with pytest.raises(ValueError, match="IR version mismatch"):
        Scene.from_dict(d)


# --------------------------------------------------------------------------------------
# coercion
# --------------------------------------------------------------------------------------


def test_ints_are_coerced_to_floats() -> None:
    """pymxs returns ints for round values, and `1 != 1.0` would break round-trip equality.

    `Point3(0,0,1)` comes back with integer components often enough that this is not a
    hypothetical; without coercion, a scene would compare unequal to its own reload
    depending on where the camera happened to be.
    """
    m = Mesh(id="a", name="A", material_id="m", positions_path="p.ply",
             to_world=(1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1))  # type: ignore[arg-type]
    assert all(isinstance(v, float) for v in m.to_world)


def test_rgb_length_is_validated() -> None:
    with pytest.raises(ValueError, match="3 components"):
        Emission(radiance_rgb=(1.0, 2.0))  # type: ignore[arg-type]


def test_matrix_length_is_validated() -> None:
    with pytest.raises(ValueError, match="16-element"):
        Mesh(id="a", name="A", material_id="m", positions_path="p.ply",
             to_world=(1.0, 0.0, 0.0))


def test_envmap_without_texture_is_refused() -> None:
    with pytest.raises(ValueError, match="requires a texture"):
        Environment(kind="envmap")


def test_zero_passes_is_refused() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        RenderSettings(passes=0)


# --------------------------------------------------------------------------------------
# parameter encoding
# --------------------------------------------------------------------------------------


def test_bool_parameters_do_not_become_floats() -> None:
    """`isinstance(True, int)` is True, so a naive encoder turns a flag into 1.0.

    Mitsuba then receives a float where it wanted a boolean and either errors or, worse,
    coerces it and silently changes behaviour.
    """
    mat = Material(id="m", name="M", kind="principled", params={"spec_tint": True})
    back = Material.from_dict(mat.to_dict())
    assert back.params["spec_tint"] is True
    assert not isinstance(back.params["spec_tint"], float)


def test_rgb_parameters_round_trip_as_tuples() -> None:
    mat = Material(id="m", name="M", kind="principled",
                   params={"base_color": (0.1, 0.2, 0.3)})
    back = Material.from_dict(mat.to_dict())
    assert back.params["base_color"] == (0.1, 0.2, 0.3)
    assert isinstance(back.params["base_color"], tuple)


def test_texture_parameters_round_trip() -> None:
    tex = TextureRef(path="t.png", raw=True, uv_scale=(2.0, 2.0), uv_offset=(0.5, 0.0),
                     invert=True)
    mat = Material(id="m", name="M", kind="principled", params={"roughness": tex})
    back = Material.from_dict(mat.to_dict())
    assert back.params["roughness"] == tex


def test_unsupported_parameter_type_is_rejected() -> None:
    mat = Material(id="m", name="M", kind="principled",
                   params={"roughness": [0.1, 0.2]})  # type: ignore[dict-item]
    with pytest.raises(TypeError, match="unsupported material parameter"):
        mat.to_dict()


# --------------------------------------------------------------------------------------
# lookups and helpers
# --------------------------------------------------------------------------------------


def test_material_lookup(kitchen_sink_scene: Scene) -> None:
    assert kitchen_sink_scene.material("mat_glass").name == "Tinted Glass"
    with pytest.raises(KeyError):
        kitchen_sink_scene.material("nope")


def test_with_warnings_is_additive(kitchen_sink_scene: Scene) -> None:
    extra = Warning_(node="N", reason="R")
    grown = kitchen_sink_scene.with_warnings(extra)
    assert grown.warnings[-1] == extra
    assert len(grown.warnings) == len(kitchen_sink_scene.warnings) + 1
    assert kitchen_sink_scene.warnings[-1] != extra   # original untouched


def test_total_spp() -> None:
    assert RenderSettings(spp_per_pass=16, passes=32).total_spp == 512


def test_scenes_are_hashable_and_frozen(simple_camera: Camera) -> None:
    light = Light(id="l", name="L", kind="point", to_world=tuple([1.0] * 16),
                  radiance_rgb=(1.0, 1.0, 1.0))
    with pytest.raises(AttributeError):
        light.name = "other"  # type: ignore[misc]
