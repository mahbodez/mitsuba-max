"""Turn the renderer-agnostic scene dict into one `mi.load_dict` will accept.

`core.emit_dict` cannot build a `mi.ScalarTransform4f` — it must not import Mitsuba — so
it emits transforms as tagged placeholders. This module is the other half of that contract
and is the only place in the project that translates between the two representations.

It also rebases relative asset paths. The IR stores `meshes/<hash>.ply` so that fixtures
are portable and hashes stay stable regardless of where an export landed; the renderer
needs a path it can open.
"""

from pathlib import Path
from typing import Any

import mitsuba as mi

from core.emit_dict import TRANSFORM_KEY

__all__ = ["ResolveError", "resolve"]

_PATH_KEYS = frozenset({"filename"})


class ResolveError(Exception):
    """A placeholder this module does not understand — i.e. the two halves disagree."""


def _transform(spec: dict[str, Any]) -> "mi.ScalarTransform4f":
    kind = spec.get("kind")
    if kind == "matrix":
        m = spec["matrix"]
        if len(m) != 16:
            raise ResolveError(f"transform matrix has {len(m)} entries, expected 16")
        return mi.ScalarTransform4f([[float(m[r * 4 + c]) for c in range(4)]
                                     for r in range(4)])
    if kind == "look_at":
        return mi.ScalarTransform4f().look_at(
            origin=[float(v) for v in spec["origin"]],
            target=[float(v) for v in spec["target"]],
            up=[float(v) for v in spec["up"]],
        )
    raise ResolveError(f"unknown transform kind {kind!r}")


def resolve(node: Any, scene_root: Path | None = None) -> Any:
    """Recursively rewrite a scene dict in place-safe fashion, returning a new structure.

    Two rewrites happen:

    * `{TRANSFORM_KEY: {...}}` becomes a real `ScalarTransform4f`.
    * a relative `filename` becomes absolute against `scene_root`.

    Anything else is copied through untouched. Copying rather than mutating matters because
    the host sends one scene dict per job and the same dict may be rendered twice with
    different sample counts.
    """
    if isinstance(node, dict):
        if TRANSFORM_KEY in node:
            if len(node) != 1:
                raise ResolveError(
                    f"transform placeholder carries extra keys: {sorted(node)}"
                )
            return _transform(node[TRANSFORM_KEY])
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _PATH_KEYS and isinstance(value, str) and scene_root is not None:
                path = Path(value)
                out[key] = str(path if path.is_absolute() else (scene_root / path))
            else:
                out[key] = resolve(value, scene_root)
        return out
    if isinstance(node, list):
        return [resolve(v, scene_root) for v in node]
    return node
