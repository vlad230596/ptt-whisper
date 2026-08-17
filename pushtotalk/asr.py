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
        if self.batching and duration > cfg.BATCH_ABOVE_SEC:
            segments, info = self._batched.transcribe(
                audio, batch_size=cfg.BATCH_SIZE, **options
            )
        else:
            segments, info = self._model.transcribe(audio, **options)
        if on_start is not None:
            on_start(info.duration, info.duration_after_vad)
        # faster-whisper decodes lazily; the generator must be drained here, inside the
        # call the caller timed.
        texts = []
        for segment in segments:
            texts.append(segment.text)
            if on_progress is not None and info.duration:
                on_progress(min(segment.end / info.duration, 1.0))
        return texts
