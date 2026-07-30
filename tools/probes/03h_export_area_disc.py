"""Probe 03h — Free_Area / Target_Disc must export through translate_photometric.

    uv run python tools/maxbatch.py tools/probes/03h_export_area_disc.py
"""

import os
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from pathlib import Path

from pymxs import runtime as rt

rt.resetMaxFile(rt.name("noPrompt"))
rt.Box(length=10, width=10, height=10)
rt.Free_Area(pos=rt.Point3(0, 0, 80))
rt.Target_Disc(pos=rt.Point3(50, 0, 80), target=rt.Point(pos=rt.Point3(50, 0, 0)))
rt.viewport.setCamera(rt.Physical(pos=rt.Point3(0, -100, 50)))

from max_side.settings import Settings
from max_side.source import PymxsSource, export_scene

result = export_scene(PymxsSource(), Path(tempfile.gettempdir()) / "mitsuba-max" / "probe03h",
                      Settings())
print("lights exported: %d" % len(result.scene.lights))
for L in result.scene.lights:
    print("  %s kind=%s type=%s" % (L.name, L.kind,
                                    L.photometric_source.max_light_type if L.photometric_source else "?"))
print("warnings:")
for w in result.warnings:
    print("  [%s] %s: %s" % (w.category, w.node, w.reason))
assert len(result.scene.lights) == 2, result.scene.lights
print("PROBE_COMPLETE")
