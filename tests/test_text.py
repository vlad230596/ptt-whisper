"""The hallucination filter, checked against the recordings that motivated it."""

from __future__ import annotations

from pathlib import Path

import pytest

from pushtotalk import config as cfg, text

DATASET = Path(cfg.DATA_DIR)


@pytest.mark.parametrize(
    "segment",
    [
        "Субтитры создавал DimaTorzok",
        "Редактор субтитров А.Синецкая",
        "Продолжение следует...",
        "Спасибо за просмотр!",
        "Подписывайтесь на канал",
        "Субтитры",
        "  Аминь.",
        "Ура!",
        # What 2.5 s of silence actually produced before VAD was switched on.
        "Subtitles by the Amara.org community",
    ],
)
def test_known_hallucinations_are_dropped(segment: str) -> None:
    assert text.is_hallucination(segment)


@pytest.mark.parametrize(
    "segment",
    [
        "Смотри, у нас в проекте используется некий фреймворк.",
        "Спасибо, я записал эту мысль.",          # "спасибо" alone must survive
        "Это захват хендла окна, куда надо вставить текст.",
        "Корректировка не нужна.",                # near "Корректор", must survive
    ],
)
def test_real_speech_survives(segment: str) -> None:
    assert not text.is_hallucination(segment)


def test_join_collapses_segments_to_one_line() -> None:
    segments = [" Первый сегмент. ", "", "Второй сегмент."]
    assert text.clean(segments) == "Первый сегмент. Второй сегмент."


def test_join_keeps_hallucinations_when_asked() -> None:
    segments = ["Настоящий текст.", "Продолжение следует..."]
    assert text.clean(segments) == "Настоящий текст."
    assert text.join_segments(segments, drop_hallucinations=False) == (
        "Настоящий текст. Продолжение следует..."
    )


def test_empty_input() -> None:
    assert text.clean([]) == ""
    assert text.clean(["", "   "]) == ""


@pytest.mark.skipif(not DATASET.is_dir(), reason="no archived recordings")
def test_archived_transcripts_are_stable() -> None:
    """Re-filtering an archived transcript must not change it.

    The .txt files were written *after* filtering, so any change here means a pattern
    became too broad and is now eating real speech.
    """
    checked = 0
    for path in DATASET.glob("*.txt"):
        if path.name.endswith(".raw.txt"):
            continue
        stored = path.read_text(encoding="utf-8").strip()
        if not stored:
            continue
        assert text.clean([stored]) == stored, f"{path.name} would now be filtered out"
        checked += 1
    assert checked, "no archived transcripts found to check"


@pytest.mark.skipif(not DATASET.is_dir(), reason="no archived recordings")
def test_raw_archives_still_get_filtered() -> None:
    """Where a .raw.txt exists, the filter must still remove something from it.

    That file only gets written when filtering changed the result, so it is a recorded
    instance of the bug the patterns exist for.
    """
    for path in DATASET.glob("*.raw.txt"):
        raw = path.read_text(encoding="utf-8")
        segments = raw.splitlines()
        filtered = text.clean(segments)
        unfiltered = text.join_segments(segments, drop_hallucinations=False)
        assert filtered != unfiltered, f"{path.name} is no longer being filtered"
