"""Wiring: hotkey -> record -> transcribe -> paste, plus the commands around it.

Threads, and why there are exactly these:

* **main** -- owns the keyboard hook, the OSD and the tray icon, and runs the one Win32
  message loop all three need. It must never block, or the hook gets dropped by Windows.
* **worker** -- one thread, so dictations are strictly serialised. Everything slow lives
  here: capture, inference, clipboard and focus work.
* **loader** -- constructs the model and warms it up, so hotkeys are live immediately.
  Pressing the key during load records straight away and only the transcription waits.
* **media** -- a single-slot executor, so pause and resume can never run out of order
  and leave your music paused forever.
"""

from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import archive, asr, config as cfg, media, micui, paste, recorder, settings, text
from . import winapi as W
from .hotkeys import HookListener, pump_messages
from .osd import StatusWindow
from .tray import MenuItem, Tray

log = logging.getLogger("pushtotalk")

_MUTEX_NAME = "Local\\PushToTalk.SingleInstance"
_ACTION_LIST_DEVICES = "list_devices"
_ACTION_TEST_MODE = "test_mode"
_ACTION_MIC_UI = "mic_ui"
_ACTION_QUIT = "quit"


class _ProgressTicker:
    """Drives the "Transcribing ..." OSD bar while `Engine.transcribe` runs.

    A real completion fraction needs a real-time factor (wall-clock seconds of decode
    per second of *speech*) to turn "N seconds elapsed" into "N% of this dictation" --
    faster-whisper does not know in advance how long a decode will take, so `rtf` is
    supplied by the caller, self-calibrated from this machine's own previous
    dictations this session (see `App._asr_rtf`). It is deliberately calibrated
    against speech seconds, not raw recording length: decode cost tracks how much was
    actually said, not how long the key was held, so a pause-heavy recording and a
    solid block of speech of the same wall-clock length cost very different amounts of
    decode time. `on_start` narrows the estimate's denominator from the recording's
    raw length down to `duration_after_vad` -- what VAD says is actually speech --
    within the first tick or two, before which the raw length is used as a rough
    placeholder. Estimated progress is capped short of 100% so it never claims to
    finish before the decode actually does; a real per-segment fraction from
    `Engine.transcribe`'s `on_progress` overrides the estimate once available, since
    it is ground truth rather than a guess. Before either exists -- no calibration
    sample yet, and no segment decoded yet -- there is nothing to show a bar for, so
    the line is a plain elapsed-time counter instead.
    """

    # Short of 100% for the reason above, not because of anything measured -- 99% is
    # just a recognisable "almost done, but not confirmed" number, the same one most
    # installers and download bars settle on.
    _ESTIMATE_CAP = 0.99

    def __init__(self, osd: StatusWindow | None, duration: float, rtf: float | None) -> None:
        self._osd = osd
        self._speech_seconds = duration
        self._rtf = rtf
        self._started = time.monotonic()
        self._segment_fraction: float | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def on_start(self, _duration: float, duration_after_vad: float) -> None:
        with self._lock:
            self._speech_seconds = duration_after_vad

    def on_progress(self, fraction: float) -> None:
        with self._lock:
            self._segment_fraction = fraction

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()

    @property
    def speech_seconds(self) -> float:
        with self._lock:
            return self._speech_seconds

    def _run(self) -> None:
        while not self._stop.wait(cfg.TRANSCRIBE_TICK_MS / 1000):
            if self._osd is None:
                continue
            elapsed = time.monotonic() - self._started
            with self._lock:
                segment_fraction = self._segment_fraction
                speech_seconds = self._speech_seconds
            estimate = None
            if self._rtf and self._rtf > 0 and speech_seconds > 0:
                estimate = min(elapsed / (speech_seconds * self._rtf), self._ESTIMATE_CAP)
            candidates = [f for f in (segment_fraction, estimate) if f is not None]
            if candidates:
                fraction = max(candidates)
                self._osd.show(f"Transcribing ... {fraction * 100:.0f}%", 0, progress=fraction)
            else:
                self._osd.show(f"Transcribing ... {elapsed:.1f}s", 0)


