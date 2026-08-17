"""Every tunable lives here. Edit and restart -- nothing else reads the registry or
a dotfile.

Measurements quoted in the comments were taken on this machine (RTX 5080, large-v3,
float16) and carried over from the AutoHotkey implementation this replaces. Where the
Python rewrite changed the meaning of a value, the comment says so explicitly.
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

# ------------------------------- AUDIO INPUT --------------------------------
# Substring of the input device name as Windows reports it. The old ffmpeg/DirectShow
# name works verbatim here: PortAudio exposes the same string.
# Ctrl+Alt+F8 lists every input device if this ever stops matching.
#
# This is the *default*. Picking a microphone in the chooser (Ctrl+Alt+M, `ptt mic`)
# writes SETTINGS_FILE, and that wins over this value -- see settings.py for why the
# "everything is in config.py" rule has exactly this one exception. Which of the two is
# in force is logged at startup and printed by `ptt devices`.
MIC = "Microphone (High Definition Audio Device)"

# Preferred host API, tried in order. WASAPI is the lowest-latency path to a shared-mode
# capture device; DirectSound and MME are kept as fallbacks for odd drivers.
HOST_API_ORDER = ("Windows WASAPI", "Windows DirectSound", "MME")

SAMPLE_RATE = 16000  # what Whisper wants; the device is resampled to it if needed

# ------------------------------- MIC CHOOSER --------------------------------
# The level meter in the chooser window (Ctrl+Alt+M). Speech on this machine sits
# around -25 dBFS, a silent room around -60, so the bar starts being useful there.
METER_FLOOR_DB = -60.0
METER_PEAK_HOLD_MS = 1200  # how long the peak marker stays before it falls back
METER_CLIP_DB = -1.0  # at or above this the bar turns red: the input is too hot
MIC_WINDOW_SIZE = (860, 640)  # px at 96 dpi, scaled up on a high-dpi display

# ------------------------------- PATHS --------------------------------------
# Derived from the project root rather than hard-coded, so a copy of this directory
# runs unchanged wherever it is put -- which is what deploy/Setup.ps1 relies on.
# Point MODEL_DIR somewhere else to try another model (large-v2, distil-large-v3);
# it is passed straight to WhisperModel, so a bare name like "large-v3" also works and
# downloads into the Hugging Face cache instead.
MODEL_DIR = str(_ROOT / "models" / "faster-whisper-large-v3")
LOG_DIR = str(_ROOT / "logs")

# The only file the app writes settings *to*. Everything in it overrides the matching
# name here; it exists so that choosing a microphone from a list does not mean editing
# Python. Delete it and the defaults in this file are back.
SETTINGS_FILE = str(_ROOT / "settings.json")

# Every successful utterance is archived as an <stamp>.mp3 + <stamp>.txt pair, so the
# recordings can be replayed later to tune recognition. ~4 KB of audio per second.
# A third file, <stamp>.raw.txt, appears only when the hallucination filter actually
# dropped something -- that is the material for refining HALLUCINATIONS below.
KEEP_AUDIO = True
DATA_DIR = str(_ROOT / "dataset")

# ------------------------------- RECOGNITION --------------------------------
# Default for this machine's config -- but "float16 always" is a fact about the
# reference GPU (RTX 5080, Blackwell/sm_120), not about every GPU: cuBLAS 12.x has no
# IMMA kernel for sm_120, so int8 there hard-fails with CUBLAS_STATUS_NOT_SUPPORTED.
# A machine with real int8 support (Turing/sm_75 and later architectures with DP4A) can
# override this per machine in settings.json's "compute_type" key -- see settings.py and
# CLAUDE.md ("float16 is mandatory, int8 is broken") for the mechanism and the field
# measurement that justified turning it on for one such machine. Do not change this
# default for the reference hardware.
COMPUTE_TYPE = "float16"
LANGUAGE = "ru"
DEVICE = "cuda"  # explicit, so a CPU fallback fails loudly instead of silently

# The model is held in VRAM for the life of the process, so the 6-11 s per-utterance
# engine start-up of the subprocess era is gone. What remains is a one-off cost the
# first time CUDA kernels are selected: measured 2.3 s warm file cache, 8.9 s cold.
# This runs one throwaway transcription of 0.3 s of silence at startup to pay it before
# the first real dictation. Hotkeys are live while it runs.
WARMUP = True

# Project vocabulary. Add terms here and both `hotwords` and the initial prompt below
# pick them up; this is the only list to maintain.
# Case matters: the initial prompt teaches the model how to spell these, so write each
# term exactly as you want it inserted (e.g. "CPU" instead of "cpu" if preferred).
HOTWORDS = (
    "numpy pandas React git commit StandaloneTrackingSocket "
    "RemoteTracking ImGui nRF52840 cpu"
)

# The same terms, comma-separated, fed as initial_prompt. Measured effect on the test
# utterance: exact-case hits went 7/8 -> 8/8 ("RemoteTracking" was being lowercased by
# hotwords alone), sentence punctuation is restored, and the trailing-silence
# hallucination below stopped occurring entirely.
INITIAL_PROMPT = "Термины проекта: " + ", ".join(HOTWORDS.split()) + "."

# large-v3 emits "subtitle credit" phrases over trailing near-silence -- an artefact of
# its subtitle training data. The initial prompt above suppressed it in every repeat
# run, but no VAD or threshold setting affected it at all, so this stays as a backstop:
# whole segments matching these patterns are dropped.
# Patterns are case-insensitive regex, matched against one transcript segment.
HALLUCINATIONS = (
    r"Субтитры (создавал|создал|сделал|подготовил)",
    r"Редактор субтитров",
    r"Корректор",
    r"Продолжение следует",
    r"Спасибо за (просмотр|внимание)",
    r"Подписывайтесь на канал",
    r"ПОДПИШИСЬ",
    r"Тайминг и перевод",
    r"^\s*(Субтитры|Sous-titres|Sottotitoli)\b",
    r"^\s*(Аминь|Ура)[.!]*\s*$",
    # The English-language members of the same family. VAD (see asr.py) stops these
    # from being generated in the first place; kept as a backstop because they are what
    # silence produced before it was switched on.
    r"Subtitles by",
    r"Amara\.org",
)

# Batched inference splits the audio across VAD chunks and decodes them in parallel.
# The single-clip measurement that motivated this (35.4 s, RTX 5080, float16: 2.06 s
# sequential -> 1.54 s batched, "slightly better sentence punctuation") did not hold up
# against real usage -- see BACKLOG item 9. Decoding every dataset recording over
# BATCH_ABOVE_SEC twice (44 clips, this machine, GTX 1650 Ti / int8_float16) measured an
# average speedup of only 1.06x, and batching lost more than half its punctuation density
# (against the same audio decoded sequentially) on 8 of the 44 -- including the utterance
# that surfaced the unpunctuated-run-on bug in CLAUDE.md's "Known limitations". Disabled
# by default until the same comparison is run on the RTX 5080, where the original 25%
# number came from and where the trade might look different.
#
# Per-machine override, same mechanism as `compute_type` below: settings.json's
# "batching" key wins over this default -- see settings.py and
# `settings.effective_batching()`. `ptt doctor` and `ptt devices` report which is in
# force and where it came from.
BATCHING_ENABLED = False
BATCH_ABOVE_SEC = 15
BATCH_SIZE = 8

# ------------------------------- HOTKEYS ------------------------------------
# Held to dictate. Suppressed while the app runs, so it never reaches the focused app.
HOTKEY_REC = "F8"

# Command hotkeys. A chord is "Ctrl+Alt+X"; modifiers are Ctrl, Alt, Shift, Win.
# A chord on the same key as HOTKEY_REC wins over dictation when its modifiers are held.
HOTKEY_LIST_DEVICES = "Ctrl+Alt+F8"
HOTKEY_TEST_MODE = "Ctrl+Alt+T"
HOTKEY_MIC = "Ctrl+Alt+M"  # the microphone chooser, with a live level meter
HOTKEY_QUIT = "Ctrl+Alt+Q"

MIN_REC_MS = 350  # ignore accidental taps shorter than this

# Keep recording this long after the key is released.
# CHANGED MEANING vs the AutoHotkey version: that one needed 400 ms because hard-killing
# ffmpeg discarded ~0.46 s of in-flight audio (3.0 s held + 0.4 s tail measured 3.16 s
# captured). Capture is now in-process and loses nothing, so this is purely a grace
# period for speech that trails the key release -- 200 ms reproduces the old effective
# behaviour. Lower it to 0 if you always release the key late; raise it if last words
# get clipped.
TAIL_MS = 200

# Root-mean-square below which the capture is treated as "no audio at all", i.e. a
# muted or dead device rather than a quiet room. A silent room on this mic measures
# 0.00086, so this only catches a truly dead input; quiet-but-real audio is left to
# the recogniser's own silence handling.
MIN_RMS = 0.0002

# ------------------------------- PASTING ------------------------------------
# The text belongs to the window that was focused when dictation started, not to
# whatever is focused a couple of seconds later. The target window is remembered on
# key-down and re-activated before pasting.
# RESTORE_FOCUS then hands focus back to the window you moved on to, so dictating does
# not drag you out of what you are now doing.
RESTORE_FOCUS = True
FOCUS_SETTLE = 150  # ms for the target window to actually take focus
PASTE_DELAY = 120  # ms to let the target window consume Ctrl+V

# There is deliberately no CLIP_RESTORE setting any more. Putting the old clipboard back
# on a timer raced with the target's own clipboard read and, under load, pasted the
# previous contents instead of the dictation -- reproduced at a 900 ms stall and fixed by
# not restoring at all. The dictation keeps the clipboard; see pushtotalk/paste.py.

# ------------------------------- MEDIA ------------------------------------
# Pause whatever is playing while you dictate, then resume it. Uses the Windows media
# session API (SMTC), the same channel the hardware media keys talk to, so it targets
# the actual player rather than blindly broadcasting a play/pause keystroke.
PAUSE_MEDIA = True

# Which players to touch, matched as case-insensitive substrings of the session's
# AppUserModelId. Empty tuple = every session that is currently playing.
# Known ids on this machine: "ru.yandex.desktop.music" (Яндекс.Музыка).
# Ctrl+Alt+F8 also prints the live session list.
PAUSE_MEDIA_APPS: tuple[str, ...] = ()

# ------------------------------- APPEARANCE ---------------------------------
# Tray icons, regenerated by `uv run python tools/make_icon.py`. The recording icon is
# the same microphone in red, so a glance at the tray tells you whether it is listening.
# A missing file falls back to the stock Windows application icon.
ICON_IDLE = str(_ROOT / "assets" / "pushtotalk.ico")
ICON_RECORDING = str(_ROOT / "assets" / "pushtotalk-rec.ico")

# ------------------------------- STATUS OSD ---------------------------------
# "cursor" puts the status near the mouse pointer (what the old ToolTip did);
# "bottom-right" pins it to the corner of the monitor the pointer is on.
OSD_POSITION = "cursor"
OSD_FONT = "Segoe UI"
OSD_FONT_PT = 11
OSD_MAX_WIDTH = 520  # px at 96 dpi, before word wrapping kicks in
OSD_ALPHA = 235  # 0..255
OSD_BG = (0x20, 0x20, 0x20)  # R, G, B
OSD_FG = (0xF0, 0xF0, 0xF0)
OSD_FG_ERROR = (0xFF, 0x8A, 0x80)
OSD_PROGRESS_BG = (0x40, 0x40, 0x40)  # the bar's empty track
OSD_PROGRESS_FG = (0x4F, 0xC3, 0xF7)  # the filled portion

# How often the "Transcribing ..." line refreshes while decoding runs. Not measured --
# just fast enough that the line is visibly still alive on a GPU slow enough for decoding
# to take seconds, so a long dictation does not look indistinguishable from a hang.
TRANSCRIBE_TICK_MS = 300
