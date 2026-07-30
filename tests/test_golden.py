"""The half of M1 that needs no renderer: fixtures are stable and emit correctly.

The rendering half lives in `tools/run_golden.py`, which runs in the worker venv because
`mitsuba` is deliberately not installed here. Splitting them this way means the expensive
half is opt-in while the half that catches most regressions — a changed hash, a dropped
field, an emitter that stopped producing an emitter — runs on every `pytest`.
"""

import json
from pathlib import Path

import pytest

from core.emit_dict import scene_to_dict
from core.emit_xml import scene_to_xml
from core.ir import Scene

from .golden.scenes import SCENES, build_all

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Scene]:
    return build_all(tmp_path_factory.mktemp("golden"))


def test_all_four_scenes_exist(built: dict[str, Scene]) -> None:
    assert set(built) == {"chirality", "white_furnace", "transform_torture", "cornell_box"}


@pytest.mark.parametrize("name", sorted(SCENES))
def test_fixture_matches_the_checked_in_json(built: dict[str, Scene], name: str) -> None:
    """The generated IR must equal the committed fixture, byte for byte.

    This is a stronger check than it looks. The asset paths inside the JSON are content
    hashes of the PLY files, so a change to `write_ply`, to the geometry generators, or to
    any conversion that feeds them shows up here as a diff — with no renderer, no GPU and
    no reference image. Regenerate deliberately with `python -m tests.golden.regenerate`.
    """
    path = GOLDEN / f"{name}.json"
    assert path.is_file(), f"missing fixture {path}; run tests/golden/regenerate.py"
    assert json.loads(built[name].to_json()) == json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(SCENES))
def test_fixture_round_trips(name: str) -> None:
    text = (GOLDEN / f"{name}.json").read_text(encoding="utf-8")
    assert Scene.from_json(text).to_json() == text


@pytest.mark.parametrize("name", sorted(SCENES))
def test_fixture_emits_to_dict_and_xml(name: str) -> None:
    scene = Scene.from_json((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))
    d = scene_to_dict(scene)
    assert d["type"] == "scene"
    assert scene_to_xml(scene).startswith("<?xml")


# --------------------------------------------------------------------------------------
# scene-specific structural expectations
# --------------------------------------------------------------------------------------


def _scene(name: str) -> Scene:
    return Scene.from_json((GOLDEN / f"{name}.json").read_text(encoding="utf-8"))


def test_chirality_texture_is_not_raw() -> None:
    """The corner markers are colour data. Flagging them raw would skip the sRGB decode and
    shift every hue, which is precisely what the test then measures."""
    scene = _scene("chirality")
    tex = scene.material("marker").params["base_color"]
    assert tex.raw is False


def test_furnace_is_white_and_two_sided() -> None:
    scene = _scene("white_furnace")
    material = scene.material("white")
    assert material.params["reflectance"] == (1.0, 1.0, 1.0)
    assert material.two_sided
    assert scene.environment is not None
    assert scene.environment.radiance_rgb == (1.0, 1.0, 1.0)


def test_furnace_path_is_not_truncated() -> None:
    """Truncating the path loses the tail of the interreflection series and darkens the
    sphere by an amount that looks exactly like an energy bug."""
    scene = _scene("white_furnace")
    assert scene.settings.max_depth >= 64
    assert scene.settings.rr_depth >= 64


def test_torture_includes_a_mirrored_node() -> None:
    """One node must have a negative-determinant transform, or the winding case is untested.

    Note that it carries `flip_normals = False`. In this object-space path the mirror lives
    in `to_world`, the PLY holds explicit outward normals, and Mitsuba transforms those by
    the inverse transpose — which handles a negative determinant correctly by itself.
    Setting the flag renders the cube black, which is how that was established.
    """
    from core.transform import determinant3

    scene = _scene("transform_torture")
    mirrored = [m for m in scene.meshes if determinant3(m.to_world) < 0.0]
    assert [m.id for m in mirrored] == ["mirrored"]
    assert not any(m.flip_normals for m in scene.meshes)


def test_torture_control_node_is_at_the_origin() -> None:
    """`plain` is the control: it is the case where the wrong conjugation still looks
    right, so its presence is what makes the other three meaningful."""
    scene = _scene("transform_torture")
    plain = next(m for m in scene.meshes if m.id == "plain")
    assert plain.to_world[3] == pytest.approx(0.0)
    assert plain.to_world[7] == pytest.approx(0.0)
    assert plain.to_world[11] == pytest.approx(0.0)


def test_torture_uses_a_centimetre_scene() -> None:
    """Authored in centimetres so the metres conversion is under test, not assumed."""
    scene = _scene("transform_torture")
    assert scene.scene_scale_to_meters == pytest.approx(0.01)
    offset = next(m for m in scene.meshes if m.id == "offset_rotated")
    # 60 cm along Max's +X is 0.6 m along Mitsuba's +X.
    assert offset.to_world[3] == pytest.approx(0.6)


def test_torture_conjugation_moved_z_up_to_y_up() -> None:
    """The `offset_rotated` node sits 20 cm up in Max, which is +0.2 m on Mitsuba's Y."""
    scene = _scene("transform_torture")
    offset = next(m for m in scene.meshes if m.id == "offset_rotated")
    assert offset.to_world[7] == pytest.approx(0.2)


def test_cornell_has_one_emitter_and_two_boxes() -> None:
    scene = _scene("cornell_box")
    emissive = [m for m in scene.materials if m.emission is not None]
    assert len(emissive) == 1
    assert emissive[0].emission is not None
    assert emissive[0].emission.radiance_rgb[0] == pytest.approx(18.387)
    assert {m.id for m in scene.meshes} >= {"small_box", "large_box", "light"}


def test_cornell_light_is_emitted_as_an_area_emitter() -> None:
    d = scene_to_dict(_scene("cornell_box"))
    assert d["shape_light"]["emitter"]["type"] == "area"
    assert "emitter" not in d["shape_floor"]


def test_cornell_uses_the_smaller_fov_axis() -> None:
    """Mitsuba's own Cornell box does, and on a square film the axes agree — so getting it
    wrong here is invisible until someone renders it non-square."""
    assert _scene("cornell_box").camera.fov_axis == "smaller"


def test_every_fixture_is_lit() -> None:
    """`scene_to_dict` refuses an unlit scene; asserting it here means the failure is
    attributed to the fixture rather than surfacing later as a black render."""
    for name in SCENES:
        scene_to_dict(_scene(name))
