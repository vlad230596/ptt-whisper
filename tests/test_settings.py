"""settings.json: the one file the app writes, and its precedence over config.py.

Every test points `SETTINGS_FILE` at a tmp_path. None of them may touch the real
settings file -- running the suite must not change which microphone the machine
dictates from.
"""

from __future__ import annotations

import json

import pytest

from pushtotalk import config as cfg
from pushtotalk import settings


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setattr(cfg, "SETTINGS_FILE", str(path))
    return path


# ------------------------------------------------------------------ precedence
def test_without_a_file_the_config_default_is_in_force(settings_file) -> None:
    assert not settings_file.exists()
    assert settings.effective_mic() == (cfg.MIC, settings.CONFIG_SOURCE)


def test_a_saved_choice_wins_over_config(settings_file) -> None:
    assert settings.set_mic("Yeti Stereo Microphone")
    assert settings.effective_mic() == ("Yeti Stereo Microphone",
                                        settings.SETTINGS_SOURCE)


def test_clearing_the_choice_goes_back_to_config(settings_file) -> None:
    settings.set_mic("Yeti Stereo Microphone")
    assert settings.clear_mic()
    assert settings.effective_mic() == (cfg.MIC, settings.CONFIG_SOURCE)


def test_clearing_when_nothing_was_chosen_is_not_an_error(settings_file) -> None:
    assert settings.clear_mic()


def test_the_name_is_stored_exactly_as_windows_reports_it(settings_file) -> None:
    """Device names are matched as substrings of what PortAudio returns, so a name
    mangled on the way in stops matching. One real device on this machine has a CR/LF
    in the middle of its name."""
    name = "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\r\n;(JBL))"
    settings.set_mic(name)
    assert json.loads(settings_file.read_text(encoding="utf-8"))["mic"] == name
    assert settings.effective_mic()[0] == name


# ------------------------------------------------------------------ robustness
@pytest.mark.parametrize(
    "content",
    ["", "   \n", "{not json", "[1, 2, 3]", '"a string"', "null"],
    ids=["empty", "blank", "truncated", "list", "string", "null"],
)
def test_an_unusable_file_degrades_to_the_config_default(settings_file, content) -> None:
    """A settings file that cannot be parsed must not stop dictation from starting --
    it only ever held a preference."""
    settings_file.write_text(content, encoding="utf-8")
    assert settings.effective_mic() == (cfg.MIC, settings.CONFIG_SOURCE)


@pytest.mark.parametrize("value", ["", "   ", 42, None, ["a"]],
                         ids=["empty", "blank", "number", "null", "list"])
def test_a_meaningless_mic_value_is_ignored(settings_file, value) -> None:
    """An empty name is the dangerous one: as a substring it matches *every* device,
    so it would silently resolve to whichever input happens to be first."""
    settings_file.write_text(json.dumps({"mic": value}), encoding="utf-8")
    assert settings.effective_mic() == (cfg.MIC, settings.CONFIG_SOURCE)


def test_surrounding_whitespace_is_trimmed(settings_file) -> None:
    settings_file.write_text(json.dumps({"mic": "  Yeti  "}), encoding="utf-8")
    assert settings.effective_mic()[0] == "Yeti"


def test_an_empty_name_is_refused_at_the_door(settings_file) -> None:
    with pytest.raises(ValueError):
        settings.set_mic("   ")


# ------------------------------------------------------------------ writing
def test_saving_keeps_keys_this_version_does_not_know(settings_file) -> None:
    """A key written by a newer build, or by hand, must survive a save from here."""
    settings_file.write_text(json.dumps({"mic": "Old", "future_option": 7}),
                             encoding="utf-8")
    settings.set_mic("New")
    stored = json.loads(settings_file.read_text(encoding="utf-8"))
    assert stored == {"mic": "New", "future_option": 7}


def test_the_file_is_utf8_without_a_bom_and_readable(settings_file) -> None:
    settings.set_mic("Микрофон (Realtek)")
    raw = settings_file.read_bytes()
    assert raw[:3] != b"\xef\xbb\xbf"
    assert "Микрофон" in raw.decode("utf-8")


def test_no_temporary_files_are_left_behind(settings_file) -> None:
    """The write goes through a temp file and os.replace; a leftover would mean the
    rename did not happen and the settings were not actually saved."""
    settings.set_mic("Yeti")
    assert [p.name for p in settings_file.parent.iterdir()] == ["settings.json"]


def test_an_unwritable_destination_is_reported_not_raised(settings_file) -> None:
    """The chooser shows this in the window; raising would take the app down instead."""
    settings_file.parent.joinpath("blocked").write_text("not a directory")
    assert settings.save({"mic": "Yeti"},
                         settings_file.parent / "blocked" / "settings.json") is False
