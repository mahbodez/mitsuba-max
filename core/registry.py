"""Decorator-based translator registry, keyed on 3ds Max class name.

`max_side` translators declare which Max classes they handle; the exporter looks a node's
`classOf` up here and dispatches. The registry lives in `core` rather than `max_side` for
one reason: it lets the tests enumerate what the build claims to support without importing
`pymxs`, so "is Multi/Sub-Object registered?" is answerable in CI.

Keys are the string form of `rt.classOf(node)` — `"PhysicalMaterial"`, `"Physical"`,
`"Free_Light"`. Matching is exact and case-insensitive. There is deliberately no fuzzy or
prefix matching: a near-miss that silently picks the wrong translator is exactly the class
of bug the probe discipline exists to prevent. An unregistered class is not an error, it is
a `None` that the caller turns into a placeholder plus a warning naming the node.
"""

from collections.abc import Callable
from typing import TypeAlias

__all__ = ["Registry", "camera", "light", "material", "texture"]


class Registry[T]:
    """A name → handler map with a decorator interface.

    Registering the same Max class twice raises. Two translators claiming the same class
    means one of them will never run, and silently keeping the last one registered makes
    that depend on import order.
    """

    __slots__ = ("_by_class", "kind")

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._by_class: dict[str, T] = {}

    def register(self, *max_classes: str) -> Callable[[T], T]:
        if not max_classes:
            raise ValueError(f"{self.kind} registration needs at least one Max class name")

        def deco(handler: T) -> T:
            for name in max_classes:
                key = name.lower()
                if key in self._by_class:
                    existing = getattr(self._by_class[key], "__name__", self._by_class[key])
                    raise ValueError(
                        f"{self.kind} class {name!r} is already handled by {existing!r}"
                    )
                self._by_class[key] = handler
            return handler

        return deco

    def lookup(self, max_class: str) -> T | None:
        """The handler for a Max class, or `None` if this build does not support it."""
        return self._by_class.get(max_class.lower())

    def supported(self) -> tuple[str, ...]:
        return tuple(sorted(self._by_class))

    def __contains__(self, max_class: str) -> bool:
        return max_class.lower() in self._by_class

    def __len__(self) -> int:
        return len(self._by_class)


# Handlers are `(node, context) -> IR node`; the exact signatures live in `max_side`, which
# is the only package that has a Max node to pass. Typing them as object-returning
# callables here keeps `core` free of any Max vocabulary.
Handler: TypeAlias = Callable[..., object]

MATERIALS: Registry[Handler] = Registry("material")
LIGHTS: Registry[Handler] = Registry("light")
CAMERAS: Registry[Handler] = Registry("camera")
TEXTURES: Registry[Handler] = Registry("texture")

material = MATERIALS.register
light = LIGHTS.register
camera = CAMERAS.register
texture = TEXTURES.register
