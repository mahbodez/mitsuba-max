"""Probe 03d — Sun Positioner / Daylight / enabled-attr false negatives.

The user's scene exports geometry and a camera but arrives at emit with zero lights.
03c showed the `superClassOf == "light"` filter is fine for the classic classes. This
probe checks the Max 2027 Create-panel defaults that artists actually place, and whether
`getattr(node, "enabled", True)` falsely treats a light as off.

    uv run python tools/maxbatch.py tools/probes/03d_sun_and_enabled.py
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

print("=== Free_Light: on / enabled / propNames containing 'enabl' or 'on' ===")
fl = rt.Free_Light(pos=rt.Point3(0, 0, 80))
show("on", lambda: bool(fl.on))
show("hasattr enabled", lambda: hasattr(fl, "enabled"))
show("getattr enabled default True", lambda: getattr(fl, "enabled", True))
show("bool(getattr enabled)", lambda: bool(getattr(fl, "enabled", True)))
try:
    props = [str(p) for p in rt.getPropNames(fl)]
    interesting = [p for p in props if "enabl" in p.lower() or p.lower() in
                   ("on", "active", "use", "multiplier", "intensity")]
    print("  interesting props: %s" % interesting)
    for p in interesting:
        show("  fl.%s" % p, lambda p=p: getattr(fl, p))
except Exception as exc:
    print("  propNames FAILED %s: %s" % (type(exc).__name__, exc))

print("=== Omnilight enabled check ===")
omni = rt.Omnilight(pos=rt.Point3(0, 0, 50))
show("hasattr enabled", lambda: hasattr(omni, "enabled"))
show("getattr enabled", lambda: getattr(omni, "enabled", "MISSING"))
show("on", lambda: bool(omni.on))

print("=== construct Sun_Positioner / Daylight / Physical Sun & Sky ===")
for name, ctor in (
    ("Sun_Positioner", lambda: rt.Sun_Positioner()),
    ("SunPositioner", lambda: rt.SunPositioner()),
    ("DaylightAssemblyHead", lambda: rt.DaylightAssemblyHead()),
    ("DaylightSystem", lambda: rt.DaylightSystem()),
    ("Physical_Sun___Sky_Environment", lambda: rt.Physical_Sun___Sky_Environment()),
    ("IES_Sky", lambda: rt.IES_Sky()),
    ("Skylight", lambda: rt.Skylight()),
):
    print("--- %s ---" % name)
    try:
        node = ctor()
    except Exception as exc:
        print("  CONSTRUCTION FAILED  %s: %s" % (type(exc).__name__, exc))
        continue
    show("  name", lambda n=node: str(n.name))
    show("  classOf", lambda n=node: str(rt.classOf(n)))
    show("  superClassOf", lambda n=node: str(rt.superClassOf(n)))
    show("  isKindOf light", lambda n=node: bool(rt.isKindOf(n, rt.light)))
    show("  isKindOf GeometryClass",
         lambda n=node: bool(rt.isKindOf(n, rt.GeometryClass)))
    show("  on", lambda n=node: getattr(n, "on", "NO ATTR"))
    show("  enabled", lambda n=node: getattr(n, "enabled", "NO ATTR"))

print("=== full scene after constructions ===")
for node in list(rt.objects):
    print("  %-28s class=%-28s super=%-14s light=%s geo=%s hidden=%s" % (
        str(node.name)[:28],
        str(rt.classOf(node))[:28],
        str(rt.superClassOf(node))[:14],
        bool(rt.isKindOf(node, rt.light)),
        bool(rt.isKindOf(node, rt.GeometryClass)),
        bool(node.isHidden),
    ))

print("=== environment map slot (not a node) ===")
show("useEnvironmentMap", lambda: bool(rt.useEnvironmentMap))
show("environmentMap", lambda: str(rt.environmentMap) if rt.environmentMap else None)
show("backgroundColor", lambda: [float(rt.backgroundColor.r),
                                  float(rt.backgroundColor.g),
                                  float(rt.backgroundColor.b)])

print("PROBE_COMPLETE")
