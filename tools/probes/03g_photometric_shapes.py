"""Probe 03g — photometric shape class names (Free_Area, Target_Disc, …).

Max 2027's Create panel produces shape-specific classes, not plain Free_Light /
Target_Light. This probe constructs every photometric shape we can find and dumps
classOf + the properties translate_photometric already reads.

    uv run python tools/maxbatch.py tools/probes/03g_photometric_shapes.py
"""

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-52s %r" % (label + ":", v))
        return v
    except Exception as exc:
        print("%-52s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))
        return None


rt.resetMaxFile(rt.name("noPrompt"))

# Shape type integers on Free_Light / common Max photometric shapes.
# Also try constructing named classes directly.
print("=== Free_Light shapeType sweep ===")
fl = rt.Free_Light(pos=rt.Point3(0, 0, 80))
show("initial classOf", lambda: str(rt.classOf(fl)))
for prop in ("type", "lightType", "shape", "shapeType", "AreaType", "areaType",
             "lightShape", "LightShape", "distribution"):
    show("hasattr %s" % prop, lambda p=prop: hasattr(fl, p))
    if hasattr(fl, prop):
        show("  value", lambda p=prop: getattr(fl, p))

# Try setting shape-like properties through common Max patterns.
for prop in ("type", "shapeType", "lightShape"):
    if not hasattr(fl, prop):
        continue
    print("--- sweep %s ---" % prop)
    for i in range(0, 12):
        try:
            setattr(fl, prop, i)
            print("  %s=%d -> classOf=%s" % (prop, i, rt.classOf(fl)))
        except Exception as exc:
            print("  %s=%d FAILED %s: %s" % (prop, i, type(exc).__name__, exc))

print("=== construct shape classes by name ===")
names = [
    "Free_Light", "Target_Light",
    "Free_Point", "Target_Point",
    "Free_Sphere", "Target_Sphere",
    "Free_Disc", "Target_Disc",
    "Free_Area", "Target_Area",
    "Free_Line", "Target_Line",
    "Free_Cylinder", "Target_Cylinder",
    "Free_Object", "Target_Object",
]
for name in names:
    if not hasattr(rt, name):
        print("  %-20s not on rt" % name)
        continue
    try:
        if name.startswith("Target_"):
            node = getattr(rt, name)(pos=rt.Point3(0, 0, 80),
                                     target=rt.Point(pos=rt.Point3(0, 0, 0)))
        else:
            node = getattr(rt, name)(pos=rt.Point3(0, 0, 80))
    except Exception as exc:
        print("  %-20s CONSTRUCT FAILED %s: %s" % (name, type(exc).__name__, exc))
        continue
    print("  %-20s classOf=%-16s super=%-8s on=%r" % (
        name, rt.classOf(node), rt.superClassOf(node), getattr(node, "on", "NO")))
    for prop in ("intensity", "intensityType", "distribution", "rgbFilter",
                 "useKelvin", "kelvin", "useMultiplier", "multiplier",
                 "hotspot", "falloff", "webFile",
                 "light_Radius", "light_Width", "light_length"):
        show("    %s" % prop, lambda n=node, p=prop: getattr(n, p))

print("=== rt.lights inventory (incl targets?) ===")
for n in list(rt.lights):
    print("  %-32s class=%-18s super=%-12s isKindOf(light)=%s isKindOf(Geometry)=%s" % (
        str(n.name)[:32],
        str(rt.classOf(n))[:18],
        str(rt.superClassOf(n))[:12],
        bool(rt.isKindOf(n, rt.light)),
        bool(rt.isKindOf(n, rt.GeometryClass)),
    ))

print("PROBE_COMPLETE")
