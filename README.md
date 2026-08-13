# ptt-whisper

> [!NOTE]
> **Written by Claude, for one person's machine and habits.** This is a personal tool
> published as-is: it was built and tuned against one GPU, one microphone, one language
> pair and one set of working habits, and the defaults are that person's defaults. There
> is no support, no compatibility promise and no warranty — expect to read `config.py` and
> change things before it fits you.

Hold **F8**, speak, release. The text appears at the caret in whatever window you were
looking at when you started talking — usually about half a second after you let go.

Whisper `large-v3` via faster-whisper, resident in VRAM, running on your own GPU. Nothing
is uploaded, nothing is logged anywhere but this folder, and the whole thing keeps working
with the network unplugged.

Tuned for Russian dictation with English technical terms kept in latin script and correct
case (`RemoteTracking` stays `RemoteTracking`, not «ремоут трекинг»), but the language and
the vocabulary are one line each in `config.py`.

## What it solves

**Dictation that is actually fast enough to use.** The obvious way to build this — shell
out to a Whisper binary per utterance — pays for loading ~1.5 GB of CUDA DLLs and a 3 GB
model *every single time*: measured 6.2 s warm and 11.0 s cold before a word is decoded.
Here the process pays that once at startup and each utterance costs only inference: the
same 35 s recording went **20.9 s → 2.06 s**, and real dictations run 0.3–0.7 s of ASR for
2.5–8.7 s of speech.

**Dictation into the window you were actually looking at.** Recognition takes a moment, by
which time focus has often moved. The target window is captured on key-down, re-activated
before pasting, and focus is handed back afterwards. If the target is gone, the text is
left on the clipboard on purpose rather than pasted into the wrong place.

**Dictation that does not invent sentences out of silence.** An accidental tap on a quiet
room makes `large-v3` produce a confident *"Subtitles by the Amara.org community"*. VAD
filtering, a vocabulary-derived initial prompt and a blocklist backstop turn that into an
empty result — measured, with the same real utterance still decoding byte-identically.

**Privacy without a subscription.** No cloud STT, no telemetry, no account, no per-minute
billing.

## Built on

| Layer | Component | Version |
|---|---|---|
| ASR | faster-whisper (CTranslate2), model resident in VRAM | 1.2.1 / ct2 4.8.1 |
| Model | faster-whisper-large-v3, float16, local copy | 2.88 GB |
| CUDA | cuBLAS + cuDNN from pip — no CUDA toolkit installed | cu12 ≥ 12.8 / cudnn ≥ 9.7 |
| Audio in | sounddevice (PortAudio, WASAPI first) | 0.5.5 |
| Audio out | PyAV for mp3 and resampling — no `ffmpeg.exe` anywhere | 18.0.0 |
| Windows | `ctypes` against user32/kernel32/gdi32/shell32/ole32 | — |
| GUI | Dear PyGui, for the microphone chooser only | 2.3.1 |
| Media | winrt, SMTC session control | 3.2.x |
| Runtime | CPython, managed by uv | 3.13.9 |

Pure Python. No pywin32, no AutoHotkey, no external processes at run time, no service.
Developed against an RTX 5080 (Blackwell, sm_120), where `float16` is mandatory —
CTranslate2 has no sm_120 IMMA kernels, so `int8` fails outright.

## Features

- **Push-to-talk on a global hotkey**, hold-to-record, with an on-screen status line and a
  tray icon that changes while recording.
- **Pastes into the window you started in**, restores your focus afterwards, and falls
  back to the clipboard rather than pasting somewhere wrong.
- **Project vocabulary**: one `HOTWORDS` list feeds both `hotwords` and the initial
  prompt, which is what teaches the model exact casing for your own terms.
- **Hallucination filtering** — VAD + prompt + a pattern blocklist, with the raw text kept
  alongside the cleaned one whenever the filter actually changed something.
- **Adaptive batching** — parallel decoding above 15 s of audio, sequential below, because
  batching a single VAD chunk is slower.
- **Media auto-pause**: whatever is playing is paused over SMTC while you speak and
  resumed when you stop.
- **A microphone chooser with a live level meter** (Ctrl+Alt+M), one entry per physical
  jack rather than one per host API, saying which devices are silent, clipping, or refuse
  to open at all. The choice takes effect without a restart.
- **Self-installing**: `ptt setup` fetches the model, generates icons, wires up PATH and
  autostart; `ptt doctor` checks every step a fresh machine can fail.
- **Two autostart modes** — an ordinary Run-key process, or an elevated logon task for
  dictating into elevated windows without a UAC prompt at every login.
- **Every utterance archived** as an `.mp3` + `.txt` pair, which is the raw material for
  extending the vocabulary from your own misses.
- **150 tests**, including ones that build a real window and inject real keystrokes into
  it, so focus handling and Ctrl+V are covered rather than assumed.

## Requirements

