"""Probe 03c — how light nodes identify themselves under pymxs in Max 2027.

Answers two questions that gate light export:

1. What does `str(rt.superClassOf(light))` actually return? `PymxsSource.light_nodes`
   filters on `== "light"`. If the string is `"Light"` or a Name repr, every light is
   silently dropped and the scene looks unlit at emit time.
2. What `classOf` strings do the Create-panel defaults produce in 2027, including
   Skylight / Sun Positioner / any renamed photometric classes?

Builds its own fixtures; writes nothing outside the print stream.

    uv run python tools/maxbatch.py tools/probes/03c_light_discovery.py
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

ctors = [
    ("Omnilight", lambda: rt.Omnilight(pos=rt.Point3(0, 0, 50))),
    ("freeSpot", lambda: rt.freeSpot(pos=rt.Point3(0, 0, 50))),
    ("Directionallight", lambda: rt.Directionallight(pos=rt.Point3(0, 0, 50))),
    ("Free_Light", lambda: rt.Free_Light(pos=rt.Point3(0, 0, 80))),
    ("Target_Light", lambda: rt.Target_Light(
        pos=rt.Point3(0, 0, 80), target=rt.Point(pos=rt.Point3(0, 0, 0)))),
    ("Skylight", lambda: rt.Skylight(pos=rt.Point3(0, 0, 100))),
]

print("=== constructed lights ===")
for name, ctor in ctors:
    print("--- %s ---" % name)
    try:
        node = ctor()
    except Exception as exc:
        print("  CONSTRUCTION FAILED  %s: %s" % (type(exc).__name__, exc))
        continue
    show("  classOf", lambda n=node: str(rt.classOf(n)))
    show("  classOf repr", lambda n=node: repr(rt.classOf(n)))
    show("  superClassOf str", lambda n=node: str(rt.superClassOf(n)))
    show("  superClassOf repr", lambda n=node: repr(rt.superClassOf(n)))
    show("  superClassOf == rt.light", lambda n=node: bool(rt.superClassOf(n) == rt.light))
    show("  isKindOf light", lambda n=node: bool(rt.isKindOf(n, rt.light)))
    show("  str == 'light'", lambda n=node: str(rt.superClassOf(n)) == "light")
    show("  str.lower() == 'light'",
         lambda n=node: str(rt.superClassOf(n)).lower() == "light")
    show("  on", lambda n=node: bool(getattr(n, "on", "NO ATTR")))
    show("  isHidden", lambda n=node: bool(n.isHidden))

print("=== Sun Positioner / Physical Sun & Sky (if present) ===")
for attr in ("Sun_Positioner", "SunPositioner", "Physical_Sun___Sky_Environment",
             "DaylightAssemblyHead", "IES_Sky", "mr_Sky", "Skylight"):
    show("hasattr rt.%s" % attr, lambda a=attr: hasattr(rt, a))

print("=== scene inventory after construction ===")
show("objects count", lambda: len(list(rt.objects)))
for node in list(rt.objects):
    print("  %-24s classOf=%-20s super=%-12s isKindOf(light)=%s" % (
        str(node.name),
        str(rt.classOf(node)),
        str(rt.superClassOf(node)),
        bool(rt.isKindOf(node, rt.light)),
    ))

print("=== filter simulation (current source.py) ===")
matched = [n for n in rt.objects
           if str(rt.superClassOf(n)) == "light" and not bool(n.isHidden)]
print("  str == 'light': %d nodes -> %s" % (
    len(matched), [str(rt.classOf(n)) for n in matched]))

matched_ci = [n for n in rt.objects
              if str(rt.superClassOf(n)).lower() == "light" and not bool(n.isHidden)]
print("  str.lower() == 'light': %d nodes -> %s" % (
    len(matched_ci), [str(rt.classOf(n)) for n in matched_ci]))

matched_kind = [n for n in rt.objects
                if bool(rt.isKindOf(n, rt.light)) and not bool(n.isHidden)]
print("  isKindOf(light): %d nodes -> %s" % (
    len(matched_kind), [str(rt.classOf(n)) for n in matched_kind]))

print("PROBE_COMPLETE")
