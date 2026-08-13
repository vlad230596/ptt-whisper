"""Archiving each utterance as an <stamp>.mp3 + <stamp>.txt pair.

mp3 is encoded in-process with PyAV, which faster-whisper already depends on and which
bundles the FFmpeg libraries -- there is no external ffmpeg.exe in this program's path
any more. Measured 3.2 KB per second of 16 kHz mono audio at q:a 4.
"""

from __future__ import annotations

import logging
from pathlib import Path

import av
import numpy as np

log = logging.getLogger(__name__)

MP3_QUALITY = 4  # libmp3lame VBR quality, same as the old `-q:a 4`


def encode_mp3(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """Write float32 mono `samples` to `path` as mp3."""
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype(np.int16)
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mp3", rate=sample_rate)
        # add_stream defaults to stereo; the encoder must match the frame we feed it.
        # Sample format conversion (s16 -> the encoder's planar format) and mp3's fixed
        # 1152-sample framing are both handled inside PyAV's encode().
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(pcm.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = sample_rate
        frame.pts = 0
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


def store(
    data_dir: Path,
    stem: str,
    samples: np.ndarray,
    sample_rate: int,
    text: str,
    raw_text: str,
) -> None:
    """Persist one utterance. Never raises: a failed archive must not lose the paste.

    `raw_text` is written to a third file only when the hallucination filter actually
    changed the result -- that difference is the material for tuning the patterns.
    """
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        encode_mp3(data_dir / f"{stem}.mp3", samples, sample_rate)
        # No BOM, so the .txt files stay clean for batch processing.
        (data_dir / f"{stem}.txt").write_text(text + "\n", encoding="utf-8")
        if raw_text.strip() != text:
            (data_dir / f"{stem}.raw.txt").write_text(raw_text + "\n", encoding="utf-8")
    except Exception:
        log.exception("archiving %s failed", stem)
