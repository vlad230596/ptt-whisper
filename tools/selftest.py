"""End-to-end check: start the app, press F8 for real, watch what it does.

This is the test the AutoHotkey version could not have. The keyboard hook ignores only
input carrying the program's own signature in dwExtraInfo, so a synthetic F8 sent from
here is indistinguishable from a physical one and drives the whole chain: hook ->
capture -> resample -> inference -> filtering.

Run it in a quiet room and keep your hands off the keyboard while it runs -- the app it
starts is the real one, so a physical F8 of your own is picked up as well and the report
gets confusing. Silence is the expected input: the run passes when the app reports
"Silence" or a transcript, and fails on a device error, a dead hook or a hang. Nothing
is pasted anywhere on the silent path.

    uv run python tools/selftest.py [--hold SECONDS]
"""

from __future__ import annotations

import argparse
import ctypes
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pushtotalk import winapi as W  # noqa: E402

VK_F8 = 0x77


def send_key(vk: int, *, up: bool) -> None:
    """A keystroke with no signature, so the app's hook treats it as physical input."""
    event = W.INPUT()
    event.type = W.INPUT_KEYBOARD
    event.ki.wVk = vk
    event.ki.wScan = W.MapVirtualKeyW(vk, 0)
    event.ki.dwFlags = W.KEYEVENTF_KEYUP if up else 0
    array = (W.INPUT * 1)(event)
    if W.SendInput(1, array, ctypes.sizeof(W.INPUT)) != 1:
        raise OSError("SendInput failed")


def reader(stream, sink: queue.Queue) -> None:
    for line in stream:
        sink.put(line.rstrip())
    sink.put(None)


def drain(sink: queue.Queue, log: list[str]) -> None:
    """Discard anything already buffered, so the next wait only sees new lines."""
    while True:
        try:
            line = sink.get_nowait()
        except queue.Empty:
            return
        if line is not None:
            log.append(line)


def wait_for(sink: queue.Queue, needle: str, timeout: float, log: list[str]) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            line = sink.get(timeout=min(1.0, max(0.1, deadline - time.monotonic())))
        except queue.Empty:
            continue
        if line is None:
            return None
        log.append(line)
        print(f"    | {line}")
        if needle in line:
            return line
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hold", type=float, default=1.5,
                        help="seconds to hold the dictation key")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    print(f"launching {sys.executable} -m pushtotalk")
    process = subprocess.Popen(
        [sys.executable, "-m", "pushtotalk"],
        cwd=root, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    sink: queue.Queue = queue.Queue()
    threading.Thread(target=reader, args=(process.stderr, sink), daemon=True).start()
    log: list[str] = []
    failures: list[str] = []

    try:
        print("[1/4] waiting for the hook to be installed")
        if not wait_for(sink, "keyboard hook installed", 30, log):
            failures.append("the keyboard hook was never installed")
            return report(failures, log)

        print("[2/4] waiting for the model (first run also downloads nothing, just loads)")
        if not wait_for(sink, "warm-up transcription", 240, log):
            failures.append("the model never finished loading and warming up")
            return report(failures, log)

        print(f"[3/4] holding F8 for {args.hold:.1f}s")
        drain(sink, log)
        send_key(VK_F8, up=False)
        time.sleep(args.hold)
        send_key(VK_F8, up=True)

        if not wait_for(sink, "recording on", 5, log):
            failures.append("the injected F8 did not start a recording")
            return report(failures, log)

        print("[4/4] waiting for the result")
        outcome = wait_for(sink, "status: ", 60, log)
        while outcome and ("REC" in outcome or "Transcribing" in outcome):
            outcome = wait_for(sink, "status: ", 60, log)
        if outcome is None:
            failures.append("no result was reported within 60 s")
        elif "No audio captured" in outcome:
            failures.append("the microphone delivered no audio -- check MIC in config.py")
        elif "Error" in outcome or "failed" in outcome:
            failures.append(f"the dictation failed: {outcome}")
    finally:
        print("stopping the app")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    return report(failures, log)


def report(failures: list[str], log: list[str]) -> int:
    print()
    if failures:
        print("FAILED")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("PASSED — hook, capture, resampling, inference and filtering all ran")
    return 0


if __name__ == "__main__":
    sys.exit(main())
