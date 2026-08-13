"""Make the pip-installed CUDA libraries findable by CTranslate2.

CTranslate2 resolves cuBLAS and cuDNN with a bare `LoadLibrary("cublas64_12.dll")` at
the moment of the first encoder call, and that lookup does not consult the directories
added by `os.add_dll_directory` -- verified on this machine: with the directories added
and nothing else, model construction succeeds and the first `encode()` still fails with
"Library cublas64_12.dll is not found or cannot be loaded", while loading the very same
file by absolute path through ctypes succeeds.

So the libraries are loaded here explicitly, by absolute path, before faster_whisper is
imported. Once a module is in the process, a later LoadLibrary for the same base name
resolves to it and no search happens at all.

Call `preload()` exactly once, before importing faster_whisper.
"""

from __future__ import annotations

import ctypes
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

# Order matters: cublas statically imports cublasLt, and cudnn64_9 pulls in the
# cudnn_* engine libraries that sit beside it.
_PRELOAD = ("cublasLt64_12.dll", "cublas64_12.dll", "cudnn64_9.dll")

_done = False


def _nvidia_root() -> Path | None:
    try:
        import nvidia
    except ImportError:
        return None
    paths = list(getattr(nvidia, "__path__", []))
    return Path(paths[0]) if paths else None


def preload() -> None:
    """Load the CUDA libraries shipped in the venv. Idempotent, never raises.

    Missing libraries are logged and left alone: CTranslate2 reports them far more
    precisely than a guess here could, and on a machine where CUDA is installed
    system-wide the pip packages are not needed at all.
    """
    global _done
    if _done:
        return
    _done = True

    root = _nvidia_root()
    if root is None:
        log.info("nvidia-* packages not installed; relying on the system CUDA libraries")
        return

    # Belt and braces for any dependency that *is* resolved through the search path.
    for entry in sorted(root.iterdir()):
        bindir = entry / "bin"
        if bindir.is_dir():
            os.add_dll_directory(str(bindir))

    for name in _PRELOAD:
        match = next(root.rglob(name), None)
        if match is None:
            log.warning("%s not found under %s", name, root)
            continue
        try:
            ctypes.WinDLL(str(match))
            log.debug("preloaded %s", match)
        except OSError as exc:
            log.warning("could not preload %s: %s", match, exc)