Windows 10/11, an NVIDIA GPU with ~4 GB free VRAM, [uv](https://docs.astral.sh/uv/), and
about 4 GB of disk for the model. No CUDA toolkit — the libraries come from pip. Install
into a path without spaces and outside OneDrive.

> [!TIP]
> **Turn on Windows clipboard history** — Settings → System → Clipboard, or press
> **Win+V** and click "Turn on". Every dictation takes the clipboard and does *not* put
> the previous contents back (see [What happens on a dictation](#what-happens-on-a-dictation)
> for why restoring it is unsafe). With history on, whatever you had copied is still one
> **Win+V** away — and so is every dictation you have made since, which doubles as a
> recovery path if a paste lands in the wrong place.

## Running it

```
uv sync                      # once, and after any change to pyproject.toml
ptt start                    # launch in the background
ptt status                   # running? autostart on? controllable from here?
ptt stop                     # ask it to quit  (--force terminates)
ptt restart
ptt autostart on             # start with Windows, per user, no admin
ptt autostart on --elevated  # ...with full privileges, via a logon task (needs admin once)
ptt autostart off
ptt mic                      # choose the microphone, with a live level meter
ptt mic --reset              # forget the choice, go back to MIC in config.py
ptt devices                  # list microphones and resolve the chosen one
ptt run                      # foreground, logs on the console
```

`StartPushToTalk.cmd` and `Dev.cmd` still work and do the same as `ptt start` / `ptt run`.

On a machine that has never run it, copy the project files and run this instead:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\Setup.ps1 -- --add-to-path --autostart --start
```

The script does only what cannot be done from inside the app — install uv and run
`uv sync` — and then hands over to `ptt setup`, which fetches the model, generates the
icons, wires up the PATH and autostart, and checks the whole thing end to end. Both halves
are idempotent, so `ptt setup` is equally the repair command.

```
ptt setup                    # re-run the install; changes nothing outside the project
ptt setup --skip-model       # ...without touching models\
ptt doctor                   # environment, packages, gpu, model, microphone, icons
```

`ptt doctor` is the first thing to try when dictation stops working; it prints what it
found at each step rather than a verdict. [deploy/DEPLOY.md](deploy/DEPLOY.md) has the
requirements, the same steps by hand, what to carry over from the old machine, and a
symptom-to-cause table.

**There is no Windows service, deliberately.** A service runs in session 0, which cannot
install a keyboard hook for the interactive desktop, cannot see the foreground window and
cannot paste into it. `ptt` manages an ordinary detached user process instead, with the
same start/stop/status/autostart surface a service would have given you.

**Two autostart mechanisms, and they are not interchangeable.** The Run key starts an
ordinary process — enough unless you dictate into windows that are themselves elevated,
which an ordinary process cannot hook. For that, `--elevated` registers a logon task with
highest privileges, which is the only way to get an elevated start without a UAC prompt at
every login. `ptt status` reports both so a machine cannot quietly end up running the
weaker one.

**If you run it elevated, control it from an elevated prompt.** Windows blocks window
messages from a lower integrity level to a higher one, so `ptt stop` from an ordinary
shell cannot reach an elevated instance. It says so rather than timing out. Ctrl+Alt+Q and
the tray menu always work, since those come from your own input.

| Hotkey | What it does |
| --- | --- |
| **F8** (hold) | Record; release to transcribe and paste |
| **Ctrl+Alt+M** | Choose the microphone, watching a live level meter |
| **Ctrl+Alt+F8** | Write the microphone and media-session list to `logs/devices.txt` and open it |
| **Ctrl+Alt+T** | Test mode: show the recognised text in a dialog instead of pasting it |
| **Ctrl+Alt+Q** | Quit (also in the tray icon's menu) |

Everything tunable is in [`pushtotalk/config.py`](pushtotalk/config.py) — microphone,
hotkeys, project vocabulary, the hallucination filter, timings, and which media players
get paused while you dictate. The comments there record what was measured, not just what
was chosen; read them before changing a number.

> [!IMPORTANT]
> **Editing Python to change a setting is temporary, and so is `F8`.** The vocabulary, the
> key bindings and the rest belong in a settings file that can be edited — and, for
> hotwords, switched per project — without touching the source; `config.py` holding them
> is a stage this project has not moved past yet, and `settings.json` (the microphone) is
> so far the only thing that has. Item 5 in [BACKLOG.md](BACKLOG.md) is that work.
>
> `HOTKEY_REC = "F8"` in particular is a poor default and is only the default because it
> was convenient here: F8 is swallowed globally, so a debugger's step-over and VS Code's
> "Go to Next Problem" stop working while this runs. Pick a key you do not use — something
> like `Ctrl+Alt+Space`, or a side button mapped by your mouse driver — before deciding
> whether the tool is any good.

## Choosing the microphone

**Ctrl+Alt+M**, the tray menu, or `ptt mic` opens a window listing every microphone with
a level meter on the selected one: speak, and the bar should follow your voice. It says
which devices are silent, which are clipping, and which refuse to open at all. "Use this
microphone" takes effect immediately — there is nothing to restart, and a choice made
from a console reaches a running instance at the next dictation.

Each microphone is listed once, not once per host API: WASAPI, DirectSound and MME all
expose the same jack, and which one gets opened is decided by `HOST_API_ORDER` rather
than by you.

The choice is written to `settings.json` and overrides `MIC` in `config.py`, which stays
the default. That is the *only* setting kept outside `config.py`. `ptt devices`,
`ptt doctor` and the log all say which of the two files the current microphone came from,
and `ptt mic --reset` goes back to the default.

## What happens on a dictation

1. The keyboard hook swallows F8 and remembers which window had focus.
2. Any playing media session is paused through SMTC (the channel the media keys use),
   and resumed when the recording stops.
3. Audio is captured in-process, resampled to 16 kHz if the device insists on its own
   rate, and checked for a dead microphone.
4. faster-whisper transcribes it — batched above 15 s, sequential below.
5. Segments that match the hallucination patterns are dropped; the rest is joined into
   one line.
6. The text goes on the clipboard, is pasted with Ctrl+V into the remembered window, and
   focus goes back to wherever you moved on to. **The dictation keeps the clipboard** --
   the previous contents are not put back.
7. The utterance is archived to `dataset/` as an `.mp3` + `.txt` pair, plus a
   `.raw.txt` when filtering actually changed something.

If the text cannot be delivered — the window is gone, or it refuses focus — it is left on
the clipboard on purpose. A Ctrl+V away beats a lost dictation.

Restoring the old clipboard afterwards, which both this program and the AutoHotkey version
used to do on a 600 ms timer, is *not* safe and has been removed. Ctrl+V is delivered
asynchronously: a target under load reads the clipboard after the timer has already fired
and pastes the previous contents instead of what you just said. Reproduced at a 900 ms
stall, and fixed by the timer at 5 s — so the cause was the timer, not the write, which is
synchronous and immediately readable even on a saturated CPU. A longer timer would only
narrow the window, and it would still overwrite anything you copied in the meantime.

## Tests

```
uv run pytest                        # 150 tests, ~11 s
uv run pytest -m "not integration"   # skip the ones that take focus
uv run python tools/selftest.py      # end to end: injects a real F8 into the real app
```

The paste tests build their own window with an edit control and inject real keystrokes
into it, so focus handling and Ctrl+V are covered rather than assumed. `selftest.py`
starts the actual program and presses F8 for real — the hook only ignores input carrying
its own signature in `dwExtraInfo`, which is what makes that possible. Run it in a quiet
room and keep your hands off the keyboard.

The clipboard and paste tests **skip on a locked workstation**: Windows denies clipboard
access to ordinary processes while the lock screen has the foreground. That is a real skip,
not a silent pass — unlock and re-run to actually exercise them.

The icons are generated, not committed as opaque binaries:

```
uv run python tools/make_icon.py    # writes assets/pushtotalk{,-rec}.ico
```

`tools/` holds only what a developer needs and a user does not: `make_icon.py` and
`selftest.py`. Installing, checking and fetching the model are `ptt setup` / `ptt doctor`,
inside the package, where the tests reach them.

Ideas not done yet live in [BACKLOG.md](BACKLOG.md).

## Layout

| File | Responsibility |
| --- | --- |
| `config.py` | Every tunable, with the measurements behind it |
| `settings.py` | The one file the app writes: the chosen microphone, over `config.py` |
| `app.py` | Orchestration and the thread model |
| `hotkeys.py` | Low-level keyboard hook, chord parsing, the message loop |
| `recorder.py` | Microphone capture, rate negotiation, resampling, the level meter |
| `micui.py` | The microphone chooser window (Dear PyGui, on its own thread) |
| `asr.py` | The resident model |
| `text.py` | Segment joining and the hallucination filter |
| `paste.py` | Clipboard, focus, synthetic Ctrl+V |
| `media.py` | Pausing and resuming players over SMTC |
| `archive.py` | mp3 + txt archiving via PyAV |
| `osd.py`, `tray.py` | Status line and notification-area icon |
| `winapi.py` | The Win32 surface, declared once with correct argtypes |
| `cudalibs.py` | Loading the pip CUDA libraries so CTranslate2 can find them |
| `cli.py` | The `ptt` commands |
| `service.py` | Start/stop/status of the background instance, autostart entries |
| `setup.py` | First-run install: model, icons, PATH, autostart |
| `doctor.py` | Every check a fresh machine can fail, in the order the app hits them |
| `model.py` | Where the model lives, whether it is complete, fetching it |

## Two things that will bite if you forget them

**The hook callback must stay fast.** Windows silently removes a low-level keyboard hook
whose callback runs longer than `LowLevelHooksTimeout` (300 ms by default), and the
hotkey then just stops working with no error anywhere. The callback in `hotkeys.py`
classifies the key and queues it; everything slow happens on the worker thread. Keep it
that way.

**COM must be initialised on any thread that starts a capture.** WASAPI is COM-based and
PortAudio leaves the apartment to the caller. Without `winapi.ensure_com()` the stream
fails with a misleading `Unanticipated host error ... WdmSyncIoctl`, which points at a
host API you are not even using.
