# Whisper Push-to-Talk Dictation

Local Russian push-to-talk dictation for Windows 11. Hold F8, speak, release — the
recognised text is pasted into the window that was focused when you started speaking.
Nothing leaves the machine.

User-facing documentation is [README.md](README.md); ideas not yet done are in
[BACKLOG.md](BACKLOG.md); installing on another machine is
[deploy/DEPLOY.md](deploy/DEPLOY.md). This file is the working context: what must not be
broken, and why each decision is what it is.

## Goals and hard constraints

Set by the project owner and not up for silent revision:

- **Fully local.** No cloud STT, no telemetry.
- **GPU inference** on an RTX 5080 (Blackwell, sm_120 / CC 12.0).
- **Model `large-v3`**, Russian, with English technical terms coming out in latin script
  and correct case.
- **`float16` always** — see the first decision below. This one is a hardware fact, not a
  preference.
- **All paths without spaces and outside OneDrive.**

**Superseded, deliberately.** The original constraints — "engine is the standalone
Purfview Faster-Whisper-XXL exe", "no Python wrappers", "glue is AutoHotkey only" — were
relaxed by the owner and the project was rewritten in Python. Do not restore them and do
not treat surviving mentions in old `dataset/` notes as current. What replaced them is
below under *History*.

## Stack

| Layer | Component | Version |
|---|---|---|
| ASR | faster-whisper (CTranslate2), model resident in VRAM | 1.2.1 / ct2 4.8.1 |
| Model | faster-whisper-large-v3, float16, local copy | 2.88 GB |
| CUDA | cuBLAS + cuDNN from pip, no toolkit installed | cu12 ≥12.8 / cudnn ≥9.7 |
| Audio in | sounddevice (PortAudio, WASAPI first) | 0.5.5 |
| Audio out | PyAV for mp3 and resampling — no `ffmpeg.exe` anywhere | 18.0.0 |
| Windows | ctypes against user32/kernel32/gdi32/shell32/ole32 | — |
| GUI | Dear PyGui, for the microphone chooser only | 2.3.1 |
| Media | winrt, SMTC session control | 3.2.x |
| Runtime | CPython, managed by uv | 3.13.9 |

No pywin32, no AutoHotkey, no external processes at run time.

## Layout

```
D:\Git\ptt-whisper\
├─ pushtotalk\        the application (see the module table in README.md)
├─ tests\             150 unit + integration tests, pytest
├─ tools\             make_icon.py, selftest.py — developer tools only
├─ deploy\            Setup.ps1 + DEPLOY.md — bootstrap for a new machine
├─ models\            faster-whisper-large-v3, fetched by `ptt setup`
├─ dataset\           ptt_<stamp>.mp3 + .txt pairs, kept for tuning recognition
├─ logs\              pushtotalk.log (rotating), devices.txt
├─ assets\            generated .ico files
├─ ptt.cmd            CLI wrapper; StartPushToTalk.cmd (background), Dev.cmd (console)
├─ settings.json      written by the app: the chosen microphone (absent until one is)
└─ pyproject.toml     + uv.lock, .python-version
```

