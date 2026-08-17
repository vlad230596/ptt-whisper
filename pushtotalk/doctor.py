"""Check that this machine can actually run the app, and say precisely what is wrong.

    ptt doctor

The checks are the ones a fresh machine fails, in the order the app itself hits them, and
each reports what it found rather than just pass/fail -- a driver version, a device name,
the seconds the model took -- because on this stack the number is usually the diagnosis.

The GPU check is deliberately a real transcription rather than a "is CUDA available"
probe. CTranslate2 resolves cuBLAS at the moment of the first encoder call, not at
construction, so a model that loads without complaint still fails on the first utterance
if the CUDA libraries are missing (see cudalibs.py). Only decoding something proves it.

Checks are ordinary functions returning findings, so `setup` can run the same ones and
the whole thing is testable without a GPU present.
"""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config as cfg
from . import model

# Blackwell (RTX 50xx) needs the r570 driver branch or newer; earlier ones have no
# sm_120 kernels and the first encode fails with CUBLAS_STATUS_NOT_SUPPORTED.
MIN_DRIVER = 570

OK = "ok"
WARN = "warn"
FAIL = "FAIL"


@dataclass
class Report:
    findings: list[tuple[str, str]] = field(default_factory=list)

    def ok(self, message: str) -> None:
        self._add(OK, message)

    def warn(self, message: str) -> None:
        self._add(WARN, message)

    def fail(self, message: str) -> None:
        self._add(FAIL, message)

    def _add(self, level: str, message: str) -> None:
        self.findings.append((level, message))
        print(f"  [{level:^4}] {message}")

    @property
    def failures(self) -> list[str]:
        return [m for level, m in self.findings if level == FAIL]

    @property
    def warnings(self) -> list[str]:
        return [m for level, m in self.findings if level == WARN]


# ------------------------------------------------------------------ 1. layout
def check_environment(report: Report, root: Path) -> None:
    report.ok(f"python {sys.version.split()[0]} at {sys.executable}")
    report.ok(f"project root {root}")
    # Both are project constraints, not preferences: a space breaks the quoting of the
    # autostart command and the .cmd launchers, and OneDrive rewrites files underneath a
    # running process.
    if " " in str(root):
        report.fail(f"the project path contains a space: {root} -- move the directory "
                    f"somewhere like C:\\Tools\\ptt-whisper")
    if "onedrive" in str(root).lower():
        report.fail(f"the project is inside OneDrive: {root} -- move it outside the "
                    f"synced tree")
    interpreter = root / ".venv" / "Scripts" / "pythonw.exe"
    if interpreter.is_file():
        report.ok(f"pythonw for the background instance: {interpreter}")
    else:
        report.fail(f"{interpreter} is missing -- run `uv sync` in {root}")


# ------------------------------------------------------------------ 2. packages
def check_packages(report: Report) -> None:
    for module, label in (
        ("faster_whisper", "faster-whisper"),
        ("ctranslate2", "ctranslate2"),
        ("av", "PyAV (mp3 archiving and resampling)"),
        ("sounddevice", "sounddevice (capture)"),
        ("numpy", "numpy"),
        ("winrt.windows.media.control", "winrt (pausing media over SMTC)"),
    ):
        try:
            imported = __import__(module, fromlist=["__version__"])
        except ImportError as exc:
            report.fail(f"{label}: {exc}")
            continue
        report.ok(f"{label} {getattr(imported, '__version__', '')}".rstrip())


# ------------------------------------------------------------------ 3. gpu
def check_gpu(report: Report) -> None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.warn(f"nvidia-smi did not run ({exc}); DEVICE in config.py is 'cuda' on "
                    f"purpose, so without an NVIDIA GPU the next check fails loudly "
                    f"rather than falling back to a slow CPU path")
        return
    if result.returncode != 0:
        report.warn(f"nvidia-smi exited {result.returncode}: {result.stderr.strip()}")
        return
    line = result.stdout.strip().splitlines()[0]
    report.ok(line)
    try:
        major = int(line.split(",")[1].strip().split(".")[0])
    except (IndexError, ValueError):
        return
    if major < MIN_DRIVER:
        report.warn(f"driver branch r{major} -- fine on Ada and earlier, but an "
                    f"RTX 50xx needs r{MIN_DRIVER}+ for sm_120 kernels")


# ------------------------------------------------------------------ 4. model
def check_model(report: Report) -> None:
    directory = Path(cfg.MODEL_DIR)
    gaps = model.missing(directory)
    if gaps:
        report.fail(f"model incomplete: {', '.join(gaps)} -- run `ptt setup` or "
                    f"`ptt setup --model-only`")
        return
    report.ok(f"{directory} ({model.size_gb(directory):.2f} GB)")


