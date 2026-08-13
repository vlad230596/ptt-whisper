"""Turning PortAudio's device list into a list of microphones, and the meter's maths.

No microphone is opened here: `input_devices` is replaced with the list this machine
actually reported, so the folding rules are tested against real names rather than
invented ones.
"""

from __future__ import annotations

import numpy as np
import pytest

from pushtotalk import recorder

HOST_API_ORDER = ("Windows WASAPI", "Windows DirectSound", "MME")

# One physical microphone appears once per host API, and MME truncates the name to 31
# characters. "Line 1 (Virtual Cable 1)" is a second, genuinely different device.
FAKE_DEVICES = [
    {"index": 1, "name": "Microphone (High Definition Aud", "api": "MME",
     "channels": 2, "default_samplerate": 44100.0},
    {"index": 2, "name": "Line 1 (Virtual Cable 1)", "api": "MME",
     "channels": 2, "default_samplerate": 44100.0},
    {"index": 5, "name": "Microphone (High Definition Audio Device)",
     "api": "Windows DirectSound", "channels": 2, "default_samplerate": 44100.0},
    {"index": 9, "name": "Microphone (High Definition Audio Device)",
     "api": "Windows WASAPI", "channels": 2, "default_samplerate": 44100.0},
    {"index": 12, "name": "Line 1 (Virtual Cable 1)", "api": "Windows WASAPI",
     "channels": 2, "default_samplerate": 44100.0},
]


@pytest.fixture
def fake_devices(monkeypatch):
    monkeypatch.setattr(recorder, "input_devices", lambda: list(FAKE_DEVICES))


# ------------------------------------------------------------------ folding
def test_one_microphone_is_offered_once(fake_devices) -> None:
    devices = recorder.physical_devices(HOST_API_ORDER)
    assert len(devices) == 2


def test_the_full_name_wins_over_the_mme_truncation(fake_devices) -> None:
    names = {d["name"] for d in recorder.physical_devices(HOST_API_ORDER)}
    assert "Microphone (High Definition Audio Device)" in names
    assert "Microphone (High Definition Aud" not in names


def test_the_entry_carries_the_device_that_would_be_opened(fake_devices) -> None:
    """What the chooser shows has to be the stream the app will really get, not the
    first row PortAudio happened to list."""
    mic = next(d for d in recorder.physical_devices(HOST_API_ORDER)
               if d["name"].startswith("Microphone"))
    assert (mic["api"], mic["index"]) == ("Windows WASAPI", 9)


def test_the_other_host_apis_are_kept_for_display(fake_devices) -> None:
    mic = next(d for d in recorder.physical_devices(HOST_API_ORDER)
               if d["name"].startswith("Microphone"))
    assert mic["apis"] == ["Windows WASAPI", "Windows DirectSound", "MME"]


def test_an_empty_device_list_is_not_an_error(monkeypatch) -> None:
    monkeypatch.setattr(recorder, "input_devices", list)
    assert recorder.physical_devices(HOST_API_ORDER) == []


def test_resolve_prefers_the_first_working_host_api(fake_devices) -> None:
    resolved = recorder.resolve("Microphone (High Definition Audio Device)",
                                HOST_API_ORDER)
    assert resolved["api"] == "Windows WASAPI"


def test_resolve_says_what_to_do_when_nothing_matches(fake_devices) -> None:
    with pytest.raises(recorder.DeviceNotFound, match="Ctrl\\+Alt\\+M"):
        recorder.resolve("Blue Yeti", HOST_API_ORDER)


# ------------------------------------------------------------------ names
def test_display_name_folds_a_name_containing_a_line_break() -> None:
    """A real Bluetooth headset here is called ...Hands-Free%0\\r\\n;(JBL T460BT), and a
    raw CR/LF breaks both a table row and the one-line device report."""
    raw = "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\r\n;(JBL))"
    folded = recorder.display_name(raw)
    assert "\n" not in folded and "\r" not in folded
    assert folded.endswith(";(JBL))")


def test_display_name_leaves_an_ordinary_name_alone() -> None:
    name = "Microphone (High Definition Audio Device)"
    assert recorder.display_name(name) == name


# ------------------------------------------------------------------ the recorder
def test_changing_the_microphone_takes_effect_from_the_next_capture(fake_devices) -> None:
    rec = recorder.Recorder("Microphone (High Definition Audio Device)", 16000,
                            HOST_API_ORDER)
    assert rec.resolve_device()["index"] == 9
    rec.set_mic("Line 1 (Virtual Cable 1)")
    assert rec.mic == "Line 1 (Virtual Cable 1)"
    assert rec.resolve_device()["index"] == 12


# ------------------------------------------------------------------ meter maths
def test_dbfs_of_full_scale_is_zero() -> None:
    assert recorder.dbfs(1.0) == pytest.approx(0.0)


def test_dbfs_halves_by_six_db() -> None:
    assert recorder.dbfs(0.5) == pytest.approx(-6.02, abs=0.01)


def test_dbfs_of_digital_silence_is_the_floor_not_minus_infinity() -> None:
    """-inf is true and undrawable, and silence is the meter's normal state."""
    assert recorder.dbfs(0.0, -60.0) == -60.0
    assert recorder.dbfs(-0.0) == -100.0


def test_dbfs_never_goes_below_the_floor() -> None:
    assert recorder.dbfs(1e-9, -60.0) == -60.0


def test_the_meter_reports_nothing_before_the_first_block_arrives() -> None:
    monitor = recorder.LevelMonitor("nothing", 16000, HOST_API_ORDER)
    assert monitor.read() == (0.0, 0.0)


def test_the_meter_measures_rms_and_peak_of_what_it_was_given() -> None:
    monitor = recorder.LevelMonitor("nothing", 16000, HOST_API_ORDER)
    block = np.array([[0.5], [-0.5], [0.5], [-0.5]], dtype=np.float32)
    monitor._callback(block, len(block), None, None)
    level, peak = monitor.read()
    assert level == pytest.approx(0.5)
    assert peak == pytest.approx(0.5)


def test_reading_the_meter_starts_a_fresh_window() -> None:
    """Otherwise the bar would show the loudest moment since the window opened and
    never fall back."""
    monitor = recorder.LevelMonitor("nothing", 16000, HOST_API_ORDER)
    monitor._callback(np.full((100, 1), 0.9, dtype=np.float32), 100, None, None)
    monitor.read()
    monitor._callback(np.zeros((100, 1), dtype=np.float32), 100, None, None)
    assert monitor.read() == (0.0, 0.0)


def test_a_device_that_will_not_open_is_reported_not_raised() -> None:
    """The chooser has to survive being pointed at every device in the list, including
    a Bluetooth headset that refuses to open."""
    monitor = recorder.LevelMonitor("no such microphone", 16000, HOST_API_ORDER)
    assert monitor.start() is False
    assert monitor.error and "no such microphone" in monitor.error
    monitor.stop()  # must be safe even though nothing was ever opened
