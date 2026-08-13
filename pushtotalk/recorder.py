"""Microphone capture, in-process.

The AutoHotkey version spawned ffmpeg on a DirectShow device and stopped it with
`taskkill /F`, which threw away roughly 0.46 s of buffered audio every time -- the
reason `-flush_packets 1` and a 400 ms tail were both needed. Capturing here means the
stream simply stops: nothing is lost, there is no process to kill and no file to wait
for a handle on.

Sample-rate negotiation is not optional. WASAPI in shared mode refuses a rate the
endpoint is not configured for -- measured on this machine: 16 kHz is rejected on the
WASAPI device (endpoint runs at 44.1 kHz) and accepted on DirectSound and MME. So the
requested rate is tried first and, if refused, the device's own rate is used and the
result is resampled with swresample (via PyAV) instead of relying on the driver.
"""

from __future__ import annotations

import logging
import math
import threading

import av
import numpy as np
import sounddevice as sd

from . import winapi as W

log = logging.getLogger(__name__)


class DeviceNotFound(RuntimeError):
    pass


def input_devices() -> list[dict]:
    """Every input device, annotated with its host API name."""
    out = []
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            out.append(
                {
                    "index": index,
                    "name": device["name"],
                    "api": sd.query_hostapis(device["hostapi"])["name"],
                    "channels": device["max_input_channels"],
                    "default_samplerate": device["default_samplerate"],
                }
            )
    return out


def display_name(name: str) -> str:
    """The device name on one line.

    Not cosmetic: a Bluetooth headset on this machine is called
    "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0\\r\\n;(JBL T460BT))"
    -- with a real CR/LF in the middle, which breaks a table row and a one-line report.
    Only the *display* is folded; what gets stored and matched is the exact name Windows
    reports, or the substring match against it would stop working.
    """
    return " ".join(name.split())


def describe_devices() -> str:
    lines = ["Input devices (the microphone setting matches on a substring):", ""]
    for device in input_devices():
        lines.append(
            f"  [{device['index']:>2}] {display_name(device['name'])}\n"
            f"       api={device['api']}  channels={device['channels']}  "
            f"default={device['default_samplerate']:.0f} Hz"
        )
    return "\n".join(lines)


def _matches(device_name: str, wanted: str) -> bool:
    """Substring match, tolerant of MME's 31-character name truncation.

    MME reports "Microphone (High Definition Aud" for the device DirectShow and WASAPI
    call "Microphone (High Definition Audio Device)", so a plain `in` test in one
    direction is not enough.
    """
    a, b = device_name.lower(), wanted.lower()
    return b in a or a in b


def _rank(api: str, host_api_order: tuple[str, ...]) -> int:
    try:
        return host_api_order.index(api)
    except ValueError:
        return len(host_api_order)


def resolve(mic: str, host_api_order: tuple[str, ...]) -> dict:
    """The device this program will actually open for `mic`.

    Several entries can match one name -- the same microphone is exposed once per host
    API -- so the winner is the one highest in `host_api_order`.
    """
    candidates = [d for d in input_devices() if _matches(d["name"], mic)]
    if not candidates:
        raise DeviceNotFound(
            f"no input device matches {mic!r} "
            f"(Ctrl+Alt+M chooses one, Ctrl+Alt+F8 lists what is available)"
        )
    return min(candidates, key=lambda device: _rank(device["api"], host_api_order))


def physical_devices(host_api_order: tuple[str, ...]) -> list[dict]:
    """One entry per *microphone*, not one per PortAudio device.

    The same jack shows up three times -- WASAPI, DirectSound and MME -- and offering
    all three in a chooser would be offering the same microphone three times. The host
    API is not the user's decision anyway: `resolve()` picks it by `host_api_order`, so
    what is stored is a name and the API follows from it.

    Entries are folded together with the same tolerant match `resolve()` uses, so MME's
    truncated "Microphone (High Definition Aud" lands on the same microphone as the full
    name -- and the longest spelling seen is the one shown. The known cost of that
    tolerance: two devices whose names are a prefix of one another would fold into one.
    """
    groups: list[dict] = []
    for device in sorted(input_devices(), key=lambda d: (-len(d["name"]), d["index"])):
        for group in groups:
            if _matches(device["name"], group["name"]):
                group["apis"].append(device["api"])
                break
        else:
            groups.append({"name": device["name"], "apis": [device["api"]]})

    for group in groups:
        # The device that would really be opened, so the chooser shows the rate and
        # channel count of the stream the app is going to get.
        chosen = resolve(group["name"], host_api_order)
        group |= {
            "index": chosen["index"],
            "api": chosen["api"],
            "channels": chosen["channels"],
            "default_samplerate": chosen["default_samplerate"],
            "apis": sorted(set(group["apis"]),
                           key=lambda api: _rank(api, host_api_order)),
        }
    return sorted(groups, key=lambda g: (_rank(g["api"], host_api_order), g["name"]))


