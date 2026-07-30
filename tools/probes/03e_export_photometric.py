"""Probe 03e — end-to-end: Free_Light through export_scene must produce IR lights.

Reproduces the user's "0 lights" report against the live exporter, with per-step
diagnostics so a silent drop (registry miss, on/enabled, translator exception) is
visible.

    uv run python tools/maxbatch.py tools/probes/03e_export_photometric.py
"""

import os
import sys
import tempfile
import traceback

# Prefer the repo the probe lives in, not a stale MITSUBA_MAX_ROOT.
_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from pymxs import runtime as rt

rt.resetMaxFile(rt.name("noPrompt"))

# Minimal lit scene: one box, one photometric light, one physical camera.
box = rt.Box(length=10, width=10, height=10)
light = rt.Free_Light(pos=rt.Point3(0, 0, 80))
cam = rt.Physical(pos=rt.Point3(0, -100, 50))
rt.viewport.setCamera(cam)

print("=== scene nodes ===")
for n in list(rt.objects):
    print("  %-28s class=%-20s super=%-10s on=%r enabled=%r hidden=%s" % (
        str(n.name)[:28],
        str(rt.classOf(n))[:20],
        str(rt.superClassOf(n))[:10],
        getattr(n, "on", "NO ATTR"),
        getattr(n, "enabled", "NO ATTR"),
        bool(n.isHidden),
    ))

print("=== registry before export ===")
from core.registry import LIGHTS
print("  LIGHTS.supported: %s" % (LIGHTS.supported(),))
print("  lookup Free_Light: %r" % (LIGHTS.lookup("Free_Light"),))

# Importing lights registers the handlers.
import max_side.lights  # noqa: F401
print("  after import max_side.lights: %s" % (LIGHTS.supported(),))
print("  lookup Free_Light: %r" % (LIGHTS.lookup("Free_Light"),))

print("=== light_nodes() ===")
from max_side.source import PymxsSource, export_scene
from max_side.settings import Settings
from max_side.lights import LightContext, translate_light

src = PymxsSource()
nodes = src.light_nodes()
print("  count: %d" % len(nodes))
for n in nodes:
    print("  - %s (%s)" % (n.name, rt.classOf(n)))

print("=== translate_light direct ===")
ctx = LightContext(scene_scale_to_meters=src.scale_to_meters())
for n in nodes:
    try:
        got = translate_light(n, ctx)
        print("  %s -> %r" % (n.name, None if got is None else (got.kind, got.name)))
    except Exception:
        print("  %s RAISED:" % n.name)
        traceback.print_exc()
print("  warnings: %s" % [ (w.node, w.reason) for w in ctx.warnings ])

print("=== export_scene ===")
root = os.path.join(tempfile.gettempdir(), "mitsuba-max", "probe03e")
try:
    result = export_scene(src, __import__("pathlib").Path(root), Settings())
    print("  meshes: %d" % len(result.scene.meshes))
    print("  lights: %d" % len(result.scene.lights))
    for L in result.scene.lights:
        print("    light %s kind=%s" % (L.name, L.kind))
    print("  warnings (%d):" % len(result.warnings))
    for w in result.warnings:
        print("    [%s] %s: %s" % (w.category, w.node, w.reason))
except Exception:
    traceback.print_exc()

print("PROBE_COMPLETE")
