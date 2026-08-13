"""Turning the recogniser's segments into the one line that gets pasted."""

from __future__ import annotations

import re
from collections.abc import Iterable

from . import config as cfg

_PATTERNS = tuple(re.compile(p, re.IGNORECASE) for p in cfg.HALLUCINATIONS)


def is_hallucination(segment: str) -> bool:
    """True if a whole segment looks like large-v3's subtitle-credit artefact."""
    return any(p.search(segment) for p in _PATTERNS)


def join_segments(segments: Iterable[str], *, drop_hallucinations: bool) -> str:
    """Collapse transcript segments into a single line.

    Blank segments are always dropped; hallucinations only when asked, so the caller
    can compare the filtered and unfiltered results and archive the difference.
    """
    kept = []
    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue
        if drop_hallucinations and is_hallucination(segment):
            continue
        kept.append(segment)
    return " ".join(kept).strip()


def clean(segments: Iterable[str]) -> str:
    return join_segments(segments, drop_hallucinations=True)
