"""The translator registry.

Lives in `core` so tests can enumerate what a build claims to support without importing
`pymxs` — "is Multi/Sub-Object registered?" has to be answerable in CI.
"""

import pytest

from core.registry import Registry


def test_lookup_is_case_insensitive() -> None:
    """`rt.classOf` spellings vary — `freeSpot` but `Free_Light`, `Physical` but
    `PhysicalMaterial`. Case sensitivity here would turn a capitalisation slip into a
    silently unsupported node."""
    reg: Registry[object] = Registry("material")

    @reg.register("PhysicalMaterial")
    def handler() -> None: ...

    assert reg.lookup("physicalmaterial") is handler
    assert reg.lookup("PHYSICALMATERIAL") is handler
    assert "PhysicalMaterial" in reg


def test_unregistered_class_returns_none() -> None:
    """Not an error: the caller turns it into a placeholder plus a warning naming the
    node, which is the documented behaviour for unsupported scene features."""
    reg: Registry[object] = Registry("material")
    assert reg.lookup("ai_standard_surface") is None


def test_one_handler_can_claim_several_classes() -> None:
    reg: Registry[object] = Registry("light")

    @reg.register("freeSpot", "targetSpot")
    def handler() -> None: ...

    assert reg.lookup("freespot") is handler
    assert reg.lookup("targetspot") is handler
    assert len(reg) == 2


def test_duplicate_registration_raises() -> None:
    """Two translators claiming one class means one never runs, and keeping the last
    registration silently makes which one depends on import order."""
    reg: Registry[object] = Registry("material")

    @reg.register("PhysicalMaterial")
    def first() -> None: ...

    with pytest.raises(ValueError, match="already handled by"):
        @reg.register("PhysicalMaterial")
        def second() -> None: ...


def test_identical_re_registration_is_idempotent() -> None:
    """Re-importing a translator module must not raise when the same handler comes back."""
    reg: Registry[object] = Registry("light")

    @reg.register("Free_Light")
    def handler() -> None: ...

    reg.register("Free_Light")(handler)
    assert reg.lookup("Free_Light") is handler

def test_registration_without_a_class_raises() -> None:
    reg: Registry[object] = Registry("camera")
    with pytest.raises(ValueError, match="at least one Max class"):
        reg.register()


def test_supported_is_sorted_and_lowercase() -> None:
    reg: Registry[object] = Registry("light")
    reg.register("Omnilight", "freeSpot")(lambda: None)
    assert reg.supported() == ("freespot", "omnilight")


def test_no_fuzzy_matching() -> None:
    """A near-miss that silently picks the wrong translator is exactly the class of bug the
    probe discipline exists to prevent."""
    reg: Registry[object] = Registry("material")
    reg.register("PhysicalMaterial")(lambda: None)
    assert reg.lookup("PhysicalMaterial2") is None
    assert reg.lookup("Physical") is None
