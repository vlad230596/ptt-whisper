"""The recogniser, held in VRAM for the life of the process.

This is where the rewrite pays for itself. The subprocess engine mapped ~1.5 GB of DLLs
and a 3.09 GB model on *every* dictation (measured 11.0 s cold / 6.2 s warm before a
word was decoded). Here the model is constructed once and each utterance costs only
inference: re-measured on the same 35.4 s recording, 2.06 s sequential and 1.54 s
batched, against 20.9 s / 11.2 s through the exe.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

import numpy as np

from . import config as cfg
from . import settings

if TYPE_CHECKING:
    from faster_whisper import BatchedInferencePipeline, WhisperModel

log = logging.getLogger(__name__)

# A segment whose letters are mostly upper-case is the signature of the ALL-CAPS
# tail-degeneration bug (see CLAUDE.md's "Known limitations", 2026-08-17 addition):
# on a long, pause-free utterance the model has been seen to flip into upper-case,
# near-punctuation-free output for the remainder of the recording. `_DIAG_CAPS_*`
# is what flags a segment as part of that.
_DIAG_CAPS_MIN_LETTERS = 6
_DIAG_CAPS_UPPER_FRACTION = 0.8

# The sibling bug (same section of CLAUDE.md, discovered first) is a whole run-on,
# all-lower-case, essentially unpunctuated segment -- there the giveaway is not casing
# but a long segment with almost no punctuation at all. `_DIAG_FLAT_*` flags that
# shape too, so one mechanism catches both known failure modes instead of only the one
# that happened to be reported most recently.
_DIAG_FLAT_MIN_DURATION_SEC = 12.0
_DIAG_FLAT_MAX_PUNCT_PER_100_CHARS = 1.0
_PUNCTUATION = ".,!?;:-–—"


class _Diagnostics:
    """Cheap per-segment bookkeeping, collected while draining the same generator
    `transcribe()` already has to drain to build the transcript -- no extra decoding.

    Added 2026-08-17 after two look-alike bugs (CLAUDE.md's "Known limitations"): an
    occasional run-on, unpunctuated, all-lower-case tail on the batched path, and a
    separate occurrence of an all-caps, near-unpunctuated tail on the *sequential*
    path (`ptt_20260817_230856_13`, 162.5 s, no >=2 s pause anywhere, `BATCHING_ENABLED`
    off on this machine so batching's already-documented root cause does not apply).
    Both looked, from the log alone, like an ordinary successful transcription -- the
    only trace was the archived transcript itself. This exists so the *next*
    occurrence leaves evidence in `pushtotalk.log` without needing the dataset clip
    re-decoded from scratch.

    Logged unconditionally, once per real (`vad=True`) utterance: a one-line summary,
    proportional to the one `"%d chars | rec ...s | asr ...s"` line `app.py` already
    logs for every dictation. The full per-segment breakdown is only logged when one of
    the two shapes above is actually detected -- which should be rare -- so this does
    not turn into per-utterance log spam.
    """

    __slots__ = ("segments", "caps", "flat")

    def __init__(self) -> None:
        self.segments: list = []
        self.caps: list[tuple[int, float, float]] = []
        self.flat: list[tuple[int, float, float]] = []

    def add(self, segment) -> None:
        i = len(self.segments)
        self.segments.append(segment)
        text = segment.text
        letters = [c for c in text if c.isalpha()]
        if (
            len(letters) >= _DIAG_CAPS_MIN_LETTERS
            and sum(1 for c in letters if c.isupper()) / len(letters)
            > _DIAG_CAPS_UPPER_FRACTION
        ):
            self.caps.append((i, segment.start, segment.end))
        duration = segment.end - segment.start
        if duration >= _DIAG_FLAT_MIN_DURATION_SEC:
            punct = sum(1 for c in text if c in _PUNCTUATION)
            punct_per_100 = 100 * punct / max(len(text), 1)
            if punct_per_100 < _DIAG_FLAT_MAX_PUNCT_PER_100_CHARS:
                self.flat.append((i, segment.start, segment.end))

    def log(self, pipeline: str) -> None:
        if not self.segments:
            return
        min_avg_logprob = min(s.avg_logprob for s in self.segments)
        max_no_speech = max(s.no_speech_prob for s in self.segments)
        max_compression_ratio = max(s.compression_ratio for s in self.segments)
        fallbacks = sum(1 for s in self.segments if s.temperature > 0)
        log.info(
            "pipeline=%s segments=%d min_avg_logprob=%.3f max_no_speech_prob=%.3f "
            "max_compression_ratio=%.2f temperature_fallbacks=%d",
            pipeline, len(self.segments), min_avg_logprob, max_no_speech,
            max_compression_ratio, fallbacks,
        )
        if not self.caps and not self.flat:
            return
        log.warning(
            "possible decoding degeneration (see CLAUDE.md 'Known limitations', "
            "run-on lower/upper-case tail): caps_segments=%s flat_segments=%s",
            [(i, f"{a:.1f}-{b:.1f}") for i, a, b in self.caps],
            [(i, f"{a:.1f}-{b:.1f}") for i, a, b in self.flat],
        )
        for i, s in enumerate(self.segments):
            log.warning(
                "  segment %2d %6.1f-%6.1f avg_logprob=%+.3f no_speech=%.3f "
                "temperature=%.1f compression_ratio=%.2f: %r",
                i, s.start, s.end, s.avg_logprob, s.no_speech_prob, s.temperature,
                s.compression_ratio, s.text,
            )


class Engine:
    def __init__(self) -> None:
        self._model: WhisperModel | None = None
        self._batched: BatchedInferencePipeline | None = None
        self.compute_type = cfg.COMPUTE_TYPE
        self.compute_type_source = settings.CONFIG_SOURCE
        self.batching = cfg.BATCHING_ENABLED
        self.batching_source = settings.CONFIG_SOURCE

    @property
    def ready(self) -> bool:
        return self._model is not None

    def load(self) -> float:
        """Construct the model. Returns seconds taken."""
        started = time.monotonic()
        # Imported here, and only after the CUDA libraries have been pulled into the
        # process, so import order in the rest of the program cannot break CUDA.
        from . import cudalibs

        cudalibs.preload()
        from faster_whisper import BatchedInferencePipeline, WhisperModel

        # settings.json overrides COMPUTE_TYPE per machine -- see settings.py and
        # CLAUDE.md ("float16 is mandatory, int8 is broken"). float16 is a hardware fact
        # only on the reference RTX 5080; a GPU with real int8 support (e.g. Turing)
        # can be switched here without touching config.py.
        self.compute_type, self.compute_type_source = settings.effective_compute_type()
        self._model = WhisperModel(
            cfg.MODEL_DIR, device=cfg.DEVICE, compute_type=self.compute_type
        )
        self._batched = BatchedInferencePipeline(model=self._model)
        self.batching, self.batching_source = settings.effective_batching()
        took = time.monotonic() - started
        log.info("model loaded from %s in %.1f s (compute_type=%s, from %s; "
                 "batching=%s, from %s)",
                 cfg.MODEL_DIR, took, self.compute_type, self.compute_type_source,
                 self.batching, self.batching_source)
        return took

    def warm_up(self) -> float:
        """Decode 0.3 s of silence so the first real utterance is not the slow one.

        The cost being paid here is CUDA kernel selection plus faulting the model in
        from the file cache: measured 2.3 s warm, 8.9 s on a cold cache.
        """
        silence = np.zeros(int(0.3 * cfg.SAMPLE_RATE), dtype=np.float32)
        started = time.monotonic()
        # VAD must be off here, or it throws the silence away and nothing is decoded --
        # the warm-up then "takes" 0.0 s and the first real utterance pays the cost
        # instead, which is the whole thing this is meant to avoid.
        self.transcribe(silence, vad=False)
        took = time.monotonic() - started
        log.info("warm-up transcription took %.1f s", took)
        return took

    def transcribe(
        self,
        audio: np.ndarray,
        *,
        vad: bool = True,
        on_start: Callable[[float, float], None] | None = None,
        on_progress: Callable[[float], None] | None = None,
    ) -> list[str]:
        """Return the transcript as a list of segment texts, in order.

        `vad=False` is only for the warm-up; see the note on vad_filter below.

        `on_start`, if given, is called once VAD and language detection are done --
        cheap, and before any decoding -- with `(duration, duration_after_vad)` in
        seconds. The latter is how much of the recording is actual speech once
        silence is discarded, which is what decode time actually scales with.

        `on_progress`, if given, is called with the fraction (0..1) of the recording
        covered so far, after each decoded segment -- faster-whisper decodes
        lazily, so this is free. But CTranslate2's `generate()` call underneath is
        atomic: every segment belonging to one ~30 s window (sequential mode) or one
        batch of windows (batched mode, see BATCH_SIZE) becomes available in a single
        burst the moment that call returns, not smoothly during it. A recording short
        or continuous enough to fit in one window/batch -- the common case here --
        means this callback only fires once, at the very end.
        """
        if self._model is None or self._batched is None:
            raise RuntimeError("the model is not loaded yet")

        duration = len(audio) / cfg.SAMPLE_RATE
        options = dict(
            language=cfg.LANGUAGE,
            multilingual=True,
            hotwords=cfg.HOTWORDS,
            initial_prompt=cfg.INITIAL_PROMPT,
            # Not optional, and not a default: faster-whisper's Python API leaves
            # vad_filter off, while the bundled engine had it on. Without it an
            # accidental tap on a quiet room produces a confident hallucination --
            # measured on 2.5 s of silence and on 2.5 s of room noise, both gave
            # "Subtitles by the Amara.org community", and earlier runs echoed the
            # initial prompt's term list back as a sentence. With it, both come back
            # empty. On real speech it changes nothing: the same 35 s utterance
            # decoded to a byte-identical 526 characters either way.
            vad_filter=vad,
        )
        # Only worth batching once the utterance is long enough to contain several VAD
        # chunks; on a short clip there is nothing to parallelise and it is slower.
        # `self.batching` is a per-machine override (see settings.effective_batching()
        # and BACKLOG item 9) -- disabled by default, since the measured speed win did
        # not hold up against real usage and batching can lose punctuation on a long,
        # pause-free utterance.
        batched = self.batching and duration > cfg.BATCH_ABOVE_SEC
        pipeline = "batched" if batched else "sequential"
        if batched:
            segments, info = self._batched.transcribe(
                audio, batch_size=cfg.BATCH_SIZE, **options
            )
        else:
            segments, info = self._model.transcribe(audio, **options)
        if on_start is not None:
            on_start(info.duration, info.duration_after_vad)
        # faster-whisper decodes lazily; the generator must be drained here, inside the
        # call the caller timed. Cheap per-segment diagnostics are collected in the same
        # pass -- see `_Diagnostics`'s docstring for why and what they are for.
        texts = []
        diag = _Diagnostics() if vad else None
        for segment in segments:
            texts.append(segment.text)
            if diag is not None:
                diag.add(segment)
            if on_progress is not None and info.duration:
                on_progress(min(segment.end / info.duration, 1.0))
        if diag is not None:
            diag.log(pipeline)
        return texts
