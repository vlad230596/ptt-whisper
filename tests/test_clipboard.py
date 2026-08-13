"""Clipboard save/restore -- the equivalent of AutoHotkey's ClipboardAll.

These tests touch the real clipboard. Each one snapshots it first and puts it back at
the end, so running the suite does not cost you whatever you had copied.
"""

from __future__ import annotations

import ctypes

import pytest

from pushtotalk import paste
from pushtotalk import winapi as W


@pytest.fixture(autouse=True)
def preserve_clipboard():
    try:
        original = paste.snapshot()
    except paste.ClipboardBusy:
        pytest.skip("the clipboard is held by another application")
    yield
    paste.restore(original)


def _read_text() -> str | None:
    with paste._clipboard():
        handle = W.GetClipboardData(W.CF_UNICODETEXT)
        if not handle:
            return None
        pointer = W.GlobalLock(handle)
        try:
            return ctypes.wstring_at(pointer)
        finally:
            W.GlobalUnlock(handle)


def test_set_text_round_trip() -> None:
    paste.set_text("Проверка буфера обмена")
    assert _read_text() == "Проверка буфера обмена"


def test_text_with_newlines_and_emoji() -> None:
    payload = "первая строка\nвторая строка\tтаб 🎤"
    paste.set_text(payload)
    assert _read_text() == payload


def test_snapshot_restore_preserves_text() -> None:
    paste.set_text("исходное содержимое")
    saved = paste.snapshot()
    paste.set_text("затёрто дикта́нтом")
    assert _read_text() == "затёрто дикта́нтом"
    paste.restore(saved)
    assert _read_text() == "исходное содержимое"


def test_snapshot_preserves_extra_formats() -> None:
    """A custom binary format must survive the round trip byte for byte.

    This is the part plain text-only save/restore gets wrong: copying from an
    application that puts HTML, RTF or its own format on the clipboard and then
    dictating would otherwise degrade what you had copied.
    """
    custom = W.RegisterClipboardFormatW("PushToTalkTestFormat")
    blob = bytes(range(256))
    with paste._clipboard():
        W.EmptyClipboard()
        paste._put(W.CF_UNICODETEXT, ("текст" + "\0").encode("utf-16-le"))
        paste._put(custom, blob)

    saved = paste.snapshot()
    assert dict(saved)[custom] == blob

    paste.set_text("что-то другое")
    paste.restore(saved)

    with paste._clipboard():
        handle = W.GetClipboardData(custom)
        size = W.GlobalSize(handle)
        pointer = W.GlobalLock(handle)
        try:
            assert ctypes.string_at(pointer, size) == blob
        finally:
            W.GlobalUnlock(handle)
    assert _read_text() == "текст"


def test_copyable_skips_handle_formats() -> None:
    assert not paste._copyable(2)       # CF_BITMAP
    assert not paste._copyable(14)      # CF_ENHMETAFILE
    assert not paste._copyable(0x0301)  # inside CF_GDIOBJFIRST..LAST
    assert paste._copyable(W.CF_UNICODETEXT)
    assert paste._copyable(15)          # CF_HDROP is a plain HGLOBAL
