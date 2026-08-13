"""The command line and the autostart entry.

The autostart tests write to the real `HKCU\\...\\Run` key, because that is the thing
being tested. Whatever was there before is saved and put back, including "nothing".
"""

from __future__ import annotations

import winreg

import pytest

from pushtotalk import service
from pushtotalk.cli import build_parser


# ------------------------------------------------------------------ parser
def _handler_name(argv: list[str]) -> str:
    args = build_parser().parse_args(argv)
    return getattr(args, "handler").__name__


@pytest.mark.parametrize(
    ("argv", "handler"),
    [
        (["run"], "_run"),
        (["start"], "_start"),
        (["stop"], "_stop"),
        (["restart"], "_restart"),
        (["status"], "_status"),
        (["autostart"], "_autostart"),
        (["autostart", "on"], "_autostart"),
        (["devices"], "_devices"),
        (["mic"], "_mic"),
        (["mic", "--reset"], "_mic"),
        (["setup"], "_setup"),
        (["setup", "--add-to-path", "--autostart"], "_setup"),
        (["doctor"], "_doctor"),
    ],
)
def test_commands_reach_their_handler(argv: list[str], handler: str) -> None:
    assert _handler_name(argv) == handler


def test_setup_defaults_are_all_off() -> None:
    """Setup must not touch the PATH, the registry or the running state unless asked:
    it is also the repair command, run on a machine that is already configured."""
    args = build_parser().parse_args(["setup"])
    assert (args.add_to_path, args.autostart, args.elevated, args.start,
            args.skip_model) == (False, False, False, False, False)
    assert args.repo


def test_mic_defaults_to_opening_the_chooser_not_resetting() -> None:
    assert build_parser().parse_args(["mic"]).reset is False
    assert build_parser().parse_args(["mic", "--reset"]).reset is True


def test_bare_invocation_means_run() -> None:
    """`pythonw -m pushtotalk` is what the autostart entry runs; it must start the app."""
    args = build_parser().parse_args([])
    assert getattr(args, "handler", None) is None  # falls through to run() in main()


def test_stop_takes_force() -> None:
    assert build_parser().parse_args(["stop", "--force"]).force is True
    assert build_parser().parse_args(["stop"]).force is False


def test_autostart_rejects_nonsense() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["autostart", "maybe"])


def test_unknown_command_is_rejected() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["frobnicate"])


# ------------------------------------------------------------------ autostart
@pytest.fixture
def clean_run_key():
    """Save the Run value, hand the test a blank slate, put it back afterwards."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, service.RUN_KEY) as key:
            previous = winreg.QueryValueEx(key, service.RUN_VALUE)[0]
    except FileNotFoundError:
        previous = None
    service.autostart_disable()
    yield
    service.autostart_disable()
    if previous is not None:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, service.RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, service.RUN_VALUE, 0, winreg.REG_SZ, previous)


def test_autostart_round_trip(clean_run_key) -> None:
    assert service.autostart_status()[0] == "disabled"
    ok, _ = service.autostart_enable()
    assert ok
    assert service.autostart_status()[0] == "enabled"
    ok, _ = service.autostart_disable()
    assert ok
    assert service.autostart_status()[0] == "disabled"


def test_disabling_twice_is_reported_not_raised(clean_run_key) -> None:
    service.autostart_enable()
    assert service.autostart_disable()[0] is True
    assert service.autostart_disable()[0] is False


def test_a_value_pointing_elsewhere_is_stale_not_enabled(clean_run_key) -> None:
    """A moved venv must not read as a working autostart entry."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, service.RUN_KEY, 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, service.RUN_VALUE, 0, winreg.REG_SZ,
                          r'"C:\Old\venv\Scripts\pythonw.exe" -m pushtotalk run')
    state, detail = service.autostart_status()
    assert state == "stale"
    assert "expected" in detail


def test_autostart_command_uses_the_windowed_interpreter() -> None:
    """A console interpreter here would flash a window at every login."""
    command = service.autostart_command()
    assert command.lower().startswith('"')
    assert "pythonw.exe" in command.lower()
    assert command.endswith("-m pushtotalk run")


def test_integrity_level_of_this_process_is_readable() -> None:
    """The elevation check degrades to "assume reachable"; make sure it is not doing
    that here, or the check would be silently useless."""
    from pushtotalk import winapi as W

    assert W.current_integrity_level() is not None
