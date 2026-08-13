"""The microphone chooser: a list of microphones and a live level meter.

Why a real GUI toolkit here and nowhere else. The OSD and the tray icon are hand-painted
GDI because they are a line of text and an icon, and because they must live on the same
thread and message loop as the keyboard hook. A meter that follows your voice is an
animation with a redraw every frame, and hand-painting that in ctypes would cost several
hundred lines for a bar that moves. Dear PyGui draws it in ten.

**Threading.** Dear PyGui owns a render loop and a single global context, and the main
thread here is not available -- it belongs to the keyboard hook, which Windows silently
unhooks if its callback is late. So the chooser runs on its own thread, and `app.py`
starts that thread rather than handing the work to the dictation worker: the worker
would be blocked for as long as the window stayed open, i.e. dictation would stop
working while you were choosing a microphone.

Measured on this machine before any of this was written, dearpygui 2.3.1, because none
of it is safe to assume:

* `create_context()` ... `destroy_context()` survives being repeated in one process, so
  the window can be opened, closed and opened again for the life of the app;
* it works from a secondary thread while the main thread runs its own Win32 message
  loop -- both cycles rendered 120 frames at ~40 fps with the hook loop pumping;
* it works under `pythonw.exe`, which is how the background instance runs.

And the trap the same measurements found: **any `dpg.*` call before `create_context()`
is an access violation that takes the process down** -- not an exception, nothing in the
log, exit code 0xC0000005. Not even `get_dearpygui_version()` is exempt. Hence the
import is lazy and everything below runs inside a live context.

Every callback takes the full `(sender, app_data, user_data)` even where it ignores all
three: Dear PyGui inspects the signature and passes as many as it fits, and a mismatch
surfaces as a callback that silently never runs.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from . import config as cfg
from . import recorder
from . import winapi as W

log = logging.getLogger(__name__)

# One window at a time, held for as long as it is up: a second Ctrl+Alt+M while the
# chooser is open must not create a second Dear PyGui context in the same process.
_open = threading.Lock()
_close_requested = threading.Event()

_TABLE_HOST = "device_table_host"
_BAR = "level_bar"
_READOUT = "level_readout"
_HINT = "level_hint"
_DEVICE_LINE = "device_line"
_FOOTER = "footer"


def is_open() -> bool:
    return _open.locked()


def request_close() -> None:
    """Ask an open chooser to shut down. Safe from any thread -- used by the Close
    button, by Escape, and by the tests."""
    _close_requested.set()


def open_window(
    mic: str,
    on_apply: Callable[[str], None] | None = None,
    *,
    sample_rate: int = cfg.SAMPLE_RATE,
    host_api_order: tuple[str, ...] = cfg.HOST_API_ORDER,
) -> bool:
    """Run the chooser on the calling thread until the window closes.

    False if a chooser is already open. `on_apply` receives the chosen device name and
    is what makes the choice stick; without it the window is a read-only meter.
    """
    if not _open.acquire(blocking=False):
        log.info("the microphone chooser is already open")
        return False
    _close_requested.clear()
    try:
        _Chooser(mic, on_apply, sample_rate, host_api_order).run()
    finally:
        _open.release()
    return True


def open_in_thread(
    mic: str, on_apply: Callable[[str], None] | None = None
) -> threading.Thread | None:
    """Same, on a thread of its own. None if a chooser is already open."""
    if is_open():
        return None

    def target() -> None:
        try:
            open_window(mic, on_apply)
        except Exception:
            log.exception("the microphone chooser failed")

    thread = threading.Thread(target=target, name="micui", daemon=True)
    thread.start()
    return thread


class _Chooser:
    def __init__(
        self,
        mic: str,
        on_apply: Callable[[str], None] | None,
        sample_rate: int,
        host_api_order: tuple[str, ...],
    ) -> None:
        self._configured = mic          # what the app records with right now
        self._selected = mic            # what is highlighted in the list
        self._on_apply = on_apply
        self._sample_rate = sample_rate
        self._host_api_order = host_api_order
        self._devices: list[dict] = []
        self._monitor: recorder.LevelMonitor | None = None
        self._hold_db = cfg.METER_FLOOR_DB
        self._hold_until = 0.0
        self._quiet_since = time.monotonic()
        # Filled in by run(), *after* the process is DPI aware: GetDpiForSystem reports
        # 96 to a process that is not, which would size the window for a 100 % display
        # on a 125 % one.
        self._scale = 1.0
        self._dpg = None
        self._theme_normal = 0
        self._theme_hot = 0

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> None:
        import dearpygui.dearpygui as dpg  # lazy: nothing may touch dpg before this

        # The app is already per-monitor DPI aware for the OSD's sake; a standalone
        # `ptt mic` is not, and Windows would scale the viewport into a blur. The scale
        # can only be read once that is true -- see __init__.
        W.set_dpi_aware()
        self._scale = W.system_dpi() / 96.0
        self._dpg = dpg
        dpg.create_context()
        try:
            self._build()
            while dpg.is_dearpygui_running() and not _close_requested.is_set():
                self._tick()
                dpg.render_dearpygui_frame()
        finally:
            self._stop_monitor()
            dpg.destroy_context()
            self._dpg = None
            log.info("microphone chooser closed")

    def _build(self) -> None:
        dpg = self._dpg
        width, height = cfg.MIC_WINDOW_SIZE
        icon = cfg.ICON_IDLE if Path(cfg.ICON_IDLE).is_file() else ""
        # ASCII only in the title: the viewport title does not go through the font
        # below, and a non-ASCII character came back as mojibake in the title bar.
        dpg.create_viewport(
            title="PushToTalk - microphone",
            width=int(width * self._scale), height=int(height * self._scale),
            min_width=520, min_height=420, small_icon=icon, large_icon=icon,
        )
        dpg.setup_dearpygui()
        self._load_font()
        self._make_themes()
        with dpg.window(tag="main"):
            dpg.add_text("Recording device")
            dpg.add_text(
                "The host API is not a choice: each microphone is opened through the "
                f"first of {', '.join(self._host_api_order)} that accepts it.",
                color=(150, 150, 150), wrap=int(760 * self._scale),
            )
            dpg.add_spacer(height=4)
            with dpg.child_window(tag=_TABLE_HOST, height=int(210 * self._scale)):
                pass
            dpg.add_spacer(height=6)
            dpg.add_text("", tag=_DEVICE_LINE, color=(150, 150, 150),
                         wrap=int(780 * self._scale))
            dpg.add_spacer(height=6)
            dpg.add_text("Level - speak, and the bar should follow your voice")
            dpg.add_progress_bar(tag=_BAR, default_value=0.0, width=-1,
                                 height=int(26 * self._scale), overlay="")
            dpg.add_text("", tag=_READOUT, color=(150, 150, 150))
            dpg.add_text("", tag=_HINT, wrap=int(780 * self._scale))
            dpg.add_spacer(height=10)
            with dpg.group(horizontal=True):
                dpg.add_button(label="Use this microphone", width=int(190 * self._scale),
                               callback=self._on_apply_clicked)
                dpg.add_button(label="Rescan", width=int(100 * self._scale),
                               callback=self._on_rescan_clicked)
                dpg.add_button(label="Close", width=int(100 * self._scale),
                               callback=self._on_close_clicked)
            dpg.add_spacer(height=6)
            dpg.add_text("", tag=_FOOTER, color=(150, 150, 150),
                         wrap=int(780 * self._scale))

        with dpg.handler_registry():
            dpg.add_key_press_handler(dpg.mvKey_Escape, callback=self._on_close_clicked)

        dpg.set_primary_window("main", True)
        dpg.show_viewport()
        self._populate()
        self._footer(f"In use: {recorder.display_name(self._configured)}")

    def _load_font(self) -> None:
        """Segoe UI, or the built-in font and a note in the log.

        Dear PyGui's built-in font is ASCII-only, and a device name it cannot draw comes
        out as a row of question marks -- which on a Russian Windows is every microphone
        ("Микрофон (Realtek High Definition Audio)"). Segoe UI covers them; 2.x builds
        the glyph ranges automatically, so no range hints are needed (they are deprecated
        no-ops there and warn if called).

        Loading the font at a size already multiplied by the DPI scale is also what keeps
        the window sharp, so this replaces `set_global_font_scale` rather than adding to
        it -- doing both would scale twice.
        """
        dpg = self._dpg
        path = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts" / "segoeui.ttf"
        if not path.is_file():
            log.info("%s not found; falling back to the built-in ASCII font", path)
            dpg.set_global_font_scale(self._scale)
            return
        with dpg.font_registry():
            font = dpg.add_font(str(path), int(16 * self._scale))
        dpg.bind_font(font)

    def _make_themes(self) -> None:
        dpg = self._dpg

        def bar_theme(color: tuple[int, int, int]) -> int:
            # A progress bar's fill is ImGuiCol_PlotHistogram.
            with dpg.theme() as theme:
                with dpg.theme_component(dpg.mvProgressBar):
                    dpg.add_theme_color(dpg.mvThemeCol_PlotHistogram, color)
            return theme

        self._theme_normal = bar_theme((70, 175, 100))
        self._theme_hot = bar_theme((215, 75, 60))

    # ------------------------------------------------------------------ device list
    def _populate(self) -> None:
        """(Re)build the device table. Also the Rescan button: a USB microphone plugged
        in while the window is open should become selectable without reopening it."""
        dpg = self._dpg
        dpg.delete_item(_TABLE_HOST, children_only=True)
        try:
            self._devices = recorder.physical_devices(self._host_api_order)
        except Exception as exc:  # noqa: BLE001 -- PortAudio raises its own types
            log.exception("enumerating input devices failed")
            self._devices = []
            self._stop_monitor()
            dpg.add_text(f"Could not list input devices: {exc}", parent=_TABLE_HOST,
                         color=(230, 120, 110))
            return
        if not self._devices:
            self._stop_monitor()
            self._selected = ""
            dpg.add_text("No input devices at all - is a microphone plugged in and "
                         "enabled in Windows?", parent=_TABLE_HOST, color=(230, 120, 110))
            return

        with dpg.table(parent=_TABLE_HOST, header_row=True, row_background=True,
                       policy=dpg.mvTable_SizingStretchProp, resizable=True):
            dpg.add_table_column(label="Microphone")
            dpg.add_table_column(label="Host API", width_fixed=True,
                                 init_width_or_weight=int(130 * self._scale))
            dpg.add_table_column(label="Ch", width_fixed=True,
                                 init_width_or_weight=int(40 * self._scale))
            dpg.add_table_column(label="Rate", width_fixed=True,
                                 init_width_or_weight=int(100 * self._scale))
            for index, device in enumerate(self._devices):
                with dpg.table_row():
                    dpg.add_selectable(
                        label=recorder.display_name(device["name"]),
                        tag=self._row_tag(index),
                        span_columns=True, callback=self._on_row_clicked,
                        user_data=index,
                    )
                    dpg.add_text(device["api"])
                    dpg.add_text(str(device["channels"]))
                    dpg.add_text(f"{device['default_samplerate']:.0f} Hz")

        # Keep the highlight where it was; if that device has gone away, fall back to
        # the configured one and then to the first, rather than metering nothing.
        chosen = self._index_of(self._selected)
        if chosen is None:
            chosen = self._index_of(self._configured)
        self._select(0 if chosen is None else chosen)

    @staticmethod
    def _row_tag(index: int) -> str:
        return f"device_row_{index}"

    def _index_of(self, name: str) -> int | None:
        if not name:
            return None
        for index, device in enumerate(self._devices):
            if recorder._matches(device["name"], name):
                return index
        return None

    def _select(self, index: int) -> None:
        dpg = self._dpg
        for other in range(len(self._devices)):
            tag = self._row_tag(other)
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, other == index)
        device = self._devices[index]
        self._selected = device["name"]

        rate_note = ("" if int(device["default_samplerate"]) == self._sample_rate
                     else f", resampled to {self._sample_rate} Hz for recognition")
        others = [api for api in device["apis"] if api != device["api"]]
        also = f"   |   also exposed by {', '.join(others)}" if others else ""
        # ASCII in everything this file writes: the loaded font covers Latin-1 and
        # Cyrillic (for device names), not General Punctuation, so an em dash or a
        # middle dot would draw as a question mark. Measured, not assumed.
        dpg.set_value(
            _DEVICE_LINE,
            f"[{device['index']}] {recorder.display_name(device['name'])} - "
            f"{device['api']} at {device['default_samplerate']:.0f} Hz{rate_note}{also}",
        )
        self._start_monitor()

    # ------------------------------------------------------------------ metering
    def _start_monitor(self) -> None:
        dpg = self._dpg
        self._stop_monitor()
        self._hold_db = cfg.METER_FLOOR_DB
        self._quiet_since = time.monotonic()
        monitor = recorder.LevelMonitor(self._selected, self._sample_rate,
                                        self._host_api_order)
        if not monitor.start():
            self._monitor = None
            dpg.set_value(_BAR, 0.0)
            dpg.configure_item(_BAR, overlay="")
            dpg.set_value(_READOUT, "")
            self._hint(f"Cannot open this device: {monitor.error}", (230, 120, 110))
            return
        self._monitor = monitor

    def _stop_monitor(self) -> None:
        monitor, self._monitor = self._monitor, None
        if monitor is not None:
            monitor.stop()

    def _tick(self) -> None:
        if self._monitor is None:
            return
        dpg = self._dpg
        level, peak = self._monitor.read()
        floor = cfg.METER_FLOOR_DB
        level_db = recorder.dbfs(level, floor)
        peak_db = recorder.dbfs(peak, floor)

        now = time.monotonic()
        if peak_db >= self._hold_db or now >= self._hold_until:
            self._hold_db = peak_db
            self._hold_until = now + cfg.METER_PEAK_HOLD_MS / 1000

        dpg.set_value(_BAR, max(0.0, min(1.0, (level_db - floor) / -floor)))
        dpg.configure_item(_BAR, overlay=f"{level_db:5.1f} dBFS")
        dpg.bind_item_theme(
            _BAR, self._theme_hot if peak_db >= cfg.METER_CLIP_DB else self._theme_normal
        )
        dpg.set_value(
            _READOUT,
            f"peak {peak_db:6.1f} dBFS   |   peak hold {self._hold_db:6.1f} dBFS"
            f"   |   rms {level:.5f}",
        )

        # MIN_RMS is the threshold the dictation path uses to decide that nothing was
        # captured at all, so the wording here matches what a real utterance would say.
        if level >= cfg.MIN_RMS:
            self._quiet_since = now
        if peak_db >= cfg.METER_CLIP_DB:
            self._hint("Too hot - the input is clipping. Turn the level down in the "
                       "Windows sound settings.", (240, 190, 90))
        elif now - self._quiet_since > 2.0:
            self._hint(f"Silence - nothing above the {cfg.MIN_RMS} threshold that "
                       f"dictation treats as 'no audio at all'. Muted, or the wrong "
                       f"device?", (240, 190, 90))
        else:
            self._hint("Signal is arriving.", (120, 200, 130))

    def _hint(self, message: str, color: tuple[int, int, int]) -> None:
        self._dpg.set_value(_HINT, message)
        self._dpg.configure_item(_HINT, color=color)

    def _footer(self, message: str) -> None:
        self._dpg.set_value(_FOOTER, message)

    # ------------------------------------------------------------------ callbacks
    def _on_row_clicked(self, sender=None, app_data=None, user_data=None) -> None:
        self._select(int(user_data))

    def _on_rescan_clicked(self, sender=None, app_data=None, user_data=None) -> None:
        self._populate()

    def _on_close_clicked(self, sender=None, app_data=None, user_data=None) -> None:
        request_close()

    def _on_apply_clicked(self, sender=None, app_data=None, user_data=None) -> None:
        if not self._selected:
            self._footer("Nothing selected.")
            return
        if self._on_apply is not None:
            try:
                self._on_apply(self._selected)
            except Exception as exc:  # noqa: BLE001 -- shown in the window, not swallowed
                log.exception("applying the microphone choice failed")
                self._footer(f"Could not save the choice: {exc}")
                return
        self._configured = self._selected
        self._footer(f"In use: {recorder.display_name(self._configured)}")
        request_close()
