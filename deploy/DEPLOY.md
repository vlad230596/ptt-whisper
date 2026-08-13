# Deploying to a new machine

Copy the project files. Run one command. Everything else installs itself.

## Where the line is drawn

A fresh machine has no Python, so the app cannot bootstrap itself all the way. That is the
*only* thing [`Setup.ps1`](Setup.ps1) does:

```
Setup.ps1  ->  install uv  ->  uv sync  ->  ptt setup
   (~90 lines of PowerShell)                (pushtotalk/setup.py)
```

`uv sync` is where CPython 3.13 and every dependency arrive. From that point on the app is
responsible for its own installation: the model, the icons, the GPU check, the microphone,
the PATH entry and autostart all live in [`pushtotalk/setup.py`](../pushtotalk/setup.py)
and [`pushtotalk/doctor.py`](../pushtotalk/doctor.py).

They live there rather than in the shell script for three reasons: the checks reuse what
the app already knows (`config`, `recorder`, `asr`, `service`) instead of restating it in
another language; a failure is then reported by the same code that will hit it in
production; and the test suite covers it, which nothing written in PowerShell here does.
**If you are tempted to add project knowledge to `Setup.ps1`, it almost certainly belongs
in `setup.py` instead.**

## What the machine needs

| | Requirement | Why |
| --- | --- | --- |
| OS | Windows 10 1809+ or 11, x64 | low-level keyboard hook, SMTC media control |
| GPU | NVIDIA, driver **r570+** for RTX 50xx (r525+ otherwise) | `DEVICE = "cuda"`; there is no CPU fallback on purpose |
| VRAM | ~4 GB free | large-v3 in float16 |
| Disk | ~6 GB | 2.9 GB model, 2.2 GB venv, the rest headroom |
| Network | once, for the install | nothing leaves the machine afterwards |

Not needed, despite what you might expect: **no CUDA toolkit** (cuBLAS and cuDNN are pip
packages listed in `pyproject.toml`), **no ffmpeg** (PyAV bundles it), **no Python**
(uv fetches its own 3.13), **no admin rights** unless you want elevated autostart.

## Doing it

