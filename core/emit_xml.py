"""IR → Mitsuba XML, for `Save scene.xml`.

Deliberately implemented as a translation of the *dict* backend rather than a second walk
over the IR. Two independent walks would drift, and "the XML I exported does not match what
you rendered" is an expensive kind of bug to chase — especially since the whole point of
the XML export is reproducibility and filing upstream bug reports.

So `core.emit_dict` remains the single description of the scene, and this module is a
mechanical dict → XML transcription. The only knowledge it adds is which XML *tag* each
plugin type belongs under, since XML separates plugins by category (`<bsdf>`, `<emitter>`,
`<shape>`, …) where `load_dict` infers it from the `type` string.
"""

import xml.etree.ElementTree as ET
from typing import Any
from xml.dom import minidom

from core.emit_dict import TRANSFORM_KEY, scene_to_dict
from core.ir import Scene

__all__ = ["XmlError", "scene_to_xml"]

MITSUBA_VERSION = "3.0.0"


class XmlError(Exception):
    """A plugin type with no known XML category, i.e. this module is out of date."""


_TAG_BY_TYPE: dict[str, str] = {
    # integrators
    "path": "integrator",
    "volpath": "integrator",
    "direct": "integrator",
    # sensors
    "perspective": "sensor",
    "thinlens": "sensor",
    # film / sampler / reconstruction filter
    "hdrfilm": "film",
    "independent": "sampler",
    "gaussian": "rfilter",
    "box": "rfilter",
    "tent": "rfilter",
    # shapes
    "ply": "shape",
    "obj": "shape",
    "serialized": "shape",
    "rectangle": "shape",
    "sphere": "shape",
    "cube": "shape",
    # bsdfs
    "principled": "bsdf",
    "diffuse": "bsdf",
    "twosided": "bsdf",
    "normalmap": "bsdf",
    "roughdielectric": "bsdf",
    "dielectric": "bsdf",
    "conductor": "bsdf",
    "roughconductor": "bsdf",
    # emitters
    "point": "emitter",
    "spot": "emitter",
    "directional": "emitter",
    "area": "emitter",
    "constant": "emitter",
    "envmap": "emitter",
    # textures and media
    "bitmap": "texture",
    "checkerboard": "texture",
    "homogeneous": "medium",
}

_SCENE_UNNAMED = frozenset({"integrator", "sensor"})


def _fmt(x: float) -> str:
    """Round-trippable float formatting. `repr` keeps full precision without `1e-05`
    surprises in the middle of a matrix."""
    return repr(float(x))


def _add_transform(parent: ET.Element, name: str, spec: dict[str, Any]) -> None:
    node = ET.SubElement(parent, "transform", {"name": name})
    match spec["kind"]:
        case "matrix":
            ET.SubElement(node, "matrix",
                          {"value": " ".join(_fmt(v) for v in spec["matrix"])})
        case "look_at":
            ET.SubElement(node, "lookat", {
                "origin": ", ".join(_fmt(v) for v in spec["origin"]),
                "target": ", ".join(_fmt(v) for v in spec["target"]),
                "up": ", ".join(_fmt(v) for v in spec["up"]),
            })
        case other:
            raise XmlError(f"unknown transform kind {other!r}")


def _add_value(parent: ET.Element, name: str, value: Any) -> None:
    if isinstance(value, bool):
        ET.SubElement(parent, "boolean", {"name": name, "value": "true" if value else "false"})
    elif isinstance(value, int):
        ET.SubElement(parent, "integer", {"name": name, "value": str(value)})
    elif isinstance(value, float):
        ET.SubElement(parent, "float", {"name": name, "value": _fmt(value)})
    elif isinstance(value, str):
        ET.SubElement(parent, "string", {"name": name, "value": value})
    elif isinstance(value, dict):
        if TRANSFORM_KEY in value:
            _add_transform(parent, name, value[TRANSFORM_KEY])
        elif value.get("type") == "rgb":
            ET.SubElement(parent, "rgb", {
                "name": name,
                "value": ", ".join(_fmt(v) for v in value["value"]),
            })
        else:
            _add_plugin(parent, value, name=name)
    else:
        raise XmlError(f"cannot serialise {name!r} of type {type(value).__name__}")


def _add_plugin(parent: ET.Element, spec: dict[str, Any], *, name: str | None = None,
                id_: str | None = None) -> ET.Element:
    ptype = spec.get("type")
    if not isinstance(ptype, str):
        raise XmlError(f"plugin dict has no 'type': {sorted(spec)}")
    tag = _TAG_BY_TYPE.get(ptype)
    if tag is None:
        raise XmlError(f"no XML category known for plugin type {ptype!r}")

    attrs = {"type": ptype}
    if name is not None:
        attrs["name"] = name
    if id_ is not None:
        attrs["id"] = id_
    node = ET.SubElement(parent, tag, attrs)

    for key, value in spec.items():
        if key == "type":
            continue
        _add_value(node, key, value)
    return node


def scene_to_xml(scene: Scene, *, spp: int | None = None) -> str:
    """The scene as pretty-printed Mitsuba XML.

    The result is loadable with `mi.load_file` and is what the user hands to a Mitsuba
    maintainer when something renders wrong — it depends on nothing from this project.
    """
    d = scene_to_dict(scene, spp=spp)
    root = ET.Element("scene", {"version": MITSUBA_VERSION})

    for key, value in d.items():
        if key == "type":
            continue
        if not isinstance(value, dict):
            raise XmlError(f"unexpected top-level entry {key!r}")
        if key in _SCENE_UNNAMED:
            _add_plugin(root, value)
        else:
            _add_plugin(root, value, id_=key)

    raw = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(raw).toprettyxml(indent="  ")
    # minidom emits its own <?xml ...?> line; keep it, drop the blank lines it also emits.
    return "\n".join(line for line in pretty.splitlines() if line.strip()) + "\n"
