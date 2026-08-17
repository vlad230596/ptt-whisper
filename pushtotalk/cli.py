"""Command line: `pushtotalk <command>`, or `ptt <command>` via the wrapper.

`run` is the app itself in the foreground. Everything else talks to an instance that is
already running, or to the autostart entry. Kept free of heavy imports -- `status` and
`stop` must not spend a second loading audio and CUDA machinery to send one message, so
`app` is imported inside the `run` handler and nowhere else.
"""

from __future__ import annotations

import argparse
import sys

from . import __version__

# The parser needs this at build time, and model.py imports nothing heavier than pathlib
# at module level -- huggingface_hub is pulled in inside the download call itself.
from .model import DEFAULT_REPO as _DEFAULT_MODEL_REPO


def _run(_args: argparse.Namespace) -> int:
    from .app import main

    return main()


def _start(_args: argparse.Namespace) -> int:
    from . import service

    ok, message = service.start()
    print(message)
    return 0 if ok else 1


def _stop(args: argparse.Namespace) -> int:
    from . import service

    ok, message = service.stop(force=args.force)
    print(message)
    return 0 if ok else 1


def _restart(_args: argparse.Namespace) -> int:
    from . import service

    ok, message = service.restart()
    print(message)
    return 0 if ok else 1


def _status(_args: argparse.Namespace) -> int:
    from . import service

    print(service.status())
    return 0


def _autostart(args: argparse.Namespace) -> int:
    from . import service

    if args.state == "on":
        if args.elevated:
            ok, message = service.autostart_task_enable()
        else:
            ok, message = service.autostart_enable()
    elif args.state == "off":
        # Remove whichever is present; saying "off" must mean off, not "off for one of
        # the two mechanisms while the other still starts it".
        run_ok, run_message = service.autostart_disable()
        task_ok, task_message = service.autostart_task_disable()
        ok = run_ok or task_ok
        message = "\n".join(m for m, done in
                            ((run_message, run_ok), (task_message, task_ok)) if done) \
            or "autostart was not enabled"
    else:
        run_state, detail = service.autostart_status()
        task_state, _ = service.autostart_task_status()
        print(f"Run key (ordinary):    {run_state}" + (f"\n  {detail}" if detail else ""))
        print(f"logon task (elevated): {task_state}")
        return 0
    print(message)
    return 0 if ok else 1


def _setup(args: argparse.Namespace) -> int:
    from . import service, setup

    return setup.main(
        service.PROJECT_ROOT,
        repo=args.repo,
        skip_model=args.skip_model,
        add_to_path=args.add_to_path,
        autostart=args.autostart,
        elevated=args.elevated,
        start=args.start,
    )


def _doctor(_args: argparse.Namespace) -> int:
    from . import doctor, service

    return doctor.main(service.PROJECT_ROOT)


def _devices(_args: argparse.Namespace) -> int:
    """Available without a running instance, unlike the Ctrl+Alt+F8 hotkey."""
    from . import config as cfg
    from . import recorder, settings

    print(recorder.describe_devices())
    mic, source = settings.effective_mic()
    print(f"\nmicrophone = {mic!r} (from {source})")
    if source == settings.SETTINGS_SOURCE:
        print(f"  config.py default is {cfg.MIC!r}; `ptt mic` changes the choice")
    compute_type, ct_source = settings.effective_compute_type()
    print(f"compute_type = {compute_type!r} (from {ct_source})")
    if ct_source == settings.SETTINGS_SOURCE:
        print(f"  config.py default is {cfg.COMPUTE_TYPE!r}")
    batching, batching_source = settings.effective_batching()
    print(f"batching = {batching!r} (from {batching_source})")
    if batching_source == settings.SETTINGS_SOURCE:
        print(f"  config.py default is {cfg.BATCHING_ENABLED!r}")
    try:
        resolved = recorder.Recorder(mic, cfg.SAMPLE_RATE,
                                     cfg.HOST_API_ORDER).resolve_device()
    except recorder.DeviceNotFound as exc:
        print(f"  -> NO MATCH: {exc}")
        return 1
    print(f"  -> resolves to [{resolved['index']}] {resolved['name']} ({resolved['api']})")
    return 0


