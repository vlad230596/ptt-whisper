"""First-run setup, owned by the app rather than by a shell script.

    ptt setup [--add-to-path] [--autostart [--elevated]]

`deploy/Setup.ps1` does the one thing that cannot be done from here -- put uv and a
Python interpreter on a machine that has neither -- and then hands over to this. Anything
that needs to know about the project belongs here instead of there, because here it can
reuse what the app already knows (`config`, `recorder`, `asr`, `service`), and because
here it is covered by the test suite. PowerShell in this project is not.

Deliberately *not* done here: writing settings back to disk. Choosing a microphone is the
one step every new machine needs and the one thing a copied `config.py` always gets
wrong, so setup resolves and reports it -- but persisting it belongs to the settings work
in flight, not to a second mechanism invented here.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config as cfg
from . import doctor, model, service


def _title(text: str) -> None:
    print()
    print("=" * 74)
    print(f"  {text}")
    print("=" * 74)


def _info(text: str) -> None:
    print(f"  . {text}")


def _good(text: str) -> None:
    print(f"  + {text}")


def _note(text: str) -> None:
    print(f"  ! {text}")


# ------------------------------------------------------------------ path entry
def path_with(entry: str, current: str) -> str | None:
    """`current` PATH plus `entry`, or None if it is already there.

    Split out and case-insensitive because the comparison is the whole content of the
    step: appending a duplicate every time setup re-runs is how a PATH ends up with the
    same directory in it nine times.
    """
    wanted = entry.rstrip("\\").lower()
    for part in current.split(";"):
        if part.strip().rstrip("\\").lower() == wanted:
            return None
    separator = "" if current.endswith(";") or not current else ";"
    return f"{current}{separator}{entry}"


def _add_to_path(root: Path) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        try:
            current, kind = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current, kind = "", winreg.REG_EXPAND_SZ
        updated = path_with(str(root), current)
        if updated is None:
            _good("already on the user PATH")
            return
        winreg.SetValueEx(key, "Path", 0, kind, updated)
    _good(f"added {root} to the user PATH -- new shells only, this one keeps the old copy")


# ------------------------------------------------------------------ steps
def _step_machine(root: Path) -> doctor.Report:
    _title("1. Checking the machine")
    report = doctor.Report()
    doctor.check_environment(report, root)
    doctor.check_gpu(report)
    free = shutil.disk_usage(root).free / 2**30
    # Only count what is still to be downloaded: on a re-run the model is already there
    # and warning about its size again is noise.
    needed = 1.0 if not model.missing(Path(cfg.MODEL_DIR)) else 3.9
    if free < needed:
        report.warn(f"only {free:.1f} GB free -- this run needs about {needed:.1f} GB")
    else:
        report.ok(f"{free:.1f} GB free, this run needs about {needed:.1f} GB")
    return report


def _step_model(repo: str, skip: bool) -> bool:
    _title("2. Model")
    directory = Path(cfg.MODEL_DIR)
    if skip:
        _info("skipped (--skip-model)")
        return True
    gaps = model.missing(directory)
    if not gaps:
        _good(f"already present: {directory} ({model.size_gb(directory):.2f} GB)")
        return True
    _info(f"missing: {', '.join(gaps)}")
    _info(f"downloading {repo} -> {directory} (about 2.9 GB, this takes a while)")
    ok, message = model.ensure(directory, repo)
    if ok:
        _good(message)
    else:
        _note(message)
    return ok


def _step_icons(root: Path) -> None:
    _title("3. Tray icons")
    if Path(cfg.ICON_IDLE).is_file() and Path(cfg.ICON_RECORDING).is_file():
        _good("already generated")
        return
    script = root / "tools" / "make_icon.py"
    if not script.is_file():
        _note(f"{script} is missing; the tray falls back to the stock Windows icon")
        return
    # A subprocess rather than an import: make_icon is a build-time tool that happens to
    # need numpy, and the package should not start importing out of tools/.
    result = subprocess.run([sys.executable, str(script)], cwd=str(root))
    if result.returncode == 0:
        _good("generated assets/pushtotalk{,-rec}.ico")
    else:
        _note(f"icon generation exited {result.returncode}; the tray falls back to the "
              f"stock Windows icon")


def _step_path(root: Path, add: bool) -> bool:
    """Do the step; return True if `ptt` will not resolve in *this* shell.

    Which is the question the closing hints need answered, and the registry cannot answer
    it: a PATH entry is read by a process when it starts, so writing one now says nothing
    about the shell already running. `os.environ` does say — and `path_with` returning None
    for "already there" is exactly the test, reused rather than restated.
    """
    _title("4. Making `ptt` available from any prompt")
    if add:
        _add_to_path(root)
    else:
        _info(f"not added; re-run with --add-to-path, or call {root}\\ptt.cmd by path")
    return path_with(str(root), os.environ.get("PATH", "")) is not None


def _step_autostart(enable: bool, elevated: bool) -> None:
    _title("5. Autostart")
    if not enable:
        _info("not enabled; run `ptt autostart on` when you want it")
        return
    if elevated:
        ok, message = service.autostart_task_enable()
    else:
        ok, message = service.autostart_enable()
    (_good if ok else _note)(message)


def _step_checks(root: Path) -> doctor.Report:
    _title("6. Checking the install end to end")
    return doctor.run(root)


# ------------------------------------------------------------------ entry point
def main(
    root: Path,
    *,
    repo: str = model.DEFAULT_REPO,
    skip_model: bool = False,
    add_to_path: bool = False,
    autostart: bool = False,
    elevated: bool = False,
    start: bool = False,
) -> int:
    print(f"\n  PushToTalk setup\n  target: {root}")

    machine = _step_machine(root)
    if machine.failures:
        # A space in the path or a missing venv makes every later step meaningless, and
        # both are fixed by moving or re-syncing rather than by pressing on.
        print("\nStopping: fix the failures above and run `ptt setup` again.")
        return 1

    if not _step_model(repo, skip_model):
        print("\nStopping: without the model there is nothing to check.")
        return 1
    _step_icons(root)
    needs_prefix = _step_path(root, add_to_path)
    _step_autostart(autostart, elevated)
    report = _step_checks(root)

    print("=" * 74)
    # The GPU and driver are checked twice -- once in step 1, once by `doctor` in step 6 --
    # so the same warning arrives from both and would be listed, and counted, twice.
    warnings = list(dict.fromkeys(machine.warnings + report.warnings))
    if report.failures:
        print(f"  Setup finished; {len(report.failures)} thing(s) still need attention:")
        for problem in report.failures:
            print(f"    - {problem.splitlines()[0]}")
        if warnings:
            print(f"  ...and {len(warnings)} worth a look, but nothing that stops it:")
    elif warnings:
        print(f"  Ready, with {len(warnings)} thing(s) worth a look:")
    else:
        print(f"  Ready. Hold {cfg.HOTKEY_REC} and speak.")
    for warning in warnings:
        print(f"    - {warning.splitlines()[0]}")

    if start and not report.failures:
        ok, message = service.start()
        print(f"\n  {message}")

    # `ptt` resolves through the PATH entry, which reaches new processes only -- so in the
    # shell setup was just run from, the one place these hints are read, the bare name is
    # exactly what does not work. PowerShell will not run `ptt` from the current directory
    # either, so the fallback has to be spelled `.\ptt`.
    prefix = ".\\" if needs_prefix else ""
    hints = []
    if any(problem.lower().startswith("microphone") for problem in report.failures):
        hints.append((f"{prefix}ptt mic",
                      "choose a microphone -- the default is the developer's device"))
    else:
        hints.append((f"{prefix}ptt devices", "list microphones and resolve the chosen one"))
    hints += [
        (f"{prefix}ptt doctor", "re-run these checks"),
        (f"{prefix}ptt start", "run it in the background"),
        (f"{cfg.HOTKEY_REC} (hold)", "dictate"),
    ]

    print("\n  Next:")
    for command, what in hints:
        print(f"    {command:<18}{what}")
    if needs_prefix:
        print(f"\n  The `.\\` is not decoration: this shell still has the PATH it started"
              f"\n  with. In a terminal opened from now on, plain `ptt <command>` works.")
    print()
    return 1 if report.failures else 0
