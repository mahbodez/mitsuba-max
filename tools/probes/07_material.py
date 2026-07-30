"""Probe 07 (+09) - PhysicalMaterial and Bitmaptexture property dumps.

SPEC 9 forbids writing the material mapping table from memory. This prints every property
name, its value and its Python type, so docs/MATERIAL_MAPPING.md can be written from
evidence. Also answers probe 09: how a bitmap's colour space / gamma override is read,
which decides the `raw` flag on every non-colour texture.

Writes nothing, loads nothing.

    python tools/maxbatch.py tools/probes/07_material.py
"""

from pymxs import runtime as rt


def show(label, fn):
    try:
        v = fn()
        print("%-42s %r" % ("  " + label + ":", v))
        return v
    except Exception as exc:
        print("%-42s FAILED  %s: %s" % ("  " + label + ":", type(exc).__name__, exc))
        return None


def dump_props(obj, title):
    print("=== %s ===" % title)
    print("  classOf:      %s" % rt.classOf(obj))
    print("  superClassOf: %s" % rt.superClassOf(obj))
    try:
        names = sorted(str(p) for p in rt.getPropNames(obj))
    except Exception as exc:
        print("  getPropNames FAILED %s: %s" % (type(exc).__name__, exc))
        return []
    print("  %d properties" % len(names))
    for n in names:
        try:
            v = getattr(obj, n)
            tn = type(v).__name__
            if tn == "MXSWrapperBase":
                tn = "mxs:%s" % rt.classOf(v)
            print("    %-34s %-22s %r" % (n, tn, v))
        except Exception as exc:
            print("    %-34s READ FAILED %s: %s" % (n, type(exc).__name__, exc))
    return names


rt.resetMaxFile(rt.name("noPrompt"))

mat = rt.PhysicalMaterial()
names = dump_props(mat, "PhysicalMaterial (defaults)")

print("=== roughness semantics ===")
# Max may store glossiness rather than roughness. If a `roughness_inv` flag exists, the
# meaning of `roughness` flips with it and reading the number alone is not enough.
show("roughness", lambda: float(mat.roughness))
show("roughness_inv exists", lambda: "roughness_inv" in names)
show("roughness_inv value", lambda: mat.roughness_inv)
show("roughness_map", lambda: mat.roughness_map)
show("roughness_map_on", lambda: mat.roughness_map_on)
show("trans_rough_inv value", lambda: mat.trans_rough_inv)
show("coat_rough_inv value", lambda: mat.coat_rough_inv)

print("=== the parameters SPEC 9 maps ===")
for prop in ("base_weight", "base_color", "reflectivity", "refl_color", "roughness",
             "metalness", "diff_roughness", "anisotropy", "aniso_angle", "aniso_mode",
             "transparency", "trans_color", "trans_depth", "trans_roughness",
             "trans_ior", "thin_walled",
             "emission", "emit_color", "emit_luminance", "emit_kelvin",
             "coating", "coat_color", "coat_roughness", "coat_ior", "coat_affect_color",
             "scattering", "sss_color", "sss_depth", "sss_scale",
             "thin_film", "thin_film_ior", "thin_film_thickness",
             "bump_map", "bump_map_amt", "displacement_map", "cutout_map",
             "material_mode", "brdf_mode"):
    show(prop, (lambda p=prop: getattr(mat, p)))

print("=== map slots ===")
show("getNumSubTexmaps", lambda: int(rt.getNumSubTexmaps(mat)))
try:
    for i in range(1, int(rt.getNumSubTexmaps(mat)) + 1):
        print("    slot %2d  %-30s = %r"
              % (i, str(rt.getSubTexmapSlotName(mat, i)), rt.getSubTexmap(mat, i)))
except Exception as exc:
    print("  subtexmap enumeration FAILED %s: %s" % (type(exc).__name__, exc))

# --------------------------------------------------------------------------------------
# probe 09 - bitmap colour space
# --------------------------------------------------------------------------------------
print()
bt = rt.Bitmaptexture()
dump_props(bt, "Bitmaptexture (defaults, no file loaded)")

print("=== probe 09: gamma / colour space ===")
show("bt.bitmap", lambda: bt.bitmap)
show("bt.fileName", lambda: bt.fileName)
for prop in ("gamma", "preMultAlpha", "alphaSource", "monoOutput", "rgbOutput",
             "coordinates", "output"):
    show(prop, (lambda p=prop: getattr(bt, p)))

print("  --- coords sub-object (UV tiling / offset) ---")
try:
    coords = bt.coords
    for p in sorted(str(x) for x in rt.getPropNames(coords)):
        try:
            print("    %-28s %r" % (p, getattr(coords, p)))
        except Exception as exc:
            print("    %-28s READ FAILED %s" % (p, exc))
except Exception as exc:
    print("  coords FAILED %s: %s" % (type(exc).__name__, exc))

print("  --- bitmap file gamma API ---")
show("rt.bitmapLoader.getGamma exists", lambda: hasattr(rt, "bitmapLoader"))
show("freeImage gamma prefs", lambda: rt.getINISetting(rt.getMAXIniFile(), "Gamma", "Enable"))
show("displayGamma", lambda: float(rt.displayGamma))
show("fileInGamma", lambda: float(rt.fileInGamma))
show("fileOutGamma", lambda: float(rt.fileOutGamma))

print("=== assignment smoke test ===")
try:
    mat.base_color = rt.color(255, 128, 64)
    print("  base_color after set: %r  (Max colours are 0-255 ints, not 0-1 floats)"
          % (mat.base_color,))
    c = mat.base_color
    print("  components: r=%r g=%r b=%r" % (c.r, c.g, c.b))
except Exception as exc:
    print("  FAILED %s: %s" % (type(exc).__name__, exc))

print("=== material class discovery ===")
for cls in ("PhysicalMaterial", "StandardMaterial", "Standardmaterial", "Multimaterial",
            "MultiMaterial", "Physical_Material"):
    show("rt.%s exists" % cls, (lambda c=cls: hasattr(rt, c)))

print("PROBE_COMPLETE")
