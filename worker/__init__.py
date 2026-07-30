"""The out-of-process renderer.

Runs in its own virtual environment containing `mitsuba` and `numpy` and nothing else.
Never imported by Max: Dr.Jit loads its own native DLLs and LLVM/CUDA backends, and Max
already has TBB, Qt, Arnold's LLVM and OpenImageIO in the same address space.

Entry points:
    python -m worker            protocol loop, driven by the host over stdin/stdout
    python -m worker.selftest   render the built-in Cornell box, prove the venv works
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
