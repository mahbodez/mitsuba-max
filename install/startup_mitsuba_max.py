"""3ds Max startup shim for mitsuba-max.

Copy (or symlink) this file into a Max startup scripts folder, e.g.

    %LOCALAPPDATA%\\Autodesk\\3dsMax\\2027 - 64bit\\ENU\\scripts\\startup\\

and set `MITSUBA_MAX_ROOT` to the repository, or edit `_default_root()` below.

Deliberately minimal, and that is the whole design. `max_side.devreload` purges every
project module from `sys.modules` so edits take effect without restarting Max; anything
this file holds at module scope would survive that purge as an object the reloaded code no
longer recognises, and produce a bug that only appears after a reload and vanishes on
restart. So: no state, no imported symbols kept, no cached window.

Everything here is wrapped so a failure prints a diagnosis and lets Max finish starting.
A startup script that raises can leave Max in a half-initialised state, which is a far
worse outcome than a renderer that is not available.
"""

import os
import sys
import traceback


def _default_root():
    """The repository root. Override with MITSUBA_MAX_ROOT."""
    override = os.environ.get("MITSUBA_MAX_ROOT")
    if override:
        return override
    # This file normally lives in <repo>/install/, so the repo is one level up. When it has
    # been *copied* into Max's startup folder that is wrong, and the environment variable
    # is the answer.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _install_menu(root):
    from pymxs import runtime as rt

    # A macroscript, so the action appears in Customize > Menus and can be bound to a key
    # or a quad menu like any other Max command.
    rt.execute("\n".join((
        'macroScript MitsubaRender',
        '  category:"mitsuba-max"',
        '  buttonText:"Render with Mitsuba"',
        '  tooltip:"Render the scene with Mitsuba 3"',
        '(',
        '  on execute do python.execute "import max_side; max_side.render()"',
        ')',
        'macroScript MitsubaExport',
        '  category:"mitsuba-max"',
        '  buttonText:"Export Mitsuba scene"',
        '  tooltip:"Extract the scene to IR and write its assets, without rendering"',
        '(',
        '  on execute do python.execute "import max_side; max_side.export_only()"',
        ')',
        'macroScript MitsubaSetup',
        '  category:"mitsuba-max"',
        '  buttonText:"Mitsuba environment setup"',
        '  tooltip:"Create and validate the worker Python environment"',
        '(',
        '  on execute do python.execute "import max_side; max_side.setup_environment()"',
        ')',
        'macroScript MitsubaReload',
        '  category:"mitsuba-max"',
        '  buttonText:"Reload mitsuba-max"',
        '  tooltip:"Developer: purge and re-import every plugin module"',
        '(',
        '  on execute do python.execute "import max_side; max_side.reload_plugin()"',
        ')',
    )))
    print("[mitsuba-max] four actions registered under the 'mitsuba-max' category")
    print("[mitsuba-max] Customize > Customize User Interface > Menus to place them")


def main():
    root = _default_root()
    if not os.path.isdir(os.path.join(root, "max_side")):
        print(f"[mitsuba-max] not a plugin root: {root}")
        print("[mitsuba-max] set MITSUBA_MAX_ROOT to the repository and restart Max")
        return

    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        import max_side  # noqa: F401 - imported for its side effect, then dropped

        _install_menu(root)
        print(f"[mitsuba-max] loaded from {root}")
    except Exception:  # noqa: BLE001 - see the module docstring
        # Never re-raise: a startup script that raises can leave Max half-initialised.
        print("[mitsuba-max] failed to load; Max will start without it")
        traceback.print_exc()


main()