def _mic(args: argparse.Namespace) -> int:
    """The chooser, standalone: no model, no CUDA, just devices and a meter.

    A running instance picks the new choice up by itself, at the next dictation --
    settings.json is read on every key-down, so there is nothing to restart.
    """
    from . import micui, settings

    if args.reset:
        if not settings.clear_mic():
            return 1
        mic, source = settings.effective_mic()
        print(f"microphone = {mic!r} (from {source})")
        return 0

    mic, _source = settings.effective_mic()

    def apply(name: str) -> None:
        if not settings.set_mic(name):
            raise RuntimeError(f"could not write {settings.path()}")
        print(f"microphone = {name!r} (saved to {settings.path()})")

    if not micui.open_window(mic, apply):
        print("a microphone chooser is already open")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pushtotalk",
        description="Push-to-talk dictation. Hold F8, speak, release.",
        epilog="With no command, `run` is assumed.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="run in the foreground (logs to the console)").set_defaults(
        handler=_run
    )
    sub.add_parser("start", help="launch the background instance").set_defaults(
        handler=_start
    )
    stop = sub.add_parser("stop", help="ask the running instance to quit")
    stop.add_argument("--force", action="store_true",
                      help="terminate it if it does not shut down in time")
    stop.set_defaults(handler=_stop)
    sub.add_parser("restart", help="stop then start").set_defaults(handler=_restart)
    sub.add_parser("status", help="is it running, is autostart on").set_defaults(
        handler=_status
    )
    autostart = sub.add_parser("autostart", help="start with Windows (per user)")
    autostart.add_argument("state", nargs="?", default="status",
                           choices=["on", "off", "status"])
    autostart.add_argument(
        "--elevated", action="store_true",
        help="start it with full privileges via a logon task, so the hotkey also works "
             "over elevated windows; creating the task needs an elevated prompt once",
    )
    autostart.set_defaults(handler=_autostart)
    sub.add_parser(
        "devices", help="list input devices and resolve the chosen microphone"
    ).set_defaults(handler=_devices)

    mic = sub.add_parser("mic", help="choose the microphone, with a live level meter")
    mic.add_argument("--reset", action="store_true",
                     help="forget the choice and go back to MIC in config.py")
    mic.set_defaults(handler=_mic)

    # First run on a new machine. `deploy/Setup.ps1` calls this once uv and the venv
    # exist; everything it does is re-runnable, so it is also the repair command.
    install = sub.add_parser("setup", help="first-run setup: model, icons, checks")
    install.add_argument("--repo", default=_DEFAULT_MODEL_REPO,
                         help=f"model to fetch (default: {_DEFAULT_MODEL_REPO})")
    install.add_argument("--skip-model", action="store_true",
                         help="leave models/ alone, e.g. when it was copied over")
    install.add_argument("--add-to-path", action="store_true",
                         help="put the project directory on the user PATH so `ptt` works "
                              "from any prompt")
    install.add_argument("--autostart", action="store_true", help="start with Windows")
    install.add_argument("--elevated", action="store_true",
                         help="with --autostart, use the elevated logon task; needs an "
                              "elevated prompt once")
    install.add_argument("--start", action="store_true",
                         help="launch the app when setup succeeds")
    install.set_defaults(handler=_setup)

    sub.add_parser(
        "doctor", help="check the environment, gpu, model and microphone"
    ).set_defaults(handler=_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    handler = getattr(args, "handler", None)
    if handler is None:
        # Bare `python -m pushtotalk` (and the Windows autostart entry) means: run.
        return _run(args)
    return handler(args)