1. Get the project onto the new machine — into **a path with no spaces, outside
   OneDrive**. `D:\Git\ptt-whisper` is the reference location. Both constraints are checked
   and refused, not assumed: a space breaks the quoting of the autostart command, and
   OneDrive rewrites files underneath a running process.

   ```powershell
   git clone https://github.com/vlad230596/ptt-whisper.git D:\Git\ptt-whisper
   ```

   A clone gives you exactly the right thing, because everything rebuilt for you is
   gitignored: `.venv\`, `models\`, `logs\`, `__pycache__\`, `assets\*.ico`,
   `settings.json`. If you copy a working directory by hand instead, leave those behind —
   they only slow the copy down, and `settings.json` names *the other machine's*
   microphone. `dataset\` is the one worth carrying over deliberately: it is your own
   recordings, useful for tuning the term list, and it is what `tests\test_text.py` reads
   when present.

2. Open PowerShell in the project directory:

   ```powershell
   powershell -ExecutionPolicy Bypass -File deploy\Setup.ps1 -- --add-to-path --autostart --start
   ```

   `-ExecutionPolicy Bypass` is needed because the file is unsigned and freshly copied.
   Everything after `--` is passed through to `ptt setup`:

   | Flag | Effect |
   | --- | --- |
   | `--add-to-path` | project directory onto the user PATH, so `ptt` works anywhere |
   | `--autostart` | start with Windows (Run key) |
   | `--elevated` | with `--autostart`, use the elevated logon task instead; needs an elevated prompt once, and is what lets the hotkey work over elevated windows |
   | `--start` | launch it when setup succeeds |
   | `--skip-model` | leave `models\` alone, e.g. when you copied it over |
   | `--repo` | fetch a different model |

   With no flags at all, setup changes nothing outside the project directory: it is also
   the repair command, so re-running it on a working machine is safe and cheap.

3. Tell it which microphone to use. The check at the end almost certainly failed on this,
   because the default `MIC` in `pushtotalk\config.py` is the previous machine's device
   name:

   ```powershell
   .\ptt mic            # pick one from a list, with a level meter to prove it hears you
   .\ptt devices        # what this machine has, and what the choice resolves to
   .\ptt doctor         # re-run every check
   ```

   **The `.\` is not a typo, and it is only needed here.** `--add-to-path` writes the user
   PATH in the registry, which is read by processes when they *start*: the shell you ran
   setup from keeps the environment it was born with, so plain `ptt` there fails with
   `The term 'ptt' is not recognized`, and PowerShell does not fall back to the current
   directory either. Open a new terminal and `ptt <command>` works everywhere; stay in this
   one and prefix it. Setup's closing hints spell out whichever applies.

   `ptt mic` writes `settings.json`, which overrides `MIC` and takes effect at the next
   dictation — no restart. Editing `MIC` in `config.py` still works and is what a machine
   without a display would do.

4. Hold **F8**, say something, release. Text appears where the caret was.

## Doing it by hand

The bootstrap is three commands; setup is one more.

```powershell
winget install --id astral-sh.uv -e         # or: irm https://astral.sh/uv/install.ps1 | iex
cd D:\Git\ptt-whisper
uv sync                                     # fetches Python 3.13 and every dependency
uv run python -m pushtotalk setup --add-to-path --autostart --start
```

After that, `ptt <command>` for everything: `setup`, `doctor`, `devices`, `start`, `stop`,
`restart`, `status`, `autostart`, `run`.

## Moving your settings across

Everything tunable is in one file, `pushtotalk\config.py`. Rather than copying it wholesale
from the old machine, copy the parts that are actually yours:

- `HOTWORDS` — the project vocabulary you have been growing from misrecognitions.
- `HALLUCINATIONS` — any patterns you added beyond the shipped list.
- `PAUSE_MEDIA_APPS`, hotkey bindings, OSD appearance, if you changed them.

`MIC` is machine-specific and must not be copied — nor is `settings.json`, which holds
the microphone chosen on the *old* machine and would override the default on the new one.
Delete it, or run `ptt mic --reset`. `MODEL_DIR`, `LOG_DIR` and `DATA_DIR` are derived
from the project root, so they follow the directory wherever you put it.

## When something is wrong

`ptt doctor` first. It walks the same path the app does, in the same order, and prints what
it found at each step rather than a verdict. The cases it distinguishes:

| Symptom | Cause | Fix |
| --- | --- | --- |
| `The term 'ptt' is not recognized` | the PATH entry reaches only processes started after setup, and PowerShell never runs a command from the current directory | `.\ptt <command>` here, or open a new terminal |
| `microphone ... matches none of these` | device name differs, or the jack is unplugged — an HD Audio input endpoint disappears entirely when nothing is in the socket | `ptt mic` and pick one; the report says whether the name came from `settings.json` or `config.py` |
| `CUBLAS_STATUS_NOT_SUPPORTED` on the first decode | wrong compute type for the GPU | `COMPUTE_TYPE` must stay `float16`; int8 selects IMMA kernels that do not exist for sm_120 |
| `Library cublas64_12.dll is not found` | the pip CUDA packages did not install | `uv sync` again; see `pushtotalk\cudalibs.py` for why the DLLs are loaded by absolute path |
| model loads, decode hangs or crashes | driver too old for the GPU | update to r570+ on Blackwell |
| `model incomplete` | interrupted download, or an LFS pointer instead of the weights | `ptt setup` — it re-fetches only what is missing |
| F8 does nothing, no error anywhere | Windows unhooked a slow callback (`LowLevelHooksTimeout`, 300 ms), or another app grabbed F8 first | `ptt restart`; if it recurs, change `HOTKEY_REC` |
| works everywhere but in one window | that window is elevated | `ptt autostart on --elevated` from an elevated prompt |
| `ptt stop` cannot reach the instance | it is running elevated and you are not | use an elevated prompt, or Ctrl+Alt+Q |

The log is `logs\pushtotalk.log` (rotating, 1 MB × 4). Every dictation records the device
it opened, the durations and the resulting text, which is usually enough to tell a capture
problem from a recognition one.

## Uninstalling

```powershell
ptt stop
ptt autostart off      # removes both the Run key and the logon task
```

Then delete the directory. Nothing else is written anywhere: no registry beyond that one
autostart value (and the PATH entry, if you asked for it), no ProgramData, no per-user
application data.
