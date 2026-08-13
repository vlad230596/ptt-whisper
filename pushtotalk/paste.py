"""Getting the text into the window you were looking at when you started talking.

Two things have to be right:

* the destination is the window that had focus at key-down, not whatever has it two
  seconds later, so the caller captures the handle early and passes it in;
* focus goes back to wherever you moved on to, so dictating does not yank you out of it.

**The previous clipboard contents are not put back, deliberately.** Both this program and
the AutoHotkey version it replaces used to save the clipboard and restore it on a 600 ms
timer. That is a race, and it loses: Ctrl+V is delivered asynchronously, and the target
reads the clipboard whenever it next gets round to its message queue. Measured with a
target stalled for 900 ms and 2 s, it read the *restored* previous contents and pasted
those -- under load, the dictation silently turned into whatever you had copied before it.
The same stall with the timer at 5 s pasted correctly, which pins the cause to the timer
rather than to anything about the write. (The write is never late: `set_text` is
synchronous and readable immediately, 1 ms idle and 91 ms under a saturated CPU.)

Restoring on a *longer* timer only narrows the window, and it has a second failure of its
own -- it overwrites whatever you copied in the meantime, since it cannot tell your copy
from its own. So the dictation simply keeps the clipboard. A dictation you can paste again
is worth more than the clipboard contents it replaced.

When the text cannot be delivered at all it is likewise left on the clipboard: a Ctrl+V
away is better than losing the dictation.
"""

from __future__ import annotations

import ctypes
import logging
import time
from contextlib import contextmanager
from enum import Enum

from . import config as cfg
from . import winapi as W
from .hotkeys import INJECT_SIGNATURE

log = logging.getLogger(__name__)

VK_V = 0x56

# Clipboard formats that are not HGLOBAL blocks and cannot be copied as bytes.
_NON_HGLOBAL = frozenset({
    2,      # CF_BITMAP
    3,      # CF_METAFILEPICT (an HGLOBAL, but it *contains* a handle)
    9,      # CF_PALETTE
    14,     # CF_ENHMETAFILE
    0x80,   # CF_OWNERDISPLAY
    0x82,   # CF_DSPBITMAP
    0x83,   # CF_DSPMETAFILEPICT
    0x8E,   # CF_DSPENHMETAFILE
})


class Outcome(Enum):
    OK = "ok"
    CLIPBOARD_BUSY = "clipboard busy"
    TARGET_GONE = "target window is gone"
    NO_FOCUS = "target window would not take focus"


class ClipboardBusy(RuntimeError):
    pass


# ------------------------------------------------------------------ clipboard
@contextmanager
def _clipboard(timeout: float = 1.0):
    """Own the clipboard for the duration of the block.

    Another application can hold it open; the retry loop is what stops a transient
    conflict from turning into a lost dictation.
    """
    deadline = time.monotonic() + timeout
    while not W.OpenClipboard(None):
        if time.monotonic() >= deadline:
            raise ClipboardBusy("another application is holding the clipboard open")
        time.sleep(0.02)
    try:
        yield
    finally:
        W.CloseClipboard()


def _copyable(fmt: int) -> bool:
    # 0x0300..0x03FF is CF_GDIOBJFIRST..CF_GDIOBJLAST: GDI handles, not bytes.
    return fmt not in _NON_HGLOBAL and not 0x0300 <= fmt <= 0x03FF


def snapshot() -> list[tuple[int, bytes]]:
    """Every byte-copyable clipboard format, as (format, bytes).

    No longer part of the paste path -- see the module docstring. Kept because the test
    suite uses it, with `restore()`, to leave your real clipboard exactly as it found it.
    """
    saved: list[tuple[int, bytes]] = []
    with _clipboard():
        fmt = W.EnumClipboardFormats(0)
        while fmt:
            if _copyable(fmt):
                handle = W.GetClipboardData(fmt)
                size = W.GlobalSize(handle) if handle else 0
                if size:
                    pointer = W.GlobalLock(handle)
                    if pointer:
                        try:
                            saved.append((fmt, ctypes.string_at(pointer, size)))
                        finally:
                            W.GlobalUnlock(handle)
            fmt = W.EnumClipboardFormats(fmt)
    return saved


def _put(fmt: int, data: bytes) -> None:
    """Hand `data` to the clipboard, which takes ownership of the allocation."""
    handle = W.GlobalAlloc(W.GMEM_MOVEABLE, len(data))
    if not handle:
        raise MemoryError("GlobalAlloc for the clipboard failed")
    pointer = W.GlobalLock(handle)
    if not pointer:
        W.GlobalFree(handle)
        raise MemoryError("GlobalLock for the clipboard failed")
    try:
        ctypes.memmove(pointer, data, len(data))
    finally:
        W.GlobalUnlock(handle)
    if not W.SetClipboardData(fmt, handle):
        W.GlobalFree(handle)
        raise OSError(f"SetClipboardData(0x{fmt:04X}) failed")


