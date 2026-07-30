"""Developer reload: purge every project module from `sys.modules` and re-import.

Python caches modules, so an edit to `materials.py` has no effect on the next run inside a
long-lived Max session — which is every Max session. Without this, the development loop is
"restart Max", and a 45-second restart per one-line change is how a project stops getting
worked on.

For this to actually work, the startup shim must be trivial and stateless. Anything holding
long-lived state at module scope in `max_side/__init__.py` survives the purge in the form of
objects the reloaded code no longer recognises, and produces the worst kind of bug: one that
only appears after a reload and vanishes on restart.
"""

import importlib
import sys

__all__ = ["PACKAGES", "purge", "reload"]

PACKAGES = ("max_side", "core")
"""Purged on reload. `worker` is deliberately absent — it never runs in this process."""


def purge() -> list[str]:
    """Drop every project module from `sys.modules`. Returns what was removed.

    Deleting from `sys.modules` rather than calling `importlib.reload` on each module: reload
    re-executes a module in its existing namespace, so a renamed or deleted symbol lingers,
    and the order in which a dependency graph is reloaded changes the result. Deleting
    everything and importing afresh has one outcome.
    """
    removed = [
        name for name in list(sys.modules)
        if name in PACKAGES or any(name.startswith(f"{pkg}.") for pkg in PACKAGES)
    ]
    for name in removed:
        del sys.modules[name]
    return sorted(removed)


def reload():
    """Purge and re-import the plugin entry point. Returns the fresh `max_side` module.

    Closes any live `RenderWindow` first: a widget whose class object has been replaced
    still runs, but its `isinstance` checks against the new classes will not, and its
    keep-alive would otherwise pin the old module's objects across the purge.
    """
    ui = sys.modules.get("max_side.ui")
    if ui is not None:
        release = getattr(ui, "release", None)
        active = getattr(ui, "_active", None)
        if active is not None and hasattr(active, "close"):
            active.close()
        if callable(release):
            release()
    removed = purge()
    module = importlib.import_module("max_side")
    print(f"[mitsuba-max] reloaded, purged {len(removed)} modules")
    return module
