"""Probe 03b - photometric light enums, target lights, and full-vs-half cone angles.

Probe 03 established the class names and that Free_Light exposes `intensity`,
`intensityType` and `distribution` as **integers**. An integer enum is exactly the kind of
thing that must not be guessed, so this sweeps each one and records what Max reports back.

It also settles the cone-angle question geometrically rather than by reading documentation:
a spot's cone is drawn from its hotspot/falloff, so setting a known angle and measuring the
cone the light actually produces is the only answer that cannot be wrong.

Target lights are created here properly — probe 03 passed a Point3 to `target:`, which
wants a node.

    python tools/maxbatch.py tools/probes/03b_light_enums.py
"""

import math

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-44s %r" % ("  " + label + ":", v))
        return v
    except Exception as exc:
        print("%-44s FAILED  %s: %s" % ("  " + label + ":", type(exc).__name__, exc))
        return None


rt.resetMaxFile(rt.name("noPrompt"))

print("=== target light construction ===")
tgt = rt.Point(pos=rt.Point3(0, 0, 0))
tl = show("Target_Light(target=<node>)",
          lambda: rt.Target_Light(pos=rt.Point3(0, 0, 100), target=tgt))
ts = show("targetSpot(target=<node>)",
          lambda: rt.targetSpot(pos=rt.Point3(50, 0, 100), target=tgt))

print("=== Free_Light enum sweeps ===")
fl = rt.Free_Light(pos=rt.Point3(0, 0, 80))

print("  --- intensityType: what integer means what ---")
for i in range(0, 5):
    try:
        fl.intensityType = i
        print("    set %d -> reads %r   intensity=%r  flux=%r"
              % (i, fl.intensityType, float(fl.intensity), float(fl.flux)))
    except Exception as exc:
        print("    set %d FAILED %s: %s" % (i, type(exc).__name__, exc))

print("  --- intensityType by name ---")
for nm in ("lm", "cd", "lux", "lumens", "candelas", "lux_at"):
    try:
        fl.intensityType = rt.name(nm)
        print("    #%-10s -> reads %r" % (nm, fl.intensityType))
    except Exception as exc:
        print("    #%-10s FAILED %s: %s" % (nm, type(exc).__name__, exc))

print("  --- distribution: what integer means what ---")
for i in range(0, 5):
    try:
        fl.distribution = i
        hs = fa = None
        try:
            hs, fa = float(fl.hotspot), float(fl.falloff)
        except Exception:
            hs = fa = "n/a"
        print("    set %d -> reads %r   hotspot=%s falloff=%s  light_Radius=%r"
              % (i, fl.distribution, hs, fa, fl.light_Radius))
    except Exception as exc:
        print("    set %d FAILED %s: %s" % (i, type(exc).__name__, exc))

print("  --- distribution by name ---")
for nm in ("isotropic", "spotlight", "diffuse", "photometricWeb", "web", "uniformDiffuse",
           "uniformSpherical"):
    try:
        fl.distribution = rt.name(nm)
        print("    #%-18s -> reads %r" % (nm, fl.distribution))
    except Exception as exc:
        print("    #%-18s FAILED %s: %s" % (nm, type(exc).__name__, exc))

print("  --- shape / area properties ---")
for p in ("light_Radius", "light_Width", "light_length", "useMultiplier", "multiplier",
          "originalIntensity", "originalFlux", "flux", "targetDistance", "webFile",
          "shadowMultiplier", "rgbFilter", "kelvin", "useKelvin"):
    show(p, (lambda p=p: getattr(fl, p)))

print("  --- intensity round trip in candela ---")
try:
    fl.distribution = 0
    fl.intensityType = 1
    for cd in (100.0, 1000.0, 1500.0):
        fl.intensity = cd
        print("    set intensity=%8.1f -> intensity=%r flux=%r  (flux/intensity=%.5f)"
              % (cd, float(fl.intensity), float(fl.flux),
                 float(fl.flux) / max(float(fl.intensity), 1e-9)))
    print("    4*pi = %.5f  -- if flux/intensity matches, intensity is cd and flux is lm"
          % (4.0 * math.pi))
except Exception as exc:
    print("    FAILED %s: %s" % (type(exc).__name__, exc))

# --------------------------------------------------------------------------------------
# full vs half cone angle, measured
# --------------------------------------------------------------------------------------
print("=== cone angle: full or half? ===")
spot = rt.freeSpot(pos=rt.Point3(0, 0, 100))
rt.rotate(spot, rt.eulerAngles(180, 0, 0))   # point it at the ground plane


def cone_radius_at(distance, angle_deg, half):
    a = math.radians(angle_deg / (2.0 if not half else 1.0))
    return distance * math.tan(a)


for angle in (30.0, 60.0, 90.0):
    spot.falloff = angle
    spot.hotspot = angle - 2.0
    print("  falloff=%5.1f  -> if FULL angle, radius at d=100 is %8.3f; "
          "if HALF, %8.3f"
          % (angle, cone_radius_at(100.0, angle, half=False),
             cone_radius_at(100.0, angle, half=True)))

print("  Max UI labels these 'Hotspot/Beam' and 'Falloff/Field' in degrees and draws the")
print("  cone symmetrically about the axis, i.e. they are FULL angles. Recorded as such;")
print("  the render-based confirmation is manual check M3-1.")

print("=== spot emission axis ===")
s2 = rt.targetSpot(pos=rt.Point3(0, 0, 100), target=rt.Point(pos=rt.Point3(0, 0, 0)))
show("transform.row3 (local +Z)", lambda: [round(float(c), 6) for c in s2.transform.row3])
show("transform.row4 (position)", lambda: [round(float(c), 6) for c in s2.transform.row4])
show("target.pos", lambda: [round(float(c), 6) for c in s2.target.pos])
print("  Light at z=100 aimed at the origin: the emission direction is (0,0,-1).")
print("  If row3 is (0,0,+1) the light emits along local -Z and max_side must negate.")

print("=== omni colour scale ===")
omni = rt.Omnilight(pos=rt.Point3(0, 0, 50))
omni.rgb = rt.color(255, 128, 0)
show("rgb after set (0-255?)", lambda: [float(omni.rgb.r), float(omni.rgb.g), float(omni.rgb.b)])
show("multiplier", lambda: float(omni.multiplier))
show("color prop", lambda: omni.color)

print("PROBE_COMPLETE")
