"""`core` must import nothing from the host application or the renderer.

This is invariant 3 from the root CLAUDE.md, and it is the invariant that keeps the whole
translation layer testable. It is worth a test rather than a code review habit because the
failure mode is silent: an `import pymxs` inside a function body works fine on the
developer's machine, inside Max, and nowhere else.

The check runs in a subprocess with the forbidden modules replaced by import hooks that
raise, so it catches deferred imports inside function bodies too — not just module-level
ones a grep would find.
"""

import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

FORBIDDEN = ("pymxs", "mitsuba", "drjit", "PySide6", "shiboken6")

ROOT = Path(__file__).resolve().parent.parent


def _core_modules() -> list[str]:
    import core

    return [f"core.{m.name}" for m in pkgutil.iter_modules(core.__path__)]


def test_core_has_modules() -> None:
    mods = _core_modules()
    assert "core.ir" in mods
    assert "core.transform" in mods


@pytest.mark.parametrize("module", sorted(_core_modules()))
def test_core_module_imports_cleanly(module: str) -> None:
    script = f"""
import sys

class Blocker:
    def find_module(self, name, path=None):
        return self if name.split(".")[0] in {FORBIDDEN!r} else None
    def load_module(self, name):
        raise AssertionError("core must not import " + name)

sys.meta_path.insert(0, Blocker())
import {module}
print("ok")
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert proc.returncode == 0, f"{module} failed to import cleanly:\n{proc.stderr}"


def test_no_forbidden_names_in_core_source() -> None:
    """A cheap belt-and-braces grep, so the failure message names the file and line.

    The subprocess test above is the real check; this one exists because its failure
    message is a stack trace from a blocked import, and "materials.py:74 mentions pymxs" is
    a considerably more useful thing to read at 2am.
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "core").glob("*.py")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            for name in FORBIDDEN:
                if f" {name}" in f" {stripped}" and name in stripped.split():
                    offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert not offenders, "forbidden imports in core/:\n" + "\n".join(offenders)
