"""Worker environment discovery, creation and validation.

The most common failure point in a DCC renderer integration is not the renderer — it is the
Python environment, on a corporate laptop, behind a proxy that blocks PyPI. So this is a
first-class flow with one hard rule: **when it fails, show the subprocess's actual stderr**.
"Failed to initialise Mitsuba" with the real error swallowed is not a diagnostic, it is a
support ticket.

Preference order (SPEC §2.2): a configured interpreter, then a managed venv created from
Max's own `sys.executable`, then one the user browses to.

The middle option is the good one and probe 12 confirmed it works: Max 2027 embeds CPython
3.13.9, Mitsuba 3.9.0 publishes cp313 wheels, and Max's Python directory has no `._pth` and
no `sitecustomize.py`, so a venv built from it gets a clean `sys.path` with no Max
directories leaking in. It also means the user needs no Python installation at all, and —
as a bonus this project relies on — the resulting numpy is ABI-identical to Max's
interpreter, which is what `max_side.numpy_bridge` borrows.

Everything here runs a subprocess. None of it may be called on Max's main thread without a
progress dialog: `pip install mitsuba` downloads about 200 MB.
"""

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from max_side.settings import config_dir

__all__ = [
    "PROJECT_PTH",
    "EnvReport",
    "create_managed_venv",
    "install_packages",
    "managed_venv_python",
    "validate_interpreter",
    "write_project_pth",
]

PROJECT_PTH = "mitsuba_max_project.pth"
r"""Name of the `.pth` file that puts this repository on the worker's `sys.path`.

A `.pth` rather than `PYTHONPATH`, and that is not a style preference. Probe 12 found that
Max's interpreter — and any venv created from it — prints

    PYTHONPATH is not set to "<Max install>/Python" - overriding setting for
    the current session

**on stdout** whenever `PYTHONPATH` is set to anything else. That line would be the first
thing the host reads from the worker's protocol stream.
"""

_CREATE_NO_WINDOW = 0x08000000


@dataclass(slots=True)
class EnvReport:
    """The result of poking an interpreter. `ok` is the only thing callers should branch on."""

    ok: bool
    interpreter: str
    python_version: str = ""
    mitsuba_version: str = ""
    numpy_version: str = ""
    stdout: str = ""
    stderr: str = ""
    message: str = ""

    def detail(self) -> str:
        """Everything worth pasting into a bug report, in one copyable block."""
        parts = [f"interpreter: {self.interpreter}"]
        if self.message:
            parts.append(f"problem:     {self.message}")
        if self.python_version:
            parts.append(f"python:      {self.python_version}")
        if self.mitsuba_version:
            parts.append(f"mitsuba:     {self.mitsuba_version}")
        if self.numpy_version:
            parts.append(f"numpy:       {self.numpy_version}")
        if self.stdout.strip():
            parts.append("--- stdout ---\n" + self.stdout.strip())
        if self.stderr.strip():
            parts.append("--- stderr ---\n" + self.stderr.strip())
        return "\n".join(parts)


