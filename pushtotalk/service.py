"""Start, stop and autostart the background instance.

**Not a Windows service, deliberately.** A real service runs in session 0, which cannot
install a keyboard hook for the interactive desktop, cannot see the foreground window and
cannot paste into it. Every one of this program's jobs is session-bound, so what is
managed here is an ordinary detached user process -- with the start/stop/status/autostart
surface a service would have given you.

Finding the running instance needs no PID file: the tray icon's hidden window has a
registered class name, and one exists if and only if the app is up. That also makes
stopping it graceful -- WM_CLOSE goes through the app's own shutdown path, which removes
the tray icon and stops any recording in progress.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
import winreg
from pathlib import Path

from . import winapi as W
from .tray import CLASS_NAME

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "PushToTalk"

DETACHED_PROCESS = 0x00000008
CREATE_NO_WINDOW = 0x08000000

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def pythonw() -> Path:
    """The windowed interpreter of the environment we are running in."""
    candidate = Path(sys.prefix) / "Scripts" / "pythonw.exe"
    if candidate.is_file():
        return candidate
    candidate = Path(sys.executable).with_name("pythonw.exe")
    if candidate.is_file():
        return candidate
    return Path(sys.executable)  # last resort: a console will flash


def autostart_command() -> str:
    return f'"{pythonw()}" -m pushtotalk run'


# ------------------------------------------------------------------ discovery
def find_instance() -> int:
    """The running instance's hidden tray window, or 0."""
    return W.FindWindowW(CLASS_NAME, None) or 0


def pid_of(hwnd: int) -> int:
    value = ctypes.c_ulong()
    W.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
    return value.value


def is_running() -> bool:
    return find_instance() != 0


_ELEVATED_ADVICE = ("it runs at a higher integrity level (started elevated); use an "
                    "elevated prompt, or quit it with Ctrl+Alt+Q or its tray menu")


def reachable(pid: int) -> tuple[bool, str]:
    """Whether this process may post window messages to that one.

    Compares mandatory integrity levels rather than probing with a message. Probing does
    not work: UIPI lets harmless messages such as WM_NULL through while blocking
    WM_CLOSE, so a probe reports success and the real command is then refused.

    Fails open -- if the level cannot be read, say yes and let the actual attempt report
    the precise error.
    """
    mine = W.current_integrity_level()
    theirs = W.process_integrity_level(pid)
    if mine is None or theirs is None:
        return True, ""
    if theirs > mine:
        return False, _ELEVATED_ADVICE
    return True, ""