def negotiate(device: dict, target_rate: int) -> tuple[int, int]:
    """Return (samplerate, channels) the device will actually accept.

    WASAPI in shared mode refuses a rate the endpoint is not configured for, so the
    requested rate is tried first and the device's own rate is the fallback; the caller
    resamples what it gets.
    """
    rates = [target_rate, int(device["default_samplerate"])]
    channel_options = [1, min(2, device["channels"])]
    for rate in rates:
        for channels in channel_options:
            try:
                sd.check_input_settings(
                    device=device["index"], channels=channels,
                    samplerate=rate, dtype="float32",
                )
            except Exception:
                continue
            return rate, channels
    raise DeviceNotFound(
        f"{device['name']!r} ({device['api']}) accepted neither "
        f"{target_rate} Hz nor its own {device['default_samplerate']:.0f} Hz"
    )


class Recorder:
    """One microphone, opened on demand. Not reentrant: one recording at a time."""

    def __init__(self, mic: str, sample_rate: int, host_api_order: tuple[str, ...]) -> None:
        self._mic = mic
        self._target_rate = sample_rate
        self._host_api_order = host_api_order
        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._overflows = 0
        self._device_rate = sample_rate
        self._channels = 1

    # ------------------------------------------------------------------ device
    @property
    def mic(self) -> str:
        return self._mic

    def set_mic(self, mic: str) -> None:
        """Point at another microphone from the next capture on.

        Deliberately safe to call at any time: the device is resolved inside `start()`,
        so this cannot disturb a capture that is already running -- the utterance being
        recorded finishes on the device it started on.
        """
        self._mic = mic

    def resolve_device(self) -> dict:
        return resolve(self._mic, self._host_api_order)

    def _negotiate(self, device: dict) -> tuple[int, int]:
        return negotiate(device, self._target_rate)

    # ------------------------------------------------------------------ capture
    def _callback(self, indata, frames, time_info, status) -> None:
        # Runs on the PortAudio thread. Copy and return -- anything slow here shows up
        # as dropped input.
        if status:
            self._overflows += 1
        with self._lock:
            self._blocks.append(indata.copy())

    def start(self) -> None:
        # WASAPI is COM-based and PortAudio leaves the apartment to the caller, so a
        # capture started from a worker thread fails without this. See ensure_com().
        W.ensure_com()
        device = self.resolve_device()
        rate, channels = self._negotiate(device)
        self._device_rate, self._channels = rate, channels
        self._blocks = []
        self._overflows = 0
        self._stream = sd.InputStream(
            device=device["index"], channels=channels, samplerate=rate,
            dtype="float32", callback=self._callback,
        )
        self._stream.start()
        log.info("recording on [%d] %s (%s) at %d Hz, %d ch",
                 device["index"], device["name"], device["api"], rate, channels)

    def stop(self) -> np.ndarray:
        """Stop and return the capture as float32 mono at the configured rate."""
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        with self._lock:
            blocks, self._blocks = self._blocks, []
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        if self._overflows:
            log.warning("%d input overflow(s) during capture", self._overflows)

        audio = np.concatenate(blocks, axis=0)
        if audio.ndim > 1 and audio.shape[1] > 1:
            audio = audio.mean(axis=1)
        audio = np.ascontiguousarray(audio.reshape(-1), dtype=np.float32)
        if self._device_rate != self._target_rate:
            audio = _resample(audio, self._device_rate, self._target_rate)
        return audio

    def abort(self) -> None:
        """Discard a recording in progress without returning the audio."""
        try:
            self.stop()
        except Exception:
            log.exception("aborting the capture failed")


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """High-quality rate conversion through swresample."""
    resampler = av.AudioResampler(format="flt", layout="mono", rate=target_rate)
    frame = av.AudioFrame.from_ndarray(audio.reshape(1, -1), format="flt", layout="mono")
    frame.sample_rate = source_rate
    frame.pts = 0
    chunks = [f.to_ndarray().reshape(-1) for f in resampler.resample(frame)]
    chunks += [f.to_ndarray().reshape(-1) for f in resampler.resample(None)]
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32, copy=False)


