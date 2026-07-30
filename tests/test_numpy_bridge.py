"""The numpy bridge, tested without Max.

The bridge itself is stdlib-only, so everything except "does it really find the worker
venv's numpy" is testable here. The part that is not — arming it against a real Max
interpreter that ships no numpy — is a manual check.
"""

import importlib
import sys

import pytest

from max_side import numpy_bridge


@pytest.fixture(autouse=True)
def _restore_meta_path():
    saved = list(sys.meta_path)
    yield
    sys.meta_path[:] = saved


def _blockers() -> list[object]:
    return [f for f in sys.meta_path if type(f).__name__ == "_MitsubaBlocker"]


def test_install_blocker_is_idempotent() -> None:
    numpy_bridge.install_blocker()
    numpy_bridge.install_blocker()
    assert len(_blockers()) == 1


def test_blocker_survives_a_devreload_without_stacking() -> None:
    """`devreload.purge()` drops the module, so the live finder is an instance of the old
    class. Identity must be checked by name or every reload adds another finder."""
    numpy_bridge.install_blocker()
    del sys.modules["max_side.numpy_bridge"]
    fresh = importlib.import_module("max_side.numpy_bridge")
    fresh.install_blocker()
    assert len(_blockers()) == 1


@pytest.mark.parametrize("name", ["mitsuba", "drjit", "mitsuba.scalar_rgb"])
def test_blocker_refuses_the_renderer(name: str) -> None:
    numpy_bridge.install_blocker()
    with pytest.raises(ImportError, match="forbidden"):
        importlib.import_module(name)


def test_candidates_include_the_managed_venv() -> None:
    candidates = numpy_bridge._candidate_site_packages(None)
    assert candidates
    assert all(c.name == "site-packages" for c in candidates)
    assert any("mitsuba-max" in str(c) for c in candidates)


def test_an_explicit_interpreter_is_tried_first() -> None:
    from pathlib import Path

    explicit = Path(r"C:\somewhere\venv\Scripts\python.exe")
    first = numpy_bridge._candidate_site_packages(explicit)[0]
    assert first == Path(r"C:\somewhere\venv\Lib\site-packages")
