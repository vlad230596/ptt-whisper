"""Delivery into a real window, driven by real injected keystrokes.

The test builds its own top-level window with an EDIT control, hands it to
`paste.deliver()` as the target, and reads the control back. That exercises the parts
that only fail in the presence of an actual window manager: getting past the foreground
lock, Ctrl+V arriving as Ctrl+V under whatever layout is active, and the clipboard being
handed back afterwards.

It takes focus for about a second while it runs. Deselect with `-m "not integration"`.
"""

from __future__ import annotations

import ctypes
import threading
import time

import pytest

from pushtotalk import paste
from pushtotalk import winapi as W

pytestmark = pytest.mark.integration

WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VSCROLL = 0x00200000
ES_MULTILINE = 0x0004
SW_SHOW = 5
_CLASS_NAME = "PushToTalkPasteTarget"


class Target:
    """A throwaway window with a focused edit control."""

    def __init__(self) -> None:
        self._proc = W.WNDPROC(
            lambda h, m, wp, lp: W.DefWindowProcW(h, m, wp, lp)
        )
        wc = W.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(W.WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = W.GetModuleHandleW(None)
        wc.hCursor = W.LoadCursorW(None, W.int_resource(W.IDC_ARROW))
        wc.lpszClassName = _CLASS_NAME
        W.RegisterClassExW(ctypes.byref(wc))  # harmless if already registered

        self.hwnd = W.CreateWindowExW(
            0, _CLASS_NAME, "PushToTalk paste target", WS_OVERLAPPEDWINDOW,
            120, 120, 520, 240, None, None, wc.hInstance, None,
        )
        assert self.hwnd, "could not create the target window"
        self.edit = W.CreateWindowExW(
            0, "EDIT", "", WS_CHILD | WS_VISIBLE | ES_MULTILINE | WS_VSCROLL,
            0, 0, 500, 200, self.hwnd, None, wc.hInstance, None,
        )
        assert self.edit, "could not create the edit control"
        W.ShowWindow(self.hwnd, SW_SHOW)
        W.SetForegroundWindow(self.hwnd)
        W.SetFocus(self.edit)

    def pump(self) -> None:
        msg = W.MSG()
        while W.PeekMessageW(ctypes.byref(msg), None, 0, 0, W.PM_REMOVE):
            W.TranslateMessage(ctypes.byref(msg))
            W.DispatchMessageW(ctypes.byref(msg))

    def read(self) -> str:
        buffer = ctypes.create_unicode_buffer(4096)
        W.SendMessageW(self.edit, W.WM_GETTEXT, len(buffer),
                       ctypes.cast(buffer, ctypes.c_void_p).value)
        return buffer.value

    def close(self) -> None:
        W.DestroyWindow(self.hwnd)
        self.pump()


def run_pumping(work, timeout: float = 15.0):
    """Run `work` on a thread while pumping this thread's messages.

    Both halves are needed: `deliver` blocks on focus changes that only happen if the
    target's thread -- this one -- keeps dispatching messages.
    """
    result: dict[str, object] = {}
    thread = threading.Thread(target=lambda: result.update(value=work()))
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive() and time.monotonic() < deadline:
        target_pump()
        time.sleep(0.01)
    thread.join(timeout=1.0)
    assert "value" in result, "the delivery thread did not finish"
    return result["value"]


target_pump = lambda: None  # replaced per-test by the fixture


@pytest.fixture
def target():
    global target_pump
    window = Target()
    target_pump = window.pump
    # Let the window actually become foreground before anything is pasted into it.
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and W.GetForegroundWindow() != window.hwnd:
        window.pump()
        time.sleep(0.02)
    if W.GetForegroundWindow() != window.hwnd:
        window.close()
        pytest.skip("could not put the test window in the foreground (locked session?)")
    yield window
    window.close()
    target_pump = lambda: None


@pytest.fixture(autouse=True)
def preserve_clipboard():
    try:
        original = paste.snapshot()
    except paste.ClipboardBusy:
        pytest.skip("the clipboard is held by another application")
    yield
    paste.restore(original)


def test_text_arrives_in_the_target_window(target: Target) -> None:
    payload = "Проверка вставки в целевое окно"
    outcome = run_pumping(lambda: paste.deliver(payload, target.hwnd))
    assert outcome is paste.Outcome.OK
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and target.read() != payload:
        target.pump()
        time.sleep(0.05)
    assert target.read() == payload


def test_multiline_text_arrives_intact(target: Target) -> None:
    payload = "первая строка\r\nвторая строка"
    outcome = run_pumping(lambda: paste.deliver(payload, target.hwnd))
    assert outcome is paste.Outcome.OK
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and target.read() != payload:
        target.pump()
        time.sleep(0.05)
    assert target.read() == payload


def _clipboard_text() -> str:
    with paste._clipboard():
        handle = W.GetClipboardData(W.CF_UNICODETEXT)
        if not handle:
            return ""
        try:
            return ctypes.wstring_at(W.GlobalLock(handle))
        finally:
            W.GlobalUnlock(handle)


def test_the_dictation_keeps_the_clipboard(target: Target) -> None:
    """Nothing is restored afterwards -- that timer is what caused the paste race."""
    paste.set_text("моё содержимое буфера")
    run_pumping(lambda: paste.deliver("продиктованный текст", target.hwnd))
    # Well past the 600 ms the old restore timer used to fire at.
    time.sleep(1.2)
    assert _clipboard_text() == "продиктованный текст"


def test_a_slow_target_still_receives_the_dictation(target: Target) -> None:
    """Regression: under load the target pasted the *previous* clipboard contents.

    The target window belongs to this process, so its Ctrl+V is only acted on when this
    thread pumps messages. Not pumping is an exact stand-in for an application that is
    loaded and has not reached its message queue yet. With the old 600 ms restore timer
    this pasted "СТАРЫЙ" at stalls of 900 ms and above; it must now paste the dictation
    no matter how late the target gets round to it.
    """
    paste.set_text("СТАРЫЙ")
    outcome = paste.deliver("новая продиктованная строка", target.hwnd)
    assert outcome is paste.Outcome.OK

    time.sleep(1.5)  # the target is busy and has not read the clipboard yet
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and target.read() != "новая продиктованная строка":
        target.pump()
        time.sleep(0.05)
    assert target.read() == "новая продиктованная строка"


def test_a_copy_made_after_a_dictation_is_not_clobbered(target: Target) -> None:
    """The old restore also overwrote whatever you copied while it was pending."""
    paste.set_text("СТАРЫЙ")
    run_pumping(lambda: paste.deliver("продиктованное", target.hwnd))
    paste.set_text("то, что скопировал пользователь")
    time.sleep(1.2)  # past the old timer
    assert _clipboard_text() == "то, что скопировал пользователь"


def test_a_dead_target_leaves_the_text_on_the_clipboard() -> None:
    """Losing the window must not lose the dictation."""
    payload = "текст для исчезнувшего окна"
    window = Target()
    hwnd = window.hwnd
    window.close()
    outcome = paste.deliver(payload, hwnd)
    assert outcome is paste.Outcome.TARGET_GONE
    with paste._clipboard():
        handle = W.GetClipboardData(W.CF_UNICODETEXT)
        assert handle, "the text was not left on the clipboard"
        try:
            assert ctypes.wstring_at(W.GlobalLock(handle)) == payload
        finally:
            W.GlobalUnlock(handle)
