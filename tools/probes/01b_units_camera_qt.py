"""Probe 01b - units, camera aperture, Qt bridge.

Read-only. Modifies nothing, writes nothing, opens no dialogs.
Run in the 3ds Max Python listener and paste the entire output back.

Each query is isolated so that one failure cannot abort the rest - the first
attempt at this probe stopped after maxVersion(), most likely because
rt.units.SystemScale raised.
"""

from pymxs import runtime as rt


def show(label, fn):
    try:
        print("%-28s %r" % (label + ":", fn()))
    except Exception as exc:
        print("%-28s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))


print("--- units ---")
show("units.SystemScale", lambda: rt.units.SystemScale)
show("units.SystemType", lambda: rt.units.SystemType)
show("units.DisplayType", lambda: rt.units.DisplayType)
show("units.MetricType", lambda: rt.units.MetricType)
show("getMasterScale(#meters)", lambda: rt.units.getMasterScale(rt.name("meters")))
show("decodeValue 1.0", lambda: rt.units.decodeValue("1.0m"))
show("formatValue 1.0", lambda: rt.units.formatValue(1.0))

print("--- camera / render output ---")
show("getRendApertureWidth", lambda: rt.getRendApertureWidth())
show("renderWidth", lambda: rt.renderWidth)
show("renderHeight", lambda: rt.renderHeight)
show("renderPixelAspect", lambda: rt.renderPixelAspect)

print("--- qt bridge ---")


def _qt():
    import PySide6
    import shiboken6
    return (PySide6.__version__, shiboken6.__name__)


show("PySide6 / shiboken6", _qt)
show("windows.getMAXHWND", lambda: rt.windows.getMAXHWND())
show("GetQMaxMainWindow", lambda: rt.GetQMaxMainWindow())

print("--- active camera, if any ---")


def _cam():
    c = rt.getActiveCamera()
    if c is None:
        return "no active camera - create one and re-run this section"
    props = [p for p in rt.getPropNames(c)]
    return (str(rt.classOf(c)), [str(p) for p in props])


show("active camera", _cam)
