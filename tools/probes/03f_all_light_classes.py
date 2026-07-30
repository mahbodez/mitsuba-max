"""Probe 03f — every creatable light class in Max 2027, and rt.lights vs rt.objects.

    uv run python tools/maxbatch.py tools/probes/03f_all_light_classes.py
"""

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-56s %r" % (label + ":", v))
        return v
    except Exception as exc:
        print("%-56s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))
        return None


rt.resetMaxFile(rt.name("noPrompt"))

print("=== rt.light subclasses (ListClasses) ===")
try:
    classes = list(rt.ListClasses())
    lightish = []
    for c in classes:
        try:
            # class is a Max class; check superclass when possible
            sc = str(rt.superClassOf(c)) if hasattr(rt, "superClassOf") else "?"
        except Exception:
            sc = "?"
        name = str(c)
        if "light" in name.lower() or "sun" in name.lower() or "sky" in name.lower() or sc == "light":
            lightish.append((name, sc))
    for name, sc in sorted(set(lightish)):
        print("  %-40s super=%s" % (name, sc))
    print("  (%d candidates)" % len(set(lightish)))
except Exception as exc:
    print("  ListClasses FAILED %s: %s" % (type(exc).__name__, exc))

print("=== try construct everything with 'Light' or 'Sun' in the name ===")
names = [
    "Omnilight", "freeSpot", "targetSpot", "Directionallight", "targetDirectionallight",
    "Free_Light", "Target_Light", "Skylight", "IES_Sky", "Sun_Positioner", "SunPositioner",
    "FreeLight", "TargetLight", "PhotometricLight", "Photometric_Light",
    "Light", "Plane_Light", "Disc_Light", "Sphere_Light", "Cylinder_Light",
    "Arnold_Light", "ai_area_light", "ai_photometric_light", "ai_sky", "ai_skydome_light",
    "VRayLight", "VRaySun", "VRaySky", "PhysicalSun", "PhysicalSky",
]
for name in names:
    if not hasattr(rt, name):
        continue
    print("--- rt.%s ---" % name)
    try:
        ctor = getattr(rt, name)
        node = ctor()
        print("  classOf=%s super=%s on=%r enabled=%r" % (
            rt.classOf(node), rt.superClassOf(node),
            getattr(node, "on", "NO"), getattr(node, "enabled", "NO")))
    except Exception as exc:
        print("  FAILED %s: %s" % (type(exc).__name__, exc))

print("=== rt.lights collection vs objects filter ===")
rt.Free_Light(pos=rt.Point3(0, 0, 50))
rt.Omnilight(pos=rt.Point3(10, 0, 50))
# Group a light inside a Max group
g_light = rt.Free_Light(pos=rt.Point3(20, 0, 50))
g_box = rt.Box()
try:
    rt.group([g_light, g_box], name="LightGroup")
    print("  grouped ok")
except Exception as exc:
    print("  group FAILED %s: %s" % (type(exc).__name__, exc))

show("len(rt.lights)", lambda: len(list(rt.lights)))
show("rt.lights classOf", lambda: [str(rt.classOf(n)) for n in rt.lights])
show("objects isKindOf light",
     lambda: [str(rt.classOf(n)) for n in rt.objects if rt.isKindOf(n, rt.light)])
show("objects super==light",
     lambda: [str(rt.classOf(n)) for n in rt.objects
              if str(rt.superClassOf(n)) == "light"])

print("=== hidden / layer ===")
L = list(rt.lights)[0]
L.isHidden = True
show("hidden still in rt.lights", lambda: L in list(rt.lights))
show("hidden passes objects filter (current code excludes)",
     lambda: [str(n.name) for n in rt.objects
              if str(rt.superClassOf(n)) == "light" and not bool(n.isHidden)])

print("PROBE_COMPLETE")
