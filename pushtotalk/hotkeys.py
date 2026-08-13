"""Global hotkeys via a low-level keyboard hook.

Why a hook and not RegisterHotKey: RegisterHotKey only reports key *presses*, and
push-to-talk needs the release too. WH_KEYBOARD_LL reports both and can swallow the
key so it never reaches the focused application.

The one hard rule of this file: **the hook callback must return fast**. Windows silently
removes a low-level hook whose callback exceeds LowLevelHooksTimeout (300 ms by
default) -- the hotkey then just stops working, with no error anywhere. So the callback
only classifies the key and hands the verdict to a queue; every actual handler runs on
another thread.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from dataclasses import dataclass

from . import winapi as W

log = logging.getLogger(__name__)

# Stamped into dwExtraInfo of everything this program injects, so the hook can ignore
# its own synthetic Ctrl+V. Deliberately *not* a blanket LLKHF_INJECTED filter: leaving
# other injected input visible is what makes the hotkeys drivable from a test.
INJECT_SIGNATURE = 0x50545430  # 'PTT0'

_VK_NAMES: dict[str, int] = {
    "BACKSPACE": 0x08, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D,
    "ESC": 0x1B, "ESCAPE": 0x1B, "SPACE": 0x20,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "END": 0x23, "HOME": 0x24,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "INSERT": 0x2D, "DELETE": 0x2E,
    "CAPSLOCK": 0x14, "PAUSE": 0x13, "SCROLLLOCK": 0x91, "NUMLOCK": 0x90,
    "PRINTSCREEN": 0x2C, "APPS": 0x5D,
    "MULTIPLY": 0x6A, "ADD": 0x6B, "SUBTRACT": 0x6D, "DECIMAL": 0x6E, "DIVIDE": 0x6F,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
}
_VK_NAMES |= {f"F{i}": 0x6F + i for i in range(1, 25)}          # F1..F24 -> 0x70..0x87
_VK_NAMES |= {c: ord(c) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
_VK_NAMES |= {f"NUMPAD{i}": 0x60 + i for i in range(10)}

_MODIFIERS = {"CTRL": "ctrl", "CONTROL": "ctrl", "ALT": "alt", "SHIFT": "shift", "WIN": "win"}


@dataclass(frozen=True, slots=True)
class Chord:
    """A key plus the modifiers that must be held for it to match."""

    vk: int
    ctrl: bool = False
    alt: bool = False
    shift: bool = False
    win: bool = False


def parse_chord(spec: str) -> Chord:
    """"Ctrl+Alt+F8" -> Chord(vk=0x77, ctrl=True, alt=True)."""
    parts = [p.strip() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"empty hotkey spec: {spec!r}")
    flags = {"ctrl": False, "alt": False, "shift": False, "win": False}
    for part in parts[:-1]:
        key = _MODIFIERS.get(part.upper())
        if key is None:
            raise ValueError(f"unknown modifier {part!r} in {spec!r}")
        flags[key] = True
    name = parts[-1].upper()
    vk = _VK_NAMES.get(name)
    if vk is None:
        raise ValueError(f"unknown key {parts[-1]!r} in {spec!r}")
    return Chord(vk=vk, **flags)


def _held(vk: int) -> bool:
    return bool(W.GetAsyncKeyState(vk) & 0x8000)


def _current_modifiers() -> dict[str, bool]:
    return {
        "ctrl": _held(W.VK_CONTROL),
        "alt": _held(W.VK_MENU),
        "shift": _held(W.VK_SHIFT),
        "win": _held(W.VK_LWIN) or _held(W.VK_RWIN),
    }


class HookListener:
    """Installs the keyboard hook and reports actions by name.

    `sink` is called from inside the hook callback and must not block -- pass something
    that does nothing but `queue.put_nowait`.
    """

    PTT_DOWN = "ptt_down"
    PTT_UP = "ptt_up"

    def __init__(
        self,
        ptt_key: str,
        commands: dict[str, str],
        sink: Callable[[str], None],
    ) -> None:
        """`commands` maps a chord spec ("Ctrl+Alt+Q") to an action name ("quit")."""
        self._ptt_vk = parse_chord(ptt_key).vk
        self._commands = {parse_chord(spec): name for spec, name in commands.items()}
        self._sink = sink
        self._hook = None
        # A reference to the trampoline must outlive the hook; if it is collected,
        # Windows calls into freed memory.
        self._proc = W.HOOKPROC(self._callback)
        # Keys whose press we swallowed, so their release can be swallowed too instead
        # of reaching the focused app on its own.
        self._swallowed: set[int] = set()
        # Holding a key makes Windows repeat the press many times a second. The repeats
        # are still swallowed, but only the first one is reported.
        self._ptt_down = False

    # ------------------------------------------------------------------ install
    def install(self) -> None:
        self._hook = W.SetWindowsHookExW(W.WH_KEYBOARD_LL, self._proc, None, 0)
        if not self._hook:
            W.raise_last_error("SetWindowsHookEx(WH_KEYBOARD_LL)")
        log.info("keyboard hook installed (ptt vk=0x%02X, %d commands)",
                 self._ptt_vk, len(self._commands))

    def uninstall(self) -> None:
        if self._hook:
            W.UnhookWindowsHookEx(self._hook)
            self._hook = None

    def __enter__(self) -> HookListener:
        self.install()
        return self

    def __exit__(self, *exc: object) -> None:
        self.uninstall()

    # ------------------------------------------------------------------ callback
    def _classify(self, vk: int, down: bool) -> str | None:
        if down:
            chord = Chord(vk=vk, **_current_modifiers())
            action = self._commands.get(chord)
            if action is not None:
                return action
            # Bare (or any-modifier) press of the dictation key. Checked after the
            # command table so that e.g. Ctrl+Alt+F8 stays a command, exactly as the
            # more-specific hotkey won in the AutoHotkey version.
            if vk == self._ptt_vk:
                return self.PTT_DOWN
            return None
        if vk == self._ptt_vk:
            return self.PTT_UP
        return None

    def _callback(self, ncode: int, wparam: int, lparam: int) -> int:
        try:
            if ncode == 0:  # HC_ACTION
                info = ctypes.cast(
                    lparam, ctypes.POINTER(W.KBDLLHOOKSTRUCT)
                ).contents
                if info.dwExtraInfo != INJECT_SIGNATURE:
                    down = wparam in (W.WM_KEYDOWN, W.WM_SYSKEYDOWN)
                    vk = info.vkCode
                    action = self._classify(vk, down)
                    if action is not None:
                        if down:
                            self._swallowed.add(vk)
                        else:
                            self._swallowed.discard(vk)
                        if action == self.PTT_DOWN:
                            repeat, self._ptt_down = self._ptt_down, True
                            if repeat:
                                return 1  # auto-repeat: swallow, do not report
                        elif action == self.PTT_UP:
                            self._ptt_down = False
                        self._sink(action)
                        return 1
                    if not down and vk in self._swallowed:
                        # We ate the press of this key; eat the release too.
                        self._swallowed.discard(vk)
                        return 1
        except Exception:
            # An exception must never escape into the hook chain.
            log.exception("keyboard hook callback failed")
        return W.CallNextHookEx(None, ncode, wparam, lparam)


def pump_messages() -> None:
    """Run the Win32 message loop until WM_QUIT.

    Required, and required *on the thread that installed the hook*: low-level hook
    callbacks are delivered through that thread's message queue. The OSD and the tray
    icon live on this thread too, so this single loop drives all three.
    """
    msg = W.MSG()
    while True:
        result = W.GetMessageW(ctypes.byref(msg), None, 0, 0)
        if result == 0:      # WM_QUIT
            return
        if result == -1:     # error
            W.raise_last_error("GetMessage")
        W.TranslateMessage(ctypes.byref(msg))
        W.DispatchMessageW(ctypes.byref(msg))
