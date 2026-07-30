"""Make numpy importable inside 3ds Max, without letting Mitsuba in with it.

Probe 06c established that Max 2027 ships no numpy: its `site-packages` holds exactly
PySide6, pymxs, qtmax and shiboken6. Most of this project copes — `core.transform`,
`core.units`, `core.meshbuild` and both emitters are stdlib-only precisely so the export
path always works. What genuinely needs numpy is the pixel path: tone-mapping a megapixel
float32 buffer for display, several times a second, is not something a Python loop can do.

The numpy we use is the one already sitting in the worker venv. Because that venv is
created from Max's own `python.exe` (probe 12), its extension modules are built for exactly
this interpreter — same CPython, same MSVC runtime, same ABI. No second download, no
version skew, and nothing installed into Max's own directory.

The catch is that the same directory contains `mitsuba` and `drjit`, and importing either
inside Max is hard invariant 1 — Dr.Jit loads its own native DLLs and LLVM/CUDA backends
into a process that already has TBB, Qt, Arnold's LLVM and OpenImageIO. So this module does
**not** put that directory on `sys.path`. It installs a finder that serves `numpy` and
nothing else, and a blocker that raises on `mitsuba` and `drjit` however they are reached.

The blocker is the point. Without it, "we only added numpy" is a convention that survives
until someone writes `import mitsuba` in a debug session and takes the user's unsaved scene
with them.
"""

import importlib.abc
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

__all__ = ["BLOCKED", "SERVED", "NumpyUnavailable", "ensure_numpy", "install_blocker"]

SERVED = ("numpy",)
"""Top-level packages this bridge is willing to resolve out of the worker venv."""

BLOCKED = ("mitsuba", "drjit")
"""Hard invariant 1. Never importable inside Max, by any route."""


class NumpyUnavailable(RuntimeError):
    """No usable numpy. The caller must degrade visibly, not silently."""


class _MitsubaBlocker(importlib.abc.MetaPathFinder):
    """Refuses `mitsuba` and `drjit` with an explanation rather than an ImportError.

    Sits at the very front of `sys.meta_path`, so it wins against any path entry, `.pth`
    file or future edit to this module.
    """

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition(".")[0]
        if root in BLOCKED:
            raise ImportError(
                f"importing {fullname!r} inside 3ds Max is forbidden. Dr.Jit loads its own "
                "native DLLs and LLVM/CUDA backends into a process that already has TBB, "
                "Qt, Arnold's LLVM and OpenImageIO. The renderer runs in a separate "
                "process; talk to it through max_side.client."
            )
        return None


class _VendorFinder(importlib.abc.MetaPathFinder):
    """Resolves only `SERVED` packages, and only from one directory."""

    def __init__(self, site_packages: Path) -> None:
        self._dir = str(site_packages)

    def find_spec(self, fullname, path=None, target=None):
        root = fullname.partition(".")[0]
        if root not in SERVED:
            return None
        # Delegate to the ordinary machinery, restricted to our one directory. Submodules
        # arrive with `path` already set by the parent package, so only the top level needs
        # redirecting.
        search = [self._dir] if fullname == root else path
        return importlib.machinery.PathFinder.find_spec(fullname, search, target)


def install_blocker() -> None:
    """Install the mitsuba/drjit blocker. Idempotent, and safe to call from a reload.

    Identity is checked by class *name*, not `isinstance`: `devreload.purge()` drops this
    module from `sys.modules`, so the blocker already on `sys.meta_path` is an instance of
    the previous incarnation of the class and fails `isinstance` against the new one. Left
    that way, every reload stacks another finder in front of the import system.
    """
    if not any(type(f).__name__ == "_MitsubaBlocker" for f in sys.meta_path):
        sys.meta_path.insert(0, _MitsubaBlocker())


def _candidate_site_packages(venv_python: Path | None) -> list[Path]:
    from max_side.env_setup import managed_venv_python

    out: list[Path] = []
    if venv_python is not None:
        out.append(venv_python.parent.parent / "Lib" / "site-packages")
    managed = managed_venv_python().parent.parent / "Lib" / "site-packages"
    if managed not in out:
        out.append(managed)
    return out


def ensure_numpy(venv_python: Path | None = None):
    """Return the numpy module, importing it through the bridge if necessary.

    Raises `NumpyUnavailable` with an actionable message rather than returning `None`.
    A caller that gets `None` writes `if np is None` once and forgets it in three other
    places; a caller that gets an exception has to decide what the UI should say.
    """
    install_blocker()

    if "numpy" in sys.modules:
        return sys.modules["numpy"]
    try:
        import numpy
        return numpy
    except ImportError:
        pass

    tried: list[str] = []
    for site_packages in _candidate_site_packages(venv_python):
        if not (site_packages / "numpy" / "__init__.py").is_file():
            tried.append(f"{site_packages} (no numpy there)")
            continue
        finder = _VendorFinder(site_packages)
        sys.meta_path.insert(1, finder)   # after the blocker, before everything else
        try:
            import numpy
            return numpy
        except Exception as exc:  # noqa: BLE001
            sys.meta_path.remove(finder)
            # An ABI mismatch surfaces here, and its message is the only useful diagnostic
            # the user will get, so it is carried through verbatim.
            tried.append(f"{site_packages}: {type(exc).__name__}: {exc}")

    raise NumpyUnavailable(
        "3ds Max does not ship numpy and none could be borrowed from the worker "
        "environment. Progressive display and image saving need it.\n"
        "Run the environment wizard to create the worker venv, which installs a numpy "
        "built for this exact interpreter.\nTried:\n  " + "\n  ".join(tried)
    )