def setup_logging() -> None:
    log_dir = Path(cfg.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "pushtotalk.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    # Only when there is a console to write to -- under pythonw.exe there is not.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter("%(levelname)-7s %(name)s: %(message)s"))
        root.addHandler(console)


def claim_single_instance() -> bool:
    """False if another copy is already running. The handle is leaked deliberately:
    it must stay open for the lifetime of the process."""
    W.CreateMutexW(None, True, _MUTEX_NAME)
    return ctypes.get_last_error() != W.ERROR_ALREADY_EXISTS


class App:
    def __init__(self) -> None:
        self._actions: queue.Queue[str] = queue.Queue()
        self._engine = asr.Engine()
        self._model_ready = threading.Event()
        # settings.json wins over config.py; keeping the source around means the log and
        # the device report can say which file the current microphone came from.
        self._mic, self._mic_source = settings.effective_mic()
        self._recorder = recorder.Recorder(self._mic, cfg.SAMPLE_RATE, cfg.HOST_API_ORDER)
        self._media = media.MediaController(cfg.PAUSE_MEDIA_APPS)
        self._media_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="media")
        self._main_thread_id = 0
        self._osd: StatusWindow | None = None
        self._tray: Tray | None = None

        self._recording = False
        # Set while an utterance is being finished off, so a key press that lands in
        # the middle of it can be rejected at the door instead of queued.
        self._busy = threading.Event()
        self._rec_started = 0.0
        self._target_hwnd = 0
        self._rec_seq = 0
        self._test_mode = False  # show the text instead of pasting it
        # Wall-clock seconds of decode per second of *speech* (post-VAD, not raw
        # recording length -- a pause costs nothing to decode), updated after each
        # dictation this session -- what lets the OSD show a real progress bar instead
        # of a bare clock. Not persisted: it is a property of this run (thermal state,
        # what else is on the GPU right now), not a setting, and it re-calibrates
        # within the first couple of dictations of the next run anyway.
        self._asr_rtf: float | None = None

    # ------------------------------------------------------------------ status
    def _status(self, message: str, timeout_ms: int = 1500, *, error: bool = False) -> None:
        if self._osd is not None:
            self._osd.show(message, timeout_ms, error=error)
        log.log(logging.ERROR if error else logging.INFO, "status: %s", message)

    def _tray_state(self, state: str) -> None:
        if self._tray is not None:
            self._tray.set_status(f"PushToTalk — {state}", active=state == "recording")

    # ------------------------------------------------------------------ startup
    def run(self) -> int:
        W.set_dpi_aware()
        self._main_thread_id = W.GetCurrentThreadId()
        self._osd = StatusWindow()
        self._tray = Tray(
            "PushToTalk — starting",
            [
                MenuItem(f"Test mode ({cfg.HOTKEY_TEST_MODE})",
                         lambda: self._actions.put(_ACTION_TEST_MODE),
                         checked=lambda: self._test_mode),
                MenuItem(f"Choose microphone ({cfg.HOTKEY_MIC})",
                         lambda: self._actions.put(_ACTION_MIC_UI)),
                MenuItem(f"List microphones ({cfg.HOTKEY_LIST_DEVICES})",
                         lambda: self._actions.put(_ACTION_LIST_DEVICES)),
                MenuItem("-", lambda: None),
                MenuItem(f"Quit ({cfg.HOTKEY_QUIT})",
                         lambda: self._actions.put(_ACTION_QUIT)),
            ],
            idle_icon=cfg.ICON_IDLE,
            active_icon=cfg.ICON_RECORDING,
        )

        worker = threading.Thread(target=self._worker, name="worker", daemon=True)
        worker.start()
        threading.Thread(target=self._load_model, name="loader", daemon=True).start()

        listener = HookListener(
            ptt_key=cfg.HOTKEY_REC,
            commands={
                cfg.HOTKEY_LIST_DEVICES: _ACTION_LIST_DEVICES,
                cfg.HOTKEY_TEST_MODE: _ACTION_TEST_MODE,
                cfg.HOTKEY_MIC: _ACTION_MIC_UI,
                cfg.HOTKEY_QUIT: _ACTION_QUIT,
            },
            sink=self._enqueue,
        )
        log.info("microphone %r (from %s)", self._mic, self._mic_source)
        self._status(f"Loading the model — hold {cfg.HOTKEY_REC} to dictate", 2500)
        self._tray_state(f"hold {cfg.HOTKEY_REC} to dictate")
        try:
            with listener:
                pump_messages()
        finally:
            self._shutdown()
        return 0

    def _shutdown(self) -> None:
        log.info("shutting down")
        micui.request_close()  # its thread is a daemon, but its audio stream is not
        if self._recording:
            self._recorder.abort()
        self._media_pool.shutdown(wait=False)
        if self._tray is not None:
            self._tray.destroy()
        if self._osd is not None:
            self._osd.destroy()

    def _load_model(self) -> None:
        try:
            took = self._engine.load()
            if cfg.WARMUP:
                took += self._engine.warm_up()
            self._model_ready.set()
            self._status(
                f"Ready — hold {cfg.HOTKEY_REC} to dictate (model {took:.1f}s)", 3000
            )
            self._tray_state(f"hold {cfg.HOTKEY_REC} to dictate")
        except Exception as exc:
            log.exception("loading the model failed")
            self._status(f"Model failed to load: {exc}", 0, error=True)
            self._tray_state("model failed to load")

    # ------------------------------------------------------------------ worker
    def _enqueue(self, action: str) -> None:
        """Hand an action to the worker. Runs inside the hook callback: never blocks.

        A dictation key press arriving while the previous utterance is still being
        transcribed is dropped, not queued. Queuing it would look like it worked and
        then start recording once the transcription finished -- seconds after the words
        were spoken.
        """
        if action == HookListener.PTT_DOWN and self._busy.is_set():
            if self._osd is not None:
                self._osd.show("Still finishing the previous dictation ...", 1500)
            return
        self._actions.put_nowait(action)

    def _worker(self) -> None:
        handlers = {
            HookListener.PTT_DOWN: self._start_recording,
            HookListener.PTT_UP: self._finish_recording,
            _ACTION_LIST_DEVICES: self._list_devices,
            _ACTION_MIC_UI: self._choose_microphone,
            _ACTION_TEST_MODE: self._toggle_test_mode,
            _ACTION_QUIT: self._quit,
        }
        while True:
            action = self._actions.get()
            handler = handlers.get(action)
            if handler is None:
                log.warning("unknown action %r", action)
                continue
            try:
                handler()
            except Exception as exc:
                log.exception("action %r failed", action)
                self._status(f"Error: {exc}", 6000, error=True)
                self._recording = False
                self._tray_state("idle")

    # ------------------------------------------------------------------ dictation
    def _start_recording(self) -> None:
        if self._recording:
            return
        self._sync_mic()
        # Remember the destination now, while the caret is still where you are looking.
        self._target_hwnd = paste.foreground_window()
        try:
            self._recorder.start()
        except Exception as exc:
            log.exception("could not start recording")
            self._status(f"Recording failed to start: {exc}", 5000, error=True)
            return
        self._recording = True
        self._rec_started = time.monotonic()
        if cfg.PAUSE_MEDIA:
            self._media_pool.submit(self._media.pause)
        self._status("REC ...", 0)
        self._tray_state("recording")

    def _finish_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        elapsed_ms = (time.monotonic() - self._rec_started) * 1000
        self._busy.set()
        try:
            self._finish(elapsed_ms)
        finally:
            self._busy.clear()
            self._tray_state("idle")

    def _finish(self, elapsed_ms: float) -> None:
        if elapsed_ms < cfg.MIN_REC_MS:
            self._recorder.abort()
            self._resume_media()
            self._status("Too short — cancelled", 1200)
            return

        # A grace period for speech that trails the key release. Nothing is lost by
        # stopping the stream itself, so this is the only reason to wait at all.
        time.sleep(cfg.TAIL_MS / 1000)
        audio = self._recorder.stop()
        self._resume_media()

        level = recorder.rms(audio)
        if audio.size == 0 or level < cfg.MIN_RMS:
            self._status(
                f"No audio captured (rms {level:.5f}) from {self._mic} — "
                f"{cfg.HOTKEY_MIC} opens the microphone chooser", 5000, error=True,
            )
            return

        self._transcribe(audio, elapsed_ms)

    def _resume_media(self) -> None:
        if cfg.PAUSE_MEDIA:
            self._media_pool.submit(self._media.resume)

    def _transcribe(self, audio, elapsed_ms: float) -> None:
        if not self._model_ready.is_set():
            self._status("Waiting for the model to finish loading ...", 0)
            self._model_ready.wait()

        self._status("Transcribing ...", 0)
        self._tray_state("transcribing")
        duration = len(audio) / cfg.SAMPLE_RATE
        started = time.monotonic()
        ticker = _ProgressTicker(self._osd, duration, self._asr_rtf)
        try:
            segments = self._engine.transcribe(
                audio, on_start=ticker.on_start, on_progress=ticker.on_progress
            )
        finally:
            ticker.stop()
        asr_ms = (time.monotonic() - started) * 1000
        speech_seconds = ticker.speech_seconds
        if speech_seconds > 0:
            sample_rtf = (asr_ms / 1000) / speech_seconds
            self._asr_rtf = (
                sample_rtf if self._asr_rtf is None
                else 0.5 * self._asr_rtf + 0.5 * sample_rtf
            )

        clean = text.clean(segments)
        raw = text.join_segments(segments, drop_hallucinations=False)
        if not clean:
            self._status("Silence — nothing recognised", 1800)
            return

        stem = f"ptt_{datetime.now():%Y%m%d_%H%M%S}_{self._next_seq()}"
        if cfg.KEEP_AUDIO:
            archive.store(Path(cfg.DATA_DIR), stem, audio, cfg.SAMPLE_RATE, clean, raw)

        stats = (f"{len(clean)} chars | rec {elapsed_ms / 1000:.1f}s "
                 f"| asr {asr_ms / 1000:.1f}s")
        log.info("%s: %s", stats, clean)

        # Test mode separates "did not recognise" from "did not paste": the text is
        # shown rather than sent to whatever window happens to have focus.
        if self._test_mode:
            if self._osd is not None:
                self._osd.hide()  # or "Transcribing ..." stays up behind the dialog
            W.MessageBoxW(None, f"{clean}\n\n{stats}", "PushToTalk test mode",
                          W.MB_ICONINFORMATION | W.MB_TOPMOST)
            return

        outcome = paste.deliver(clean, self._target_hwnd)
        if outcome is paste.Outcome.OK:
            self._status(stats, 2500)
        elif outcome is paste.Outcome.CLIPBOARD_BUSY:
            self._status("Clipboard is locked by another app", 3000, error=True)
        else:
            self._status(f"{outcome.value} — text left in the clipboard", 5000, error=True)

    def _next_seq(self) -> int:
        # Keeps two recordings inside the same second from sharing an archive name.
        self._rec_seq += 1
        return self._rec_seq

    # ------------------------------------------------------------------ microphone
    def _sync_mic(self) -> None:
        """Pick up a choice made outside this process before recording.

        `ptt mic` run from a console writes settings.json in a process of its own, and
        the running instance would otherwise keep the old device until it was restarted.
        This is one small read on the worker thread, ahead of opening a stream that
        costs orders of magnitude more.
        """
        mic, source = settings.effective_mic()
        if mic == self._mic:
            return
        self._mic, self._mic_source = mic, source
        self._recorder.set_mic(mic)
        log.info("microphone changed to %r (from %s)", mic, source)

    def _choose_microphone(self) -> None:
        """Open the chooser on a thread of its own.

        Not on this one: the worker is the dictation thread, and running a window's
        render loop here would stop dictation working for as long as the window was up.
        """
        if micui.open_in_thread(self._mic, self._apply_mic) is None:
            self._status("The microphone window is already open", 2000)

    def _apply_mic(self, name: str) -> None:
        """Called from the chooser's thread when a device is accepted."""
        if not settings.set_mic(name):
            raise RuntimeError(f"could not write {settings.path()}")
        self._mic, self._mic_source = name, settings.SETTINGS_SOURCE
        self._recorder.set_mic(name)
        self._status(f"Microphone: {name}", 3000)

    # ------------------------------------------------------------------ commands
    def _list_devices(self) -> None:
        report = [recorder.describe_devices(), "",
                  f"microphone = {self._mic!r} (from {self._mic_source})"]
        try:
            resolved = self._recorder.resolve_device()
            report.append(
                f"  -> resolves to [{resolved['index']}] {resolved['name']} "
                f"({resolved['api']})"
            )
        except recorder.DeviceNotFound as exc:
            report.append(f"  -> NO MATCH: {exc}")
        report += ["", "Media sessions (SMTC), for PAUSE_MEDIA_APPS in config.py:", ""]
        sessions = self._media_pool.submit(self._media.sessions).result(timeout=10)
        if sessions:
            report += [
                f"  {s.app_id}\n       status={s.status} can_pause={s.can_pause}"
                for s in sessions
            ]
        else:
            report.append("  (none -- start a player and press the hotkey again)")

        path = Path(cfg.LOG_DIR) / "devices.txt"
        path.write_text("\n".join(report), encoding="utf-8")
        os.startfile(path)  # noqa: S606 -- opening our own report in the default editor
        self._status(f"Device list written to {path}", 2500)

    def _toggle_test_mode(self) -> None:
        self._test_mode = not self._test_mode
        self._status(
            "Test mode ON — text will be shown, not pasted" if self._test_mode
            else "Test mode OFF — text will be pasted", 2500,
        )

    def _quit(self) -> None:
        log.info("quit requested")
        W.PostThreadMessageW(self._main_thread_id, W.WM_QUIT, 0, 0)


def main() -> int:
    setup_logging()
    if not claim_single_instance():
        W.MessageBoxW(None, "PushToTalk is already running.", "PushToTalk",
                      W.MB_ICONINFORMATION | W.MB_TOPMOST)
        return 1
    log.info("starting up (pid %d)", os.getpid())
    try:
        return App().run()
    except Exception as exc:
        log.exception("fatal error")
        W.MessageBoxW(None, f"{type(exc).__name__}: {exc}\n\n"
                            f"See {Path(cfg.LOG_DIR) / 'pushtotalk.log'}",
                      "PushToTalk failed to start", W.MB_ICONERROR | W.MB_TOPMOST)
        return 2