# ------------------------------------------------------------------ lifecycle
def start(timeout: float = 20.0) -> tuple[bool, str]:
    if is_running():
        return False, "already running"
    interpreter = pythonw()
    subprocess.Popen(
        [str(interpreter), "-m", "pushtotalk", "run"],
        cwd=str(PROJECT_ROOT),
        creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hwnd = find_instance()
        if hwnd:
            return True, (f"started (pid {pid_of(hwnd)}); the model loads in the "
                          f"background, the hotkey already works")
        time.sleep(0.1)
    return False, ("the process was launched but never registered its window -- "
                   f"see {PROJECT_ROOT / 'logs' / 'pushtotalk.log'}")


def stop(timeout: float = 10.0, *, force: bool = False) -> tuple[bool, str]:
    hwnd = find_instance()
    if not hwnd:
        return False, "not running"
    pid = pid_of(hwnd)
    ok, why = reachable(pid)
    if not ok:
        return False, f"cannot stop pid {pid}: {why}"
    # Goes through the app's own shutdown: tray icon removed, recording aborted.
    ctypes.set_last_error(0)
    if not W.PostMessageW(hwnd, W.WM_CLOSE, 0, 0):
        error = ctypes.get_last_error()
        advice = f" -- {_ELEVATED_ADVICE}" if error == W.ERROR_ACCESS_DENIED else ""
        return False, f"WM_CLOSE to pid {pid} was refused (error {error}){advice}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_running():
            return True, f"stopped (pid {pid})"
        time.sleep(0.1)
    if not force:
        return False, (f"pid {pid} did not shut down within {timeout:.0f}s; "
                       f"retry with --force to terminate it")
    try:
        os.kill(pid, 9)
    except PermissionError:
        return False, (f"not allowed to terminate pid {pid} -- it runs at a higher "
                       f"integrity level; use an elevated prompt")
    except OSError as exc:
        return False, f"could not terminate pid {pid}: {exc}"
    return True, (f"terminated pid {pid} -- the tray icon may linger until you "
                  f"hover over it")


def restart() -> tuple[bool, str]:
    if is_running():
        ok, message = stop()
        if not ok:
            return False, f"restart aborted: {message}"
    return start()


def status() -> str:
    hwnd = find_instance()
    lines = []
    if hwnd:
        pid = pid_of(hwnd)
        lines.append(f"running: yes (pid {pid})")
        ok, why = reachable(pid)
        lines.append(f"controllable from here: {'yes' if ok else 'no -- ' + why}")
    else:
        lines.append("running: no")
    state, detail = autostart_status()
    lines.append(f"autostart (Run key, ordinary): {state}"
                 + (f" -- {detail}" if detail else ""))
    task_state, _ = autostart_task_status()
    lines.append(f"autostart (logon task, elevated): {task_state}")
    lines.append(f"interpreter: {pythonw()}")
    return "\n".join(lines)


# ------------------------------------------------------------------ autostart
# Two mechanisms, because they are not interchangeable:
#
# * the Run key starts the app as an ordinary process. Simple, no admin needed, but the
#   result cannot install a hook over windows that are themselves elevated.
# * a scheduled task with "run with highest privileges" starts it elevated without a UAC
#   prompt at every login, which the Run key cannot do at all. Creating the task needs an
#   elevated prompt once.
#
# Which one you want depends on whether you dictate into elevated windows. `status`
# reports both so a machine cannot end up quietly running the weaker one.
TASK_NAME = "PushToTalk"


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args], capture_output=True, text=True,
        encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )


def autostart_task_status() -> tuple[str, str]:
    result = _schtasks("/query", "/tn", TASK_NAME)
    if result.returncode != 0:
        return "disabled", ""
    return "enabled", ""


def autostart_task_enable(*, elevated: bool = True) -> tuple[bool, str]:
    """Create the logon task. `elevated=False` exists so the mechanism is testable
    without admin rights -- the only difference is the privilege level."""
    command = autostart_command()
    args = ["/create", "/tn", TASK_NAME, "/tr", command, "/sc", "onlogon", "/f"]
    if elevated:
        args += ["/rl", "highest"]
    result = _schtasks(*args)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"schtasks exited {result.returncode}"
        if elevated:
            message += " -- creating an elevated logon task requires an elevated prompt"
        return False, message
    return True, f"elevated autostart task created: {command}"


def autostart_task_disable() -> tuple[bool, str]:
    result = _schtasks("/delete", "/tn", TASK_NAME, "/f")
    if result.returncode != 0:
        return False, "no autostart task to remove"
    return True, "autostart task removed"


def autostart_enable() -> tuple[bool, str]:
    command = autostart_command()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command)
    return True, f"autostart enabled: {command}"


def autostart_disable() -> tuple[bool, str]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, RUN_VALUE)
    except FileNotFoundError:
        return False, "autostart was not enabled"
    return True, "autostart disabled"


def autostart_status() -> tuple[str, str]:
    """Returns (state, detail) where state is enabled / disabled / stale."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            current, _kind = winreg.QueryValueEx(key, RUN_VALUE)
    except FileNotFoundError:
        return "disabled", ""
    expected = autostart_command()
    if current.strip().lower() == expected.strip().lower():
        return "enabled", ""
    # Points at something else -- a moved venv or an older install. Worth saying so
    # rather than reporting a plain "enabled" that starts the wrong thing.
    return "stale", f"registry says {current!r}, expected {expected!r}"
