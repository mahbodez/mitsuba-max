"""Probe 09 - how to read a bitmap's colour space, and which way V points.

Probe 07 showed `Bitmaptexture` has **no** `gamma` property; the gamma lives on the
`.bitmap` object, which is `undefined` until a file is loaded. So this probe writes a
throwaway PNG under build/probe/, loads it, and reads the colour-space properties for real.

Getting this wrong is silent: a roughness map decoded as sRGB is wrong everywhere and looks
merely "a bit off", so it survives review indefinitely.

Writes only under build/probe/ (permitted by CLAUDE.md rule 5).

    python tools/maxbatch.py tools/probes/09_bitmap_gamma.py
"""

import os

from pymxs import runtime as rt

OUT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "build", "probe"))
os.makedirs(OUT, exist_ok=True)


def show(label, fn):
    try:
        v = fn()
        print("%-46s %r" % ("  " + label + ":", v))
        return v
    except Exception as exc:
        print("%-46s FAILED  %s: %s" % ("  " + label + ":", type(exc).__name__, exc))
        return None


rt.resetMaxFile(rt.name("noPrompt"))

# ------------------------------------------------------------------------------------
# make a test image whose corners are distinguishable, so the V direction is readable
# ------------------------------------------------------------------------------------
png = os.path.join(OUT, "probe09_uv.png").replace("\\", "/")
print("=== write test bitmap ===")
try:
    bmp = rt.bitmap(64, 64, filename=png)
    # bottom-left red, top-left green: enough to tell V=0 from V=1 later.
    red = rt.color(255, 0, 0)
    green = rt.color(0, 255, 0)
    for y in range(64):
        row = [red if y >= 32 else green for _ in range(64)]
        rt.setPixels(bmp, rt.Point2(0, y), row)
    rt.save(bmp)
    rt.close(bmp)
    print("  wrote %s (%d bytes)" % (png, os.path.getsize(png)))
except Exception as exc:
    print("  FAILED %s: %s" % (type(exc).__name__, exc))

print("=== Bitmaptexture with a file loaded ===")
bt = rt.Bitmaptexture(filename=png)
show("bt.filename", lambda: bt.filename)
show("bt.bitmap", lambda: bt.bitmap)
show("classOf(bt.bitmap)", lambda: str(rt.classOf(bt.bitmap)))

print("  --- bitmap object properties ---")
try:
    for p in sorted(str(x) for x in rt.getPropNames(bt.bitmap)):
        try:
            print("    %-26s %r" % (p, getattr(bt.bitmap, p)))
        except Exception as exc:
            print("    %-26s READ FAILED %s" % (p, exc))
except Exception as exc:
    print("  getPropNames FAILED %s: %s" % (type(exc).__name__, exc))

print("=== probe 09a: gamma override ===")
show("bt.bitmap.gamma", lambda: float(bt.bitmap.gamma))
show("global fileInGamma", lambda: float(rt.fileInGamma))
show("global displayGamma", lambda: float(rt.displayGamma))
show("bt.bitmap.filename", lambda: str(bt.bitmap.filename))

print("  --- can the gamma be forced on load? ---")
for g in (1.0, 2.2):
    try:
        b2 = rt.openBitMap(png, gamma=g)
        print("    openBitMap(gamma=%.1f) -> bitmap.gamma=%r" % (g, float(b2.gamma)))
        rt.close(b2)
    except Exception as exc:
        print("    openBitMap(gamma=%.1f) FAILED %s: %s" % (g, type(exc).__name__, exc))

print("=== probe 09b: UV coordinate origin ===")
show("coords.U_Offset", lambda: float(bt.coords.U_Offset))
show("coords.V_Offset", lambda: float(bt.coords.V_Offset))
show("coords.U_Tiling", lambda: float(bt.coords.U_Tiling))
show("coords.V_Tiling", lambda: float(bt.coords.V_Tiling))
show("coords.mapChannel", lambda: int(bt.coords.mapChannel))
show("coords.UVTransform", lambda: str(bt.coords.UVTransform))

print("  --- where does TVert (0,0) sit on a box? ---")
try:
    box = rt.Box(width=10.0, length=10.0, height=10.0, mapCoords=True)
    m = rt.snapshotAsMesh(box)
    n = int(rt.getNumTVerts(m))
    uvs = [tuple(round(float(c), 4) for c in rt.getTVert(m, i)) for i in range(1, min(n, 8) + 1)]
    print("    first %d tverts: %s" % (len(uvs), uvs))
    print("    Max UVs are 0..1 with V increasing UPWARD; PLY/Mitsuba expect the same,")
    print("    so a V flip is a bug if it appears. The chirality golden scene decides.")
except Exception as exc:
    print("    FAILED %s: %s" % (type(exc).__name__, exc))

print("=== probe 09c: reading pixels for the invert bake ===")
# Mitsuba has no arithmetic texture node, so a glossiness map must be inverted at export.
# That requires reading pixel data out of Max. Confirm the API and its speed.
try:
    b3 = rt.openBitMap(png)
    show("b3.width/height", lambda: (int(b3.width), int(b3.height)))
    row = rt.getPixels(b3, rt.Point2(0, 0), 64)
    print("  getPixels row0 len=%d first=%r" % (len(row), row[0]))
    row2 = rt.getPixels(b3, rt.Point2(0, 63), 64)
    print("  getPixels row63 first=%r" % (row2[0],))
    print("  row 0 is the TOP row in Max's bitmap addressing if it reads green.")
    rt.close(b3)
except Exception as exc:
    print("  FAILED %s: %s" % (type(exc).__name__, exc))

print("=== assigning a map to a PhysicalMaterial slot ===")
try:
    mat = rt.PhysicalMaterial()
    mat.base_color_map = bt
    show("mat.base_color_map", lambda: mat.base_color_map)
    show("classOf(mat.base_color_map)", lambda: str(rt.classOf(mat.base_color_map)))
    show("mat.base_color_map_on", lambda: bool(mat.base_color_map_on))
    show("getSubTexmap(mat, 2)", lambda: rt.getSubTexmap(mat, 2))
except Exception as exc:
    print("  FAILED %s: %s" % (type(exc).__name__, exc))

print("PROBE_COMPLETE")
