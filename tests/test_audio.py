"""Audio handling that does not need a microphone: resampling and mp3 archiving."""

from __future__ import annotations

import numpy as np
import pytest

from pushtotalk import archive, recorder


def _tone(seconds: float, rate: int, hz: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.25 * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_rms_of_silence_is_zero() -> None:
    assert recorder.rms(np.zeros(1000, dtype=np.float32)) == 0.0
    assert recorder.rms(np.zeros(0, dtype=np.float32)) == 0.0


def test_rms_of_a_tone() -> None:
    # RMS of a sine of amplitude a is a / sqrt(2).
    assert recorder.rms(_tone(1.0, 16000)) == pytest.approx(0.25 / np.sqrt(2), rel=1e-3)


def test_resample_preserves_duration_and_level() -> None:
    """44.1 kHz is what WASAPI actually hands over on this machine."""
    source = _tone(1.0, 44100)
    out = recorder._resample(source, 44100, 16000)
    assert out.dtype == np.float32
    assert len(out) == pytest.approx(16000, abs=200)
    assert recorder.rms(out) == pytest.approx(recorder.rms(source), rel=0.05)


def test_resample_is_a_noop_free_path_for_equal_rates() -> None:
    source = _tone(0.5, 16000)
    out = recorder._resample(source, 16000, 16000)
    assert len(out) == pytest.approx(len(source), abs=64)


def test_mp3_round_trip(tmp_path) -> None:
    from faster_whisper.audio import decode_audio

    path = tmp_path / "sample.mp3"
    source = _tone(2.0, 16000)
    archive.encode_mp3(path, source, 16000)
    assert path.stat().st_size > 1000

    decoded = decode_audio(str(path), sampling_rate=16000)
    assert len(decoded) == pytest.approx(len(source), rel=0.05)
    assert recorder.rms(decoded) == pytest.approx(recorder.rms(source), rel=0.1)


def test_store_writes_the_pair_and_skips_raw_when_unchanged(tmp_path) -> None:
    audio = _tone(0.5, 16000)
    archive.store(tmp_path, "ptt_test_1", audio, 16000, "текст", "текст")
    assert (tmp_path / "ptt_test_1.mp3").is_file()
    assert (tmp_path / "ptt_test_1.txt").read_text(encoding="utf-8") == "текст\n"
    assert not (tmp_path / "ptt_test_1.raw.txt").exists()


def test_store_writes_raw_when_filtering_changed_the_text(tmp_path) -> None:
    audio = _tone(0.5, 16000)
    archive.store(tmp_path, "ptt_test_2", audio, 16000,
                  "текст", "текст\nПродолжение следует...")
    assert (tmp_path / "ptt_test_2.raw.txt").is_file()


def test_txt_has_no_bom(tmp_path) -> None:
    """The archive is batch-processed later; a BOM would show up as a stray character."""
    archive.store(tmp_path, "ptt_test_3", _tone(0.2, 16000), 16000, "текст", "текст")
    assert (tmp_path / "ptt_test_3.txt").read_bytes()[:3] != b"\xef\xbb\xbf"


def test_device_name_matching_tolerates_mme_truncation() -> None:
    wanted = "Microphone (High Definition Audio Device)"
    assert recorder._matches(wanted, wanted)
    # MME truncates device names to 31 characters.
    assert recorder._matches("Microphone (High Definition Aud", wanted)
    assert not recorder._matches("Line 1 (Virtual Audio Cable)", wanted)
