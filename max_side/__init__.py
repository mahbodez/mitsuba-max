"""3ds Max side of mitsuba-max: the startup shim and the render entry point.

Deliberately thin, and deliberately stateless. `devreload.purge()` drops this module from
`sys.modules` and re-imports it, so anything held here at module scope would survive as an
object the reloaded code no longer recognises. All real logic lives in the sibling modules,
which is what makes the reload loop work at all.

Nothing here imports `mitsuba` or `drjit`. `install_blocker()` runs on import to make that
mechanical rather than aspirational — see `max_side.numpy_bridge`.
"""

import tempfile
import uuid
from pathlib import Path

from max_side.numpy_bridge import install_blocker

install_blocker()

__all__ = ["PROJECT_ROOT", "export_only", "reload_plugin", "render", "setup_environment"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _export_root() -> Path:
    from max_side.settings import load

    configured = load().export_root
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "mitsuba-max" / "export"


def export_only(*, selection_only: bool = False):
    """Extract the scene to IR and write its assets, without rendering.

    Useful on its own — it is how you inspect the warnings list, diff a `scene.xml`, or
    produce a golden fixture from a real scene — and it is the first half of `render`.
    """
    from core.registry import LIGHTS
    from max_side import lights as lights_mod
    from max_side.settings import load
    from max_side.source import PymxsSource, export_scene

    # Side-effect import: decorator registrations live in lights/materials/camera modules.
    # Print the guard stamp so a Max session still running a pre-03d `lights.py` is obvious.
    print(f"[mitsuba-max] light guard={lights_mod.LIGHT_GUARD!r} "
          f"registry={list(LIGHTS.supported())}")

    settings = load()
    root = _export_root()
    result = export_scene(PymxsSource(selection_only=selection_only), root, settings)
    print(f"[mitsuba-max] exported {len(result.scene.meshes)} shapes, "
          f"{len(result.scene.lights)} lights, "
          f"{result.triangles} triangles in {result.elapsed_s:.2f}s "
          f"({result.assets_written} assets written, {result.assets_reused} reused), "
          f"{len(result.warnings)} warnings")
    for warning in result.warnings:
        print(f"[mitsuba-max] warning: [{warning.category}] {warning.node}: "
              f"{warning.reason}")
    return result


def render(*, selection_only: bool = False):
    """Export, reuse the session worker (starting it if needed), and open the render window.

    The window is retained in `max_side.ui` so the menu macroscript — which discards the
    return value of `python.execute` — does not let CPython collect a live Qt dialog.
    """
    from max_side import env_setup
    from max_side.numpy_bridge import ensure_numpy
    from max_side.settings import load, save

    settings = load()
    if not settings.interpreter:
        report = setup_environment()
        if report is None or not report.ok:
            return None
        settings = load()

    # Max ships no numpy (probe 06c), and `core.film`, `core.tonemap` and `max_side.exr`
    # need it. Arm the bridge *before* importing anything that pulls them in, or the import
    # fails with a bare ModuleNotFoundError instead of the wizard instructions below.
    ensure_numpy(Path(settings.interpreter) if settings.interpreter else None)

    from max_side.client import shared_worker
    from max_side.ui import RenderWindow, retain

    result = export_only(selection_only=selection_only)

    env_setup.write_project_pth(settings.interpreter, PROJECT_ROOT)
    client = shared_worker(settings.interpreter, PROJECT_ROOT, variant=settings.variant)

    # retain() is load-bearing for the menu button: see max_side.ui._active.
    window = retain(RenderWindow(client, settings))
    window.show()
    film = result.root / "film" / f"{uuid.uuid4().hex}.film"
    window.start_render(result.scene, result.root, film)
    save(settings)
    return window


def setup_environment(base_interpreter: str | None = None):
    """Create and validate the managed worker venv, then remember it.

    Prints the subprocess's real stderr on failure. Never a sanitised message: corporate
    networks block PyPI, and the user needs to be able to see that.
    """
    from max_side import env_setup
    from max_side.settings import load, save

    print("[mitsuba-max] creating the worker environment; this downloads ~200 MB")
    created = env_setup.create_managed_venv(base_interpreter)
    if not created.ok:
        print(created.detail())
        return created

    report = env_setup.install_packages(created.interpreter)
    if not report.ok:
        print(report.detail())
        return report

    env_setup.write_project_pth(report.interpreter, PROJECT_ROOT)
    settings = load()
    settings.interpreter = report.interpreter
    settings.mitsuba_version = report.mitsuba_version
    save(settings)
    print(f"[mitsuba-max] ready: mitsuba {report.mitsuba_version}, "
          f"numpy {report.numpy_version}")
    return report


def reload_plugin():
    """Purge and re-import every project module. See `max_side.devreload`."""
    from max_side.devreload import reload

    return reload()
