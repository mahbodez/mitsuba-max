"""Probe 03 - light classes, cone-angle property names, and whether angles are full or half.

Builds one of each light type, dumps its property names and the values that matter for the
photometric and cone conversions in core/units.py. Writes nothing.

    python tools/maxbatch.py tools/probes/03_lights.py
"""

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-40s %r" % ("  " + label + ":", v))
        return v
    except Exception as exc:
        print("%-40s FAILED  %s: %s" % ("  " + label + ":", type(exc).__name__, exc))
        return None


def dump(name, ctor):
    print("=== %s ===" % name)
    try:
        node = ctor()
    except Exception as exc:
        print("  CONSTRUCTION FAILED  %s: %s" % (type(exc).__name__, exc))
        return None
    show("classOf", lambda: str(rt.classOf(node)))
    show("superClassOf", lambda: str(rt.superClassOf(node)))
    try:
        props = sorted(str(p) for p in rt.getPropNames(node))
        print("  propNames (%d): %s" % (len(props), props))
    except Exception as exc:
        print("  propNames FAILED %s: %s" % (type(exc).__name__, exc))
    return node


rt.resetMaxFile(rt.name("noPrompt"))

# --------------------------------------------------------------------------------------
# standard lights
# --------------------------------------------------------------------------------------
omni = dump("Omnilight", lambda: rt.Omnilight(pos=rt.Point3(0, 0, 50)))
if omni is not None:
    show("multiplier", lambda: float(omni.multiplier))
    show("rgb", lambda: [float(omni.rgb.r), float(omni.rgb.g), float(omni.rgb.b)])
    show("on", lambda: bool(omni.on))
    show("castShadows", lambda: bool(omni.castShadows))
    show("useFarAtten", lambda: bool(omni.useFarAtten))

spot = dump("targetSpot", lambda: rt.targetSpot(pos=rt.Point3(0, 0, 100),
                                                target=rt.Point3(0, 0, 0)))
if spot is not None:
    show("hotspot", lambda: float(spot.hotspot))
    show("falloff", lambda: float(spot.falloff))
    show("multiplier", lambda: float(spot.multiplier))
    show("coneShape (0 circle?)", lambda: str(spot.coneShape))
    # Full vs half angle: set a known hotspot and read back the projected cone. A 60 degree
    # setting that reports 60 is a full angle by Max's own documentation; the definitive
    # check is the geometric one below.
    spot.hotspot = 60.0
    spot.falloff = 80.0
    show("hotspot after set 60", lambda: float(spot.hotspot))
    show("falloff after set 80", lambda: float(spot.falloff))
    show("target pos", lambda: [float(c) for c in spot.target.pos])
    show("transform.row3 (local Z)", lambda: [float(c) for c in spot.transform.row3])
    show("transform.row4 (position)", lambda: [float(c) for c in spot.transform.row4])
    print("  NOTE: row3 is the local +Z axis. If it points from the light TOWARDS the")
    print("  target, Max emits along +Z; if away, Max emits along -Z and max_side must flip.")

fspot = dump("freeSpot", lambda: rt.freeSpot(pos=rt.Point3(50, 0, 100)))
if fspot is not None:
    show("hotspot", lambda: float(fspot.hotspot))
    show("falloff", lambda: float(fspot.falloff))

directional = dump("Directionallight", lambda: rt.Directionallight(pos=rt.Point3(0, 0, 100)))
if directional is not None:
    show("multiplier", lambda: float(directional.multiplier))
    show("hotspot", lambda: float(directional.hotspot))
    show("falloff", lambda: float(directional.falloff))

# --------------------------------------------------------------------------------------
# photometric lights - the ones v1 actually cares about
# --------------------------------------------------------------------------------------
free_light = dump("Free_Light", lambda: rt.Free_Light(pos=rt.Point3(0, 0, 80)))
if free_light is not None:
    for prop in ("intensity", "intensityType", "distribution", "lightColor",
                 "color", "rgbFilter", "on", "targeted", "shape", "shapeType",
                 "areaLightLength", "areaLightWidth", "areaLightRadius",
                 "hotspot", "falloff", "coneAngle", "spotlightConeAngle",
                 "kelvin", "filterColor", "useKelvin", "resultingIntensity",
                 "dimmerValue", "useDimmer"):
        show(prop, (lambda p=prop: getattr(free_light, p)))

target_light = dump("Target_Light", lambda: rt.Target_Light(pos=rt.Point3(0, 0, 80),
                                                            target=rt.Point3(0, 0, 0)))
if target_light is not None:
    for prop in ("intensity", "intensityType", "distribution", "shape",
                 "coneAngle", "hotspot", "falloff", "spotlightConeAngle",
                 "areaLightLength", "areaLightWidth"):
        show(prop, (lambda p=prop: getattr(target_light, p)))

    print("  --- distribution sweep: which props exist per distribution ---")
    for dist in ("isotropic", "spotlight", "photometricWeb", "diffuse"):
        try:
            target_light.distribution = rt.name(dist)
            got = str(target_light.distribution)
            cone = None
            try:
                cone = float(target_light.coneAngle)
            except Exception:
                cone = "n/a"
            hs = None
            try:
                hs = (float(target_light.hotspot), float(target_light.falloff))
            except Exception:
                hs = "n/a"
            print("    %-16s -> %-16s coneAngle=%-10s hotspot/falloff=%s"
                  % (dist, got, cone, hs))
        except Exception as exc:
            print("    %-16s FAILED %s: %s" % (dist, type(exc).__name__, exc))

    print("  --- intensityType sweep ---")
    for it in ("lm", "cd", "lux"):
        try:
            target_light.intensityType = rt.name(it)
            print("    %-6s -> intensityType=%-10s intensity=%s"
                  % (it, str(target_light.intensityType), float(target_light.intensity)))
        except Exception as exc:
            print("    %-6s FAILED %s: %s" % (it, type(exc).__name__, exc))

print("=== class discovery ===")
show("lightsuperclass instances",
     lambda: sorted({str(rt.classOf(o)) for o in rt.objects
                     if str(rt.superClassOf(o)) == "light"}))

print("PROBE_COMPLETE")