def restore(saved: list[tuple[int, bytes]]) -> None:
    try:
        with _clipboard():
            W.EmptyClipboard()
            for fmt, data in saved:
                try:
                    _put(fmt, data)
                except OSError:
                    log.debug("could not restore clipboard format 0x%04X", fmt)
    except Exception:
        log.exception("restoring the clipboard failed")


def set_text(text: str) -> None:
    with _clipboard():
        W.EmptyClipboard()
        _put(W.CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))


# ------------------------------------------------------------------ focus
def foreground_window() -> int:
    return W.GetForegroundWindow()


def activate(hwnd: int, timeout: float = 2.0) -> bool:
    """Bring `hwnd` to the foreground.

    A plain SetForegroundWindow is refused when the calling process is not the
    foreground one; attaching to the target's input queue for the duration of the call
    is the standard way around the foreground lock.
    """
    if W.GetForegroundWindow() == hwnd:
        return True
    if W.IsIconic(hwnd):
        W.ShowWindow(hwnd, W.SW_RESTORE)
    our_thread = W.GetCurrentThreadId()
    target_thread = W.GetWindowThreadProcessId(hwnd, None)
    attached = bool(W.AttachThreadInput(our_thread, target_thread, True))
    try:
        W.SetForegroundWindow(hwnd)
        W.BringWindowToTop(hwnd)
    finally:
        if attached:
            W.AttachThreadInput(our_thread, target_thread, False)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if W.GetForegroundWindow() == hwnd:
            return True
        time.sleep(0.02)
    return False


# ------------------------------------------------------------------ keystrokes
def _key(vk: int, *, up: bool = False) -> W.INPUT:
    item = W.INPUT()
    item.type = W.INPUT_KEYBOARD
    item.ki.wVk = vk
    item.ki.wScan = W.MapVirtualKeyW(vk, 0)
    item.ki.dwFlags = W.KEYEVENTF_KEYUP if up else 0
    item.ki.time = 0
    # So our own hook ignores what we inject.
    item.ki.dwExtraInfo = INJECT_SIGNATURE
    return item


def _held(vk: int) -> bool:
    return bool(W.GetAsyncKeyState(vk) & 0x8000)


def send_ctrl_v() -> None:
    """Ctrl+V, addressed by virtual-key code.

    The V is sent as VK_V rather than as the character 'v' so it does not depend on the
    active layout mapping that letter -- dictation here runs under a Russian layout.
    Any modifier the user happens to be holding is released first, so this cannot turn
    into Ctrl+Shift+V (a different command in a lot of editors).
    """
    events: list[W.INPUT] = []
    for stray in (W.VK_SHIFT, W.VK_MENU, W.VK_LWIN, W.VK_RWIN):
        if _held(stray):
            log.debug("releasing stray modifier 0x%02X before pasting", stray)
            events.append(_key(stray, up=True))
    events += [
        _key(W.VK_CONTROL),
        _key(VK_V),
        _key(VK_V, up=True),
        _key(W.VK_CONTROL, up=True),
    ]
    array = (W.INPUT * len(events))(*events)
    sent = W.SendInput(len(events), array, ctypes.sizeof(W.INPUT))
    if sent != len(events):
        raise OSError(f"SendInput sent {sent} of {len(events)} events")


# ------------------------------------------------------------------ the flow
def deliver(text: str, target: int) -> Outcome:
    """Put `text` into `target` via the clipboard, then hand focus back.

    The dictation keeps the clipboard afterwards -- see the note at the top of this
    module for why nothing is restored.
    """
    try:
        set_text(text)
    except ClipboardBusy:
        return Outcome.CLIPBOARD_BUSY

    # Where the user is right now -- focus is handed back here afterwards.
    current = W.GetForegroundWindow()

    if target and not W.IsWindow(target):
        # Pasting into whatever happens to be focused would drop the text somewhere
        # unpredictable. Leave it on the clipboard instead.
        return Outcome.TARGET_GONE

    if target and target != current:
        if not activate(target):
            return Outcome.NO_FOCUS
        time.sleep(cfg.FOCUS_SETTLE / 1000)

    send_ctrl_v()
    time.sleep(cfg.PASTE_DELAY / 1000)

    if cfg.RESTORE_FOCUS and current and current != target and W.IsWindow(current):
        activate(current, timeout=0.5)

    return Outcome.OK