# ------------------------------------------------------------------ 5. inference
def check_inference(report: Report) -> None:
    """Load the model and decode. Slow, and the check that actually proves the install."""
    if report.failures:
        report.warn("skipped -- fix the failures above first")
        return
    from .asr import Engine

    engine = Engine()
    try:
        took = engine.load()
    except Exception as exc:  # noqa: BLE001 -- whatever it is, it is the answer
        report.fail(f"the model would not load ({type(exc).__name__}: {exc})")
        return
    report.ok(f"loaded {engine.compute_type} (from {engine.compute_type_source}) "
              f"on {cfg.DEVICE} in {took:.1f} s")
    report.ok(f"batching={engine.batching} (from {engine.batching_source})")
    started = time.monotonic()
    try:
        engine.warm_up()
    except Exception as exc:  # noqa: BLE001
        report.fail(f"decoding failed ({type(exc).__name__}: {exc}); "
                    f"CUBLAS_STATUS_NOT_SUPPORTED here usually means compute_type="
                    f"{engine.compute_type!r} has no kernel on this GPU -- float16 is "
                    f"the default that works everywhere tried so far (see CLAUDE.md, "
                    f"'float16 is mandatory, int8 is broken'); `ptt doctor` reports "
                    f"where the current value came from above")
        return
    report.ok(f"decoded 0.3 s of silence in {time.monotonic() - started:.1f} s "
              f"-- the whole CUDA path works")


# ------------------------------------------------------------------ 6. hardware
def check_microphone(report: Report) -> None:
    from . import recorder, settings

    try:
        devices = recorder.input_devices()
    except Exception as exc:  # noqa: BLE001
        report.fail(f"could not enumerate input devices ({type(exc).__name__}: {exc})")
        return
    if not devices:
        report.fail("no input devices at all -- is a microphone plugged in and enabled?")
        return
    mic, source = settings.effective_mic()
    try:
        resolved = recorder.Recorder(
            mic, cfg.SAMPLE_RATE, cfg.HOST_API_ORDER
        ).resolve_device()
    except recorder.DeviceNotFound:
        names = "\n".join(f"             {d['name']}" for d in devices)
        # Worth spelling out: an HD Audio input endpoint disappears from the list
        # entirely when nothing is in the socket, which presents exactly like a wrong
        # setting but is fixed with a cable rather than an edit.
        report.fail(f"microphone = {mic!r} (from {source}) matches none of these:\n"
                    f"{names}\n"
                    f"           run `ptt mic` to choose one, or plug the microphone in "
                    f"if it should be there")
    else:
        report.ok(f"microphone (from {source}) resolves to [{resolved['index']}] "
                  f"{resolved['name']} ({resolved['api']})")


def check_icons(report: Report) -> None:
    for label, path in (("idle icon", cfg.ICON_IDLE),
                        ("recording icon", cfg.ICON_RECORDING)):
        if Path(path).is_file():
            report.ok(f"{label}: {path}")
        else:
            report.warn(f"{label} missing: {path} -- run "
                        f"`uv run python tools/make_icon.py`; the tray falls back to the "
                        f"stock Windows icon meanwhile")


# ------------------------------------------------------------------ driver
STEPS = (
    ("interpreter and layout", lambda r, root: check_environment(r, root)),
    ("python packages", lambda r, root: check_packages(r)),
    ("gpu and driver", lambda r, root: check_gpu(r)),
    ("model files", lambda r, root: check_model(r)),
    ("loading the model and decoding on the gpu", lambda r, root: check_inference(r)),
    ("microphone and icons", lambda r, root: (check_microphone(r), check_icons(r))),
)


def run(root: Path, *, skip_inference: bool = False) -> Report:
    """Every check, printed as it goes. Returns the report for the caller to judge."""
    report = Report()
    for index, (title, step) in enumerate(STEPS, start=1):
        if skip_inference and "decoding" in title:
            continue
        print(f"[{index}/{len(STEPS)}] {title}")
        step(report, root)
        print()
    return report


def main(root: Path) -> int:
    print(f"pushtotalk doctor -- {root}\n")
    report = run(root)
    if report.failures:
        print(f"NOT READY -- {len(report.failures)} problem(s):")
        for failure in report.failures:
            print(f"  - {failure.splitlines()[0]}")
        return 1
    if report.warnings:
        print(f"READY, with {len(report.warnings)} warning(s). Hold "
              f"{cfg.HOTKEY_REC} and speak.")
        return 0
    print(f"READY -- everything checks out. Hold {cfg.HOTKEY_REC} and speak.")
    return 0