def rms(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(audio, dtype=np.float64))))


def dbfs(level: float, floor_db: float = -100.0) -> float:
    """A 0..1 amplitude as dBFS, with digital silence pinned to `floor_db`.

    Straight 20*log10 returns -inf for a block of zeros, which is true and useless: it
    cannot be drawn, compared or formatted. Silence is the normal state of a level meter
    between words, so the floor is part of the measurement rather than a special case at
    every call site.
    """
    if level <= 0.0:
        return floor_db
    return max(floor_db, 20.0 * math.log10(level))


class LevelMonitor:
    """An input stream opened purely to watch the needle move.

    Separate from `Recorder` on purpose: it keeps no audio (only the loudest sample and
    the running sum of squares since the last read), it survives a device that refuses
    to open by reporting `error` instead of raising, and it can therefore be pointed at
    every device in a list one after another while someone clicks through them.

    Opening a second stream on a microphone that is already recording is fine on WASAPI
    shared mode -- which is what makes it safe to leave the chooser open while dictating.
    """

    def __init__(self, mic: str, sample_rate: int, host_api_order: tuple[str, ...]) -> None:
        self._mic = mic
        self._target_rate = sample_rate
        self._host_api_order = host_api_order
        self._lock = threading.Lock()
        self._sum_squares = 0.0
        self._samples = 0
        self._peak = 0.0
        self._stream: sd.InputStream | None = None
        self.device: dict | None = None
        self.error: str | None = None

    def start(self) -> bool:
        """True if the meter is live. On failure `error` says why, in words meant for
        the window rather than for the log."""
        # The chooser renders on its own thread, and WASAPI is COM-based: without this
        # the stream fails with a host error naming an API that is not even in use.
        W.ensure_com()
        try:
            device = resolve(self._mic, self._host_api_order)
            rate, channels = negotiate(device, self._target_rate)
            stream = sd.InputStream(
                device=device["index"], channels=channels, samplerate=rate,
                dtype="float32", blocksize=0, callback=self._callback,
            )
            stream.start()
        except DeviceNotFound as exc:
            self.error = str(exc)
            return False
        except Exception as exc:  # noqa: BLE001 -- PortAudio raises its own types
            self.error = f"{type(exc).__name__}: {exc}"
            log.warning("level meter could not open %r: %s", self._mic, exc)
            return False
        self.device, self._stream, self.error = device, stream, None
        log.info("level meter on [%d] %s (%s) at %d Hz, %d ch",
                 device["index"], device["name"], device["api"], rate, channels)
        return True

    def _callback(self, indata, frames, time_info, status) -> None:
        # PortAudio's thread: accumulate two numbers and return.
        block = np.asarray(indata, dtype=np.float32)
        total = float(np.sum(np.square(block, dtype=np.float64)))
        peak = float(np.max(np.abs(block))) if block.size else 0.0
        with self._lock:
            self._sum_squares += total
            self._samples += block.size
            self._peak = max(self._peak, peak)

    def read(self) -> tuple[float, float]:
        """(rms, peak) since the previous read, then start a fresh window.

        Returning (0, 0) when no block has arrived yet is correct: a stream that is
        open but silent and a stream that has not delivered anything look the same to
        someone watching a bar, and both mean "nothing is coming in".
        """
        with self._lock:
            total, count, peak = self._sum_squares, self._samples, self._peak
            self._sum_squares, self._samples, self._peak = 0.0, 0, 0.0
        if count == 0:
            return 0.0, 0.0
        return math.sqrt(total / count), peak

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            log.exception("closing the level meter stream failed")
