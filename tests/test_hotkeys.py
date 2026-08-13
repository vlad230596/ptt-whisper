"""Hotkey parsing and the hook's classification rules.

The hook callback is tested directly rather than through Windows: it is a pure function
of (vk, direction, modifier state), and `_classify` is where the precedence rule that
matters lives -- Ctrl+Alt+F8 must stay a command and not start a dictation.
"""

from __future__ import annotations

import pytest

from pushtotalk.hotkeys import Chord, HookListener, parse_chord

VK_F8 = 0x77
VK_T = ord("T")


def test_parse_plain_key() -> None:
    assert parse_chord("F8") == Chord(vk=VK_F8)


def test_parse_chord_with_modifiers() -> None:
    assert parse_chord("Ctrl+Alt+F8") == Chord(vk=VK_F8, ctrl=True, alt=True)


def test_parse_is_case_insensitive_and_ignores_spacing() -> None:
    assert parse_chord("ctrl + shift + q") == Chord(vk=ord("Q"), ctrl=True, shift=True)


def test_parse_win_and_punctuation_keys() -> None:
    assert parse_chord("Win+.") == Chord(vk=0xBE, win=True)


@pytest.mark.parametrize("spec", ["", "Ctrl+", "Meta+F8", "Ctrl+Alt+NotAKey"])
def test_parse_rejects_nonsense(spec: str) -> None:
    with pytest.raises(ValueError):
        parse_chord(spec)


def _listener(modifiers: dict[str, bool]) -> HookListener:
    listener = HookListener(
        ptt_key="F8",
        commands={"Ctrl+Alt+F8": "list_devices", "Ctrl+Alt+T": "test_mode"},
        sink=lambda _action: None,
    )
    # _classify reads the live modifier state; pin it for the test.
    import pushtotalk.hotkeys as module

    module._current_modifiers = lambda: modifiers  # type: ignore[assignment]
    return listener


NO_MODS = {"ctrl": False, "alt": False, "shift": False, "win": False}
CTRL_ALT = {"ctrl": True, "alt": True, "shift": False, "win": False}


def test_bare_key_starts_dictation() -> None:
    listener = _listener(NO_MODS)
    assert listener._classify(VK_F8, down=True) == HookListener.PTT_DOWN
    assert listener._classify(VK_F8, down=False) == HookListener.PTT_UP


def test_command_chord_wins_over_dictation_on_the_same_key() -> None:
    listener = _listener(CTRL_ALT)
    assert listener._classify(VK_F8, down=True) == "list_devices"


def test_command_chord_on_another_key() -> None:
    listener = _listener(CTRL_ALT)
    assert listener._classify(VK_T, down=True) == "test_mode"
    # Releasing a command key is not an action of its own.
    assert listener._classify(VK_T, down=False) is None


def test_unrelated_keys_are_ignored() -> None:
    listener = _listener(NO_MODS)
    assert listener._classify(ord("A"), down=True) is None
    assert listener._classify(ord("A"), down=False) is None


def test_release_of_dictation_key_reports_even_with_modifiers_held() -> None:
    """Ctrl+Alt+F8 while recording must still end the recording, not strand it."""
    listener = _listener(CTRL_ALT)
    assert listener._classify(VK_F8, down=False) == HookListener.PTT_UP
