"""Getting the model onto the machine, and knowing when it is not there.

The model is the one thing a copy of this project cannot bring with it: 3.09 GB, and the
reason `models/` is in .gitignore. It is fetched here rather than left to
faster-whisper's own lazy download so that the bytes land inside the project, next to the
code that needs them, instead of in `%USERPROFILE%\\.cache\\huggingface` where a later
cache clean removes them and the next dictation silently starts a 3 GB download.

`missing()` is the check `doctor` and `setup` both use. It exists because a partial
model directory does not fail informatively: WhisperModel reports neither which file is
absent nor that a `model.bin` is a 130-byte Git LFS pointer rather than the weights.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_REPO = "Systran/faster-whisper-large-v3"

# What CTranslate2 opens. Everything else in the repo -- README, .gitattributes -- would
# be copied for nothing.
REQUIRED = (
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)

# large-v3's model.bin is 3,087,284,237 bytes. Anything under a gigabyte is a truncated
# download or an LFS pointer: present, plausible-looking, and unusable.
MIN_WEIGHTS_BYTES = 1_000_000_000


def directory_for(repo: str, root: Path) -> Path:
    """`Systran/faster-whisper-large-v3` -> `<root>/models/faster-whisper-large-v3`."""
    return root / "models" / repo.split("/")[-1]


def missing(directory: Path) -> list[str]:
    """Which required files are absent or implausibly small. Empty means usable."""
    if not directory.is_dir():
        return [f"the directory {directory} does not exist"]
    gaps = []
    for name in REQUIRED:
        path = directory / name
        if not path.is_file():
            gaps.append(name)
        elif name == "model.bin" and path.stat().st_size < MIN_WEIGHTS_BYTES:
            gaps.append(f"{name} (only {path.stat().st_size:,} bytes)")
    return gaps


def size_gb(directory: Path) -> float:
    if not directory.is_dir():
        return 0.0
    return sum(f.stat().st_size for f in directory.glob("*") if f.is_file()) / 2**30


def fetch(directory: Path, repo: str = DEFAULT_REPO) -> tuple[bool, str]:
    """Download `repo` into `directory`. Returns (ok, message); never raises.

    Resumable in the sense that huggingface_hub skips files it already has with a
    matching etag, so re-running after an interrupted download costs one HEAD request
    per file rather than another 3 GB.
    """
    from huggingface_hub import snapshot_download

    try:
        snapshot_download(
            repo_id=repo,
            local_dir=str(directory),
            allow_patterns=list(REQUIRED),
        )
    except Exception as exc:  # noqa: BLE001 -- network, disk, permissions, auth
        return False, f"download failed ({type(exc).__name__}: {exc})"

    gaps = missing(directory)
    if gaps:
        return False, f"download finished but these are still wrong: {', '.join(gaps)}"
    return True, f"{directory} ({size_gb(directory):.2f} GB)"


def ensure(directory: Path, repo: str = DEFAULT_REPO) -> tuple[bool, str]:
    """Download only what is not already there."""
    gaps = missing(directory)
    if not gaps:
        return True, f"already present: {directory} ({size_gb(directory):.2f} GB)"
    log.info("model incomplete (%s); fetching %s", ", ".join(gaps), repo)
    return fetch(directory, repo)