def managed_venv_python() -> Path:
    """`%LOCALAPPDATA%\\mitsuba-max\\venv\\Scripts\\python.exe`."""
    return config_dir() / "venv" / "Scripts" / "python.exe"


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a clean environment and no console window.

    `PYTHONPATH` and `PYTHONHOME` are removed rather than merely not set: Max inherits the
    environment of whatever launched it, and a developer who exported `PYTHONPATH` in their
    shell would otherwise get the stdout banner described on `PROJECT_PTH`.
    """
    import os

    env = dict(os.environ)
    for var in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(var, None)
    return subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, env=env, creationflags=_CREATE_NO_WINDOW,
    )


_VALIDATE_SOURCE = (
    "import sys, json\n"
    "out = {'python': sys.version.replace(chr(10), ' ')}\n"
    "try:\n"
    "    import mitsuba; out['mitsuba'] = mitsuba.__version__\n"
    "except Exception as exc:\n"
    "    out['mitsuba_error'] = '%s: %s' % (type(exc).__name__, exc)\n"
    "try:\n"
    "    import numpy; out['numpy'] = numpy.__version__\n"
    "except Exception as exc:\n"
    "    out['numpy_error'] = '%s: %s' % (type(exc).__name__, exc)\n"
    "print('MMXENV' + json.dumps(out))\n"
)


def validate_interpreter(interpreter: str | Path, *, timeout: int = 180) -> EnvReport:
    """Check that an interpreter can import `mitsuba` and `numpy`, and report what it found.

    The probe script prints one marker-prefixed JSON line. The marker matters because
    `import mitsuba` can emit unrelated chatter — on the machine this was developed on it
    prints `jitc_llvm_init(): LLVM API initialization failed` — and scanning for the marker
    is what separates "the environment is broken" from "the environment is fine and noisy".
    """
    interpreter = str(interpreter)
    if not Path(interpreter).is_file():
        return EnvReport(ok=False, interpreter=interpreter,
                         message="the interpreter does not exist at that path")

    try:
        proc = _run([interpreter, "-c", _VALIDATE_SOURCE], timeout=timeout)
    except subprocess.TimeoutExpired:
        return EnvReport(ok=False, interpreter=interpreter,
                         message=f"the interpreter did not respond within {timeout}s")
    except OSError as exc:
        return EnvReport(ok=False, interpreter=interpreter,
                         message=f"could not start the interpreter: {exc}")

    marker = next((ln for ln in proc.stdout.splitlines() if ln.startswith("MMXENV")), None)
    if marker is None:
        return EnvReport(ok=False, interpreter=interpreter, stdout=proc.stdout,
                         stderr=proc.stderr,
                         message="the interpreter produced no usable output")

    import json

    info = json.loads(marker[len("MMXENV"):])
    report = EnvReport(
        ok="mitsuba" in info and "numpy" in info,
        interpreter=interpreter,
        python_version=info.get("python", ""),
        mitsuba_version=info.get("mitsuba", ""),
        numpy_version=info.get("numpy", ""),
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
    if not report.ok:
        missing = [k for k in ("mitsuba", "numpy") if k not in info]
        detail = "; ".join(info[f"{k}_error"] for k in missing if f"{k}_error" in info)
        report.message = f"missing {' and '.join(missing)} — {detail}"
    return report


def create_managed_venv(base_interpreter: str | Path | None = None, *,
                        timeout: int = 300) -> EnvReport:
    """Create the managed venv, defaulting to Max's own interpreter as the base.

    `--system-site-packages` is never passed. Max's `site-packages` holds its custom PySide6
    build, and Autodesk is explicit that a second PySide6 breaks it; inheriting the whole
    directory would also put pymxs in the worker's path, where it cannot work.
    """
    base = str(base_interpreter or sys.executable)
    target = config_dir() / "venv"
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = _run([base, "-m", "venv", str(target)], timeout=timeout)
    except subprocess.TimeoutExpired:
        return EnvReport(ok=False, interpreter=base,
                         message=f"creating the virtual environment timed out after {timeout}s")

    if proc.returncode != 0:
        return EnvReport(ok=False, interpreter=base, stdout=proc.stdout, stderr=proc.stderr,
                         message=f"`{base} -m venv` failed with exit code {proc.returncode}")

    python = managed_venv_python()
    if not python.is_file():
        return EnvReport(ok=False, interpreter=base, stdout=proc.stdout, stderr=proc.stderr,
                         message=f"the venv was created but {python} is missing")
    return EnvReport(ok=True, interpreter=str(python))


def install_packages(interpreter: str | Path, *, timeout: int = 1800) -> EnvReport:
    """`pip install mitsuba numpy` — and nothing else, ever.

    **Never install PySide6 here.** The worker is headless and needs no Qt, and Autodesk
    warns that a PySide6 in `site-packages` conflicts with Max's custom build. That is hard
    invariant 2.

    The generous timeout is because Mitsuba's wheel is large and corporate proxies are slow;
    the failure that matters is the one where PyPI is unreachable, and that surfaces as pip's
    own stderr, verbatim.
    """
    interpreter = str(interpreter)
    try:
        proc = _run([interpreter, "-m", "pip", "install", "--disable-pip-version-check",
                     "mitsuba", "numpy"], timeout=timeout)
    except subprocess.TimeoutExpired:
        return EnvReport(ok=False, interpreter=interpreter,
                         message=f"pip did not finish within {timeout}s — a blocked or very "
                                 "slow connection to PyPI is the usual cause")

    if proc.returncode != 0:
        return EnvReport(ok=False, interpreter=interpreter, stdout=proc.stdout,
                         stderr=proc.stderr,
                         message=f"pip failed with exit code {proc.returncode}")
    return validate_interpreter(interpreter)


def write_project_pth(interpreter: str | Path, project_root: Path) -> Path | None:
    """Put this repository on the worker's `sys.path` via a `.pth` file.

    Returns the path written, or `None` if the interpreter's `site-packages` could not be
    located — which happens when the user browsed to a system interpreter rather than a
    venv, and is worth reporting rather than silently skipping, because the worker will then
    fail to import `core` for a reason that looks unrelated.
    """
    python = Path(interpreter)
    site_packages = python.parent.parent / "Lib" / "site-packages"
    if not site_packages.is_dir():
        return None
    pth = site_packages / PROJECT_PTH
    pth.write_text(str(project_root) + "\n", encoding="utf-8")
    return pth
