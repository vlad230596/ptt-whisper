"""Dispatch rules that decide whether a key press turns into a dictation at all, and
where the app gets its microphone from."""

from __future__ import annotations

import pytest

from pushtotalk import config as cfg
from pushtotalk import settings
from pushtotalk.app import App
from pushtotalk.hotkeys import HookListener


@pytest.fixture
def app() -> App:
    # App.__init__ only builds objects; nothing opens a device or a window until run().
    return App()


@pytest.fixture
def app_with_settings(tmp_path, monkeypatch) -> App:
    """An app whose settings.json is a scratch file -- the suite must never repoint the
    microphone this machine actually dictates with."""
    monkeypatch.setattr(cfg, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    return App()


def test_press_is_queued_when_idle(app: App) -> None:
    app._enqueue(HookListener.PTT_DOWN)
    assert app._actions.get_nowait() == HookListener.PTT_DOWN


def test_press_is_dropped_while_the_previous_utterance_finishes(app: App) -> None:
    """Queuing it instead would start recording after the words were already spoken."""
    app._busy.set()
    app._enqueue(HookListener.PTT_DOWN)
    assert app._actions.empty()


def test_release_is_never_dropped(app: App) -> None:
    """Dropping a release would strand the recording, running until the app is killed."""
    app._busy.set()
    app._enqueue(HookListener.PTT_UP)
    assert app._actions.get_nowait() == HookListener.PTT_UP


def test_commands_still_work_while_busy(app: App) -> None:
    app._busy.set()
    app._enqueue("quit")
    assert app._actions.get_nowait() == "quit"


def test_sequence_numbers_do_not_repeat(app: App) -> None:
    """Two utterances inside the same second must not share an archive name."""
    assert [app._next_seq() for _ in range(3)] == [1, 2, 3]


# ------------------------------------------------------------------ microphone
def test_the_microphone_starts_from_the_config_default(app_with_settings: App) -> None:
    app = app_with_settings
    assert (app._mic, app._mic_source) == (cfg.MIC, settings.CONFIG_SOURCE)
    assert app._recorder.mic == cfg.MIC


def test_accepting_a_choice_saves_it_and_repoints_the_recorder(
    app_with_settings: App,
) -> None:
    app = app_with_settings
    app._apply_mic("Yeti Stereo Microphone")
    assert app._recorder.mic == "Yeti Stereo Microphone"
    assert app._mic_source == settings.SETTINGS_SOURCE
    assert settings.effective_mic()[0] == "Yeti Stereo Microphone"


def test_a_choice_made_in_another_process_is_picked_up_before_recording(
    app_with_settings: App,
) -> None:
    """`ptt mic` from a console writes settings.json in a process of its own; the
    running instance must not keep the old device until it is restarted."""
    app = app_with_settings
    settings.set_mic("Line 1 (Virtual Cable 1)")
    app._sync_mic()
    assert app._mic == "Line 1 (Virtual Cable 1)"
    assert app._recorder.mic == "Line 1 (Virtual Cable 1)"


def test_syncing_with_nothing_changed_leaves_the_recorder_alone(
    app_with_settings: App, monkeypatch
) -> None:
    """This runs on every key-down, so it must be a no-op in the normal case."""
    app = app_with_settings

    def fail(_name: str) -> None:
        raise AssertionError("the recorder was repointed for no reason")

    monkeypatch.setattr(app._recorder, "set_mic", fail)
    app._sync_mic()
    assert app._mic == cfg.MIC


def test_an_unwritable_settings_file_surfaces_as_an_error(
    app_with_settings: App, monkeypatch
) -> None:
    """The chooser shows what comes out of here in its own window, so it has to raise
    rather than report success on a save that did not happen."""
    app = app_with_settings
    monkeypatch.setattr(settings, "set_mic", lambda name: False)
    with pytest.raises(RuntimeError):
        app._apply_mic("Yeti Stereo Microphone")
