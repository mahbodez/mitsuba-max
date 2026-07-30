"""Probe 13 - does 3dsmaxbatch.exe work, and what does the interpreter look like inside it.

Gates every other autonomous probe. Builds nothing, loads nothing, writes nothing.

    python tools/maxbatch.py tools/probes/13_batch_env.py
"""

import os
import sys

from pymxs import runtime as rt


def show(label, fn):
    try:
        print("%-32s %r" % (label + ":", fn()))
    except Exception as exc:
        print("%-32s FAILED  %s: %s" % (label + ":", type(exc).__name__, exc))


print("--- interpreter ---")
show("sys.version", lambda: sys.version)
show("sys.executable", lambda: sys.executable)
show("sys.prefix", lambda: sys.prefix)
show("cwd", os.getcwd)

print("--- max ---")
# rt.maxVersion() is not sliceable through pymxs - indexing it raises IndexError with a
# MAXScript error rather than returning a sub-array. Convert to a list first.
show("maxVersion", lambda: list(rt.maxVersion()))
show("maxVersion[7] (year)", lambda: int(list(rt.maxVersion())[7]))
show("maxFilePath", lambda: str(rt.maxFilePath))
show("maxFileName", lambda: str(rt.maxFileName))
show("objects count", lambda: int(rt.objects.count))

print("--- ui presence ---")
# In batch mode there is no main window; this tells us definitively rather than by
# inference, and confirms which parts of the plugin can never be tested this way.
show("windows.getMAXHWND", lambda: rt.windows.getMAXHWND())
show("hasCurrentSelection", lambda: bool(rt.selection.count))

print("PROBE_COMPLETE")
