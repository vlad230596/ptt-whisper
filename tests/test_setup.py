"""Setup, the model check and the doctor's judgement.

The parts worth testing here are the ones that decide *whether something is wrong*: a
model directory that looks complete but is not, a PATH entry that would be appended
twice, and a report that must distinguish "warn" from "fail". None of it needs a GPU.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pushtotalk import doctor, model, setup


@pytest.fixture(autouse=True)
def small_weights_threshold(monkeypatch):
    """Shrink the "is this really the weights file" threshold for the whole module.

    It is a gigabyte in production. Writing a plausible `model.bin` once per test at that
    size is not a hypothetical cost: it filled the disk and failed the run. What is under
    test is a size comparison, and 1 KB exercises it exactly as well.
    """
    monkeypatch.setattr(model, "MIN_WEIGHTS_BYTES", 1024)


def _write_model(directory: Path, *, weights: int | None = None,
                 omit: tuple[str, ...] = ()) -> Path:
    if weights is None:
        weights = model.MIN_WEIGHTS_BYTES + 1
    directory.mkdir(parents=True, exist_ok=True)
    for name in model.REQUIRED:
        if name in omit:
            continue
        size = weights if name == "model.bin" else 16
        (directory / name).write_bytes(b"\0" * size)
    return directory


# ------------------------------------------------------------------ model files
def test_a_complete_directory_has_nothing_missing(tmp_path: Path) -> None:
    assert model.missing(_write_model(tmp_path / "m")) == []


def test_an_absent_directory_is_reported_not_raised(tmp_path: Path) -> None:
    gaps = model.missing(tmp_path / "never-created")
    assert len(gaps) == 1
    assert "does not exist" in gaps[0]


@pytest.mark.parametrize("name", model.REQUIRED)
def test_each_required_file_is_noticed_when_absent(tmp_path: Path, name: str) -> None:
    gaps = model.missing(_write_model(tmp_path / "m", omit=(name,)))
    assert any(gap.startswith(name) for gap in gaps)


def test_an_lfs_pointer_counts_as_missing_weights(tmp_path: Path) -> None:
    """The failure this exists for: `model.bin` present, 130 bytes, and unusable.
    faster-whisper's error for it names neither the file nor the reason."""
    directory = _write_model(tmp_path / "m", weights=130)
    gaps = model.missing(directory)
    assert any(gap.startswith("model.bin") and "130 bytes" in gap for gap in gaps)


def test_directory_is_derived_from_the_repo_name(tmp_path: Path) -> None:
    assert model.directory_for("Systran/faster-whisper-large-v3", tmp_path) == \
        tmp_path / "models" / "faster-whisper-large-v3"


def test_ensure_does_not_touch_the_network_when_the_model_is_there(tmp_path: Path) -> None:
    """`ensure` must be cheap on every run after the first -- a re-run of setup that
    re-downloaded 3 GB would be worse than useless."""
    directory = _write_model(tmp_path / "m")
    ok, message = model.ensure(directory)
    assert ok
    assert "already present" in message


# ------------------------------------------------------------------ PATH entry
def test_path_entry_is_appended_once() -> None:
    current = r"C:\Windows;C:\Windows\System32"
    updated = setup.path_with(r"C:\Software\Whisper", current)
    assert updated == current + r";C:\Software\Whisper"


@pytest.mark.parametrize("existing", [
    r"C:\Software\Whisper",
    r"c:\software\whisper",       # Windows paths are case-insensitive
    "C:\\Software\\Whisper\\",    # a trailing separator is the same directory
])
def test_an_entry_already_present_is_left_alone(existing: str) -> None:
    """Re-running setup must not grow the PATH. This is the whole point of the helper."""
    assert setup.path_with(r"C:\Software\Whisper", f"C:\\Windows;{existing}") is None


def test_an_empty_path_gets_no_leading_separator() -> None:
    assert setup.path_with(r"C:\Software\Whisper", "") == r"C:\Software\Whisper"


def test_a_trailing_separator_is_not_doubled() -> None:
    assert setup.path_with("C:\\New", "C:\\Windows;") == "C:\\Windows;C:\\New"


# ------------------------------------------------------------------ report
def test_the_report_separates_failures_from_warnings(capsys) -> None:
    report = doctor.Report()
    report.ok("fine")
    report.warn("odd")
    report.fail("broken")
    assert report.failures == ["broken"]
    assert report.warnings == ["odd"]
    # Each finding is printed as it happens: a check that loads a 3 GB model must not
    # hold its output until the end.
    assert "broken" in capsys.readouterr().out


def test_layout_check_rejects_a_path_with_a_space(tmp_path: Path) -> None:
    """A space breaks the quoting of the autostart command; it must fail, not warn."""
    directory = tmp_path / "with space"
    (directory / ".venv" / "Scripts").mkdir(parents=True)
    (directory / ".venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
    report = doctor.Report()
    doctor.check_environment(report, directory)
    assert any("contains a space" in failure for failure in report.failures)


def test_layout_check_rejects_onedrive(tmp_path: Path) -> None:
    directory = tmp_path / "OneDrive" / "Whisper"
    (directory / ".venv" / "Scripts").mkdir(parents=True)
    (directory / ".venv" / "Scripts" / "pythonw.exe").write_bytes(b"")
    report = doctor.Report()
    doctor.check_environment(report, directory)
    assert any("OneDrive" in failure for failure in report.failures)


def test_layout_check_notices_a_missing_venv(tmp_path: Path) -> None:
    report = doctor.Report()
    doctor.check_environment(report, tmp_path)
    assert any("uv sync" in failure for failure in report.failures)


def test_inference_check_is_skipped_when_something_is_already_broken() -> None:
    """Loading the model takes seconds; there is no point spending them to confirm a
    failure that has already been diagnosed."""
    report = doctor.Report()
    report.fail("no model")
    doctor.check_inference(report)
    assert report.failures == ["no model"]
    assert any("skipped" in warning for warning in report.warnings)