A git repository since 2026-08-13, when the project moved out of `C:\Software\Whisper` —
the working copy *is* the checkout, model and all. `.gitignore` keeps out what must not
travel: the venv (rebuilt by `uv sync`), the 3 GB model (fetched by `ptt setup`), the
logs, `settings.json` (this machine's microphone) and `dataset/` — the owner's voice
recordings, which stay on the machine that produced them and are the material for tuning
`HOTWORDS` and `HALLUCINATIONS`. So a fresh clone is code only, and `ptt setup` fills in
the rest.

`MODEL_DIR`, `LOG_DIR` and `DATA_DIR` in `config.py` are derived from the project root,
so the directory can be moved or copied and still runs. Everything else tunable is in
that one file, with the measurement behind each value in the comment above it — with
exactly one exception, `settings.json`, described below.

## Decisions, with the evidence behind them

### float16 is mandatory, int8 is broken
CTranslate2 with cuBLAS 12.x has no sm_120 IMMA kernels; int8 fails with
`CUBLAS_STATUS_NOT_SUPPORTED`. float16 works (generic GEMM + PTX JIT). `DEVICE = "cuda"`
is explicit so a CPU fallback fails loudly instead of quietly making dictation slow.

### The model stays in VRAM — this is what the rewrite bought
The subprocess engine mapped ~1.5 GB of DLLs and the 3 GB model on *every* utterance:
6.2 s warm, 11.0 s cold before a word was decoded. Now the process pays it once at
startup and each utterance costs only inference. Same 35.4 s recording: **20.9 s → 2.06 s
sequential, 11.2 s → 1.54 s batched.** Real dictations in the log run 0.3–0.7 s of ASR
for 2.5–8.7 s of speech.

### `vad_filter=True` is not optional, and it is not the default
faster-whisper's Python API leaves it off; the bundled exe had it on. Without it, an
accidental tap on a quiet room produces a confident hallucination — measured on 2.5 s of
silence and 2.5 s of room noise, both gave *"Subtitles by the Amara.org community"*, and
earlier runs echoed the initial prompt's term list back as a sentence. With it, both come
back empty. On real speech it changes nothing: the same 35 s utterance decoded to a
byte-identical 526 characters either way. The warm-up passes `vad=False` on purpose — VAD
would throw the silence away, the warm-up would "take" 0.0 s, and the first real
utterance would pay the cost the warm-up exists to avoid.

### `initial_prompt` built from `HOTWORDS` — the single most valuable setting
One list, used both as `hotwords` and, comma-joined, as the initial prompt. Measured:
exact-case term hits **7/8 → 8/8** (`RemoteTracking` was being lowercased by `hotwords`
alone), sentence punctuation restored, and a 100 %-reproducible hallucination eliminated.
Casing is taught by the prompt, so write each term exactly as it should appear.

### Adaptive batching above 15 s, never below
Batched inference decodes VAD chunks in parallel. Above the threshold it is a clear win
(above); on a short clip there is one chunk, nothing to parallelise, and it is slower.

### The paste target is captured on key-down
Recognition takes a second or two, by which time focus has often moved. The target window
is remembered at key-down, re-activated before pasting, and focus is handed back
afterwards (`RESTORE_FOCUS`). If the target is gone the text is deliberately **left on the
clipboard** rather than pasted somewhere wrong.

### The clipboard is not restored afterwards
Both this program and the AutoHotkey version used to put the old contents back on a
600 ms timer. That races with the target's own clipboard read: under load the target reads
after the timer fires and pastes the *previous* contents instead of the dictation.
Reproduced at a 900 ms stall, fixed by removing the restore entirely. The write itself is
synchronous and immediately readable even on a saturated CPU, so the timer was the bug. A
longer timer would only narrow the window and would still clobber anything copied
meanwhile.

### CUDA libraries are loaded by absolute path before importing faster_whisper
CTranslate2 resolves cuBLAS with a bare `LoadLibrary("cublas64_12.dll")` at the moment of
the *first encoder call*, and that lookup ignores `os.add_dll_directory`. Verified: with
the directories added, model construction succeeds and the first `encode()` still fails
with "Library cublas64_12.dll is not found", while loading the same file by absolute path
through ctypes succeeds. Hence `cudalibs.preload()`, called before the import, once.
This is also why `tools/doctor.py` decodes something instead of probing for CUDA.

### Not a Windows service, and two different autostarts
A service runs in session 0: it cannot install a keyboard hook for the interactive
desktop, see the foreground window, or paste into it. `service.py` manages an ordinary
detached user process with the same start/stop/status surface. The Run key starts it
unelevated; a logon task with highest privileges is the only way to start it elevated
without a UAC prompt every login, and the only way it can hook over elevated windows.
`ptt status` reports both, so a machine cannot quietly end up on the weaker one.

### The app installs itself; PowerShell only bootstraps it
`deploy/Setup.ps1` does the three things that cannot be done from inside the app on a
machine with no Python — install uv, `uv sync`, hand over — and nothing else. The model,
the icons, the GPU check, the microphone, the PATH entry and autostart are `setup.py`,
`doctor.py` and `model.py` inside the package, where they reuse `config`, `recorder`,
`asr` and `service` instead of restating that knowledge in another language, and where
the test suite reaches them. PowerShell in this project has no tests and should stay
small enough not to need any. Project knowledge added to `Setup.ps1` is in the wrong
place.

`doctor` decides *whether something is wrong*; that judgement is what
`tests/test_setup.py` covers — an LFS pointer masquerading as weights, a PATH entry that
would be appended twice, a path with a space. Note the threshold monkeypatch in that
file: `MIN_WEIGHTS_BYTES` is a gigabyte, and writing a plausible `model.bin` at full size
once per test filled the disk and failed the run.

Setup does **not** persist settings. Choosing a microphone is the one step every new
machine needs and the one thing a copied `config.py` always gets wrong, so setup resolves
and reports it — but writing it back belongs to the settings work in flight, not to a
second mechanism invented in the installer.

### `settings.json` is the only setting outside `config.py`, and only because of the chooser
Picking a microphone from a list and then having to edit Python to make it stick is not a
setting flow, and a program that rewrites its own source is worse than one with two
settings files. So the chooser writes `settings.json`, whose `mic` key overrides `MIC`;
`config.py` remains the default and the hand-edited file. What keeps that from being
confusing is that **the source is always reported**: `settings.effective_mic()` returns
the value *and* which file it came from, and that is logged at startup, printed by
`ptt devices` and `ptt doctor`, and shown in the chooser. Nothing in `settings.py` raises
— a truncated or hand-mangled file degrades to the `config.py` default and says so, since
it only ever held a preference. Writes go through a temp file and `os.replace`. Do not
add a second override mechanism (registry, env vars); extend this one.

The running instance re-reads it on every key-down, which is what lets `ptt mic` in a
console repoint a background instance without a restart.

### The chooser is Dear PyGui, on its own thread, and everything about that was measured
A level meter is an animation; hand-painting one in GDI the way `osd.py` does would be
hundreds of lines for a bar that moves. Before writing any of it, on this machine with
dearpygui 2.3.1: `create_context()`/`destroy_context()` survives being repeated in one
process (so the window can be reopened for the life of the app); it renders from a
secondary thread while the main thread runs the hook's own message loop; and it works
under `pythonw.exe`. That is why the chooser is in-process — no extra process, and the
"no external processes at run time" rule stands.

Three traps found the same way, all of which cost a debugging session if forgotten:

* **Any `dpg.*` call before `create_context()` is an access violation**, not an
  exception: no traceback, no log line, exit code 0xC0000005. Not even
  `get_dearpygui_version()`. Hence the lazy import inside `run()`.
* **The built-in font is ASCII-only** — Cyrillic renders as `????????`, which on a
  Russian Windows is every device name. Segoe UI is loaded instead; 2.x builds glyph
  ranges automatically, and `add_font_range_hint` is a deprecated no-op there. The
  chooser's own strings stay ASCII anyway, so the fallback path is still readable.
* **Read the DPI after `SetProcessDpiAwarenessContext`,** not before: `GetDpiForSystem`
  reports 96 to a process that is not yet aware, and the window comes out sized for a
  100 % display on a 125 % one.

The chooser runs on a thread of its own rather than on the dictation worker, or dictation
would stop working for as long as the window was open.

### One entry per microphone, not one per host API
The same jack appears three times (WASAPI, DirectSound, MME), and MME truncates names to
31 characters. `physical_devices()` folds them with the same tolerant match `resolve()`
uses and shows the longest spelling; the host API is not offered as a choice because
`HOST_API_ORDER` decides it. What is stored is therefore a name, which survives the
PortAudio indices being renumbered when anything is plugged in. Device names are stored
exactly as Windows reports them — one real headset here has a CR/LF *inside* its name, so
there is a `display_name()` for showing it and no normalisation anywhere else, or the
substring match would stop matching.

### Archiving
Every successful utterance is kept as `dataset\ptt_<stamp>.mp3` + `.txt` (UTF-8, no BOM).
mp3 is encoded in-process by PyAV, ~3.2 KB per second of audio. A `.raw.txt` appears
**only** when the hallucination filter actually dropped something — that is the material
for refining the blocklist. Archiving never raises: a failed archive must not lose the
paste.

### The hallucination blocklist is a backstop
large-v3 emits subtitle-credit phrases over trailing near-silence. No VAD method or
threshold affected it (silero v4/v5, auditok, webrtc, thresholds, tail trimming); the
initial prompt suppressed it in every repeat run, and `vad_filter` stops most of it being
generated at all. `HALLUCINATIONS` stays as defence in depth.

## Two things that will bite

**The hook callback must stay fast.** Windows silently removes a low-level keyboard hook
whose callback exceeds `LowLevelHooksTimeout` (300 ms default), and the hotkey then just
stops working with no error anywhere. `hotkeys.py` classifies and queues; everything slow
happens on the worker thread. Keep it that way.

**COM must be initialised on any thread that starts a capture.** WASAPI is COM-based and
PortAudio leaves the apartment to the caller. Without `winapi.ensure_com()` the stream
fails with a misleading `Unanticipated host error ... WdmSyncIoctl`, naming a host API
that is not even in use.

## Verifying a change

```
uv run pytest                        # 150 tests, ~11 s
uv run pytest -m "not integration"   # skip the ones that take focus
uv run python tools/selftest.py      # injects a real F8 into the real app
ptt doctor                           # environment, GPU, model, microphone
```

The paste tests build their own window with an edit control and inject real keystrokes, so
focus handling and Ctrl+V are covered rather than assumed. The hook ignores only input
carrying its own signature in `dwExtraInfo`, which is what makes `selftest.py` possible.
Clipboard and paste tests **skip on a locked workstation** — a real skip, not a silent
pass.

## Status

In daily use. Recognition, capture, pasting, media pausing and autostart are all confirmed
on live speech.

## Known limitations

- **`F8` is swallowed globally**, so VS Code's "Go to Next Problem" and debugger F8 stop
  working while it runs. Change `HOTKEY_REC` if that matters.
- **Elevated windows** need the elevated logon task; an ordinary process cannot hook them.
- **The microphone is a substring match against a device name** and is therefore
  machine-specific. An HD Audio input endpoint also *disappears* from the device list
  when nothing is plugged into the jack, which presents as "matches nothing" rather than
  as silence — deliberately an error at the next dictation, not a silent fallback to some
  other device. Ctrl+Alt+M is the fix.
- **Folding devices by name is tolerant, and tolerance costs**: two microphones whose
  names are a prefix of one another would appear in the chooser as one entry. It has not
  happened here, and the alternative loses the MME truncation.
- **Not every device can be metered.** A Bluetooth headset on this machine fails to open
  under WDM-KS (`WdmSyncIoctl ... GLE 0x48F`) with COM initialised and everything else
  right; the chooser reports it in the window rather than pretending it is silent.
- Short acronyms in fast speech remain unreliable; extend `HOTWORDS` from `dataset/`
  misses.

## History — what was removed, so it does not come back

Until 2026-07-29 this was AutoHotkey v2 glue driving `ffmpeg` (DirectShow capture) and
Purfview's Faster-Whisper-XXL `.exe` per utterance. It worked, and the measurements it
produced are the ones quoted above, but it had a **~6 s floor on every dictation**: the
exe has no resident/server/stdin mode — verified across all 113 flags — so the model was
loaded from scratch each time. The Python rewrite removed that floor and made the whole
thing testable. On 2026-08-07 the dead stack was deleted (6.35 GB): `engine\`, `ffmpeg\`,
`dl\`, `tools\AutoHotkey\`, `PushToTalk.ahk`, plus the `ffmpeg` entry in the user PATH.

Conclusions from that era that still bind, because they are about the model and not the
engine: float16 only, the initial-prompt casing effect, VAD does not fix the subtitle
hallucination, and batching only pays above ~15 s. Conclusions that died with it: the
`-flush_packets 1` / `TAIL_MS` fight against `taskkill` losing buffered audio (capture is
in-process now and loses nothing — `TAIL_MS` is only a grace period for trailing speech),
`--rehot` transliterating terms into Cyrillic, and every AutoHotkey trap.
