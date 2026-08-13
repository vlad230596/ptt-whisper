"""The chooser's guards -- the parts that must hold without opening a window.

No test here creates a Dear PyGui context. That is deliberate as well as convenient:
one context per process is all Dear PyGui allows at a time, and a test that left one
behind would take the rest of the suite with it.
"""

from __future__ import annotations

import pytest

from pushtotalk import micui


@pytest.fixture(autouse=True)
def clean_state():
    """Leave the module exactly as found, whatever a test does to it."""
    yield
    if micui._open.locked():
        micui._open.release()
    micui._close_requested.clear()


def test_nothing_is_open_to_begin_with() -> None:
    assert micui.is_open() is False


def test_a_second_window_is_refused_rather_than_opened() -> None:
    """Two Dear PyGui contexts in one process is not a thing that recovers -- so the
    second Ctrl+Alt+M has to be a no-op, not a crash."""
    micui._open.acquire()
    assert micui.is_open() is True
    assert micui.open_window("Some microphone") is False
    assert micui.open_in_thread("Some microphone") is None


def test_close_can_be_requested_from_another_thread() -> None:
    """The Close button, Escape and app shutdown all reach the render loop this way."""
    assert not micui._close_requested.is_set()
    micui.request_close()
    assert micui._close_requested.is_set()


def test_the_import_alone_does_not_touch_dearpygui() -> None:
    """Any dpg.* call before create_context() is an access violation that kills the
    process, so nothing may run at import time."""
    import sys

    assert "dearpygui.dearpygui" not in sys.modules
