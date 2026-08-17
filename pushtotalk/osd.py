"""The on-screen status line ("REC ...", "Transcribing ...", errors).

A borderless, click-through, non-activating topmost window painted with GDI. This
replaces AutoHotkey's ToolTip and is deliberately not tkinter: the window lives on the
same thread as the keyboard hook and is driven by the same message loop, so there is no
second event loop and no cross-thread UI marshalling to get wrong.

`show()` is safe to call from any thread -- it stores the text and posts a message.
"""

from __future__ import annotations

import ctypes
import logging
import threading

from . import config as cfg
from . import winapi as W

log = logging.getLogger(__name__)

_WM_SHOW = W.WM_APP + 1
_WM_HIDE = W.WM_APP + 2
_TIMER_ID = 1
_PADDING = 10  # px at 96 dpi
_BAR_HEIGHT = 4  # px at 96 dpi
_BAR_GAP = 6  # px at 96 dpi, between the text and the bar
_CLASS_NAME = "PushToTalkOSD"


class StatusWindow:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._text = ""
        self._timeout = 0
        self._error = False
        self._progress: float | None = None
        self._fonts: dict[int, int] = {}
        self._proc = W.WNDPROC(self._wndproc)
        self._hwnd = self._create_window()
        self._bg_brush = W.CreateSolidBrush(W.rgb(cfg.OSD_BG))
        self._track_brush = W.CreateSolidBrush(W.rgb(cfg.OSD_PROGRESS_BG))
        self._fill_brush = W.CreateSolidBrush(W.rgb(cfg.OSD_PROGRESS_FG))

    # ------------------------------------------------------------------ window
    def _create_window(self) -> int:
        wc = W.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(W.WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = W.GetModuleHandleW(None)
        wc.hCursor = W.LoadCursorW(None, W.int_resource(W.IDC_ARROW))
        wc.lpszClassName = _CLASS_NAME
        if not W.RegisterClassExW(ctypes.byref(wc)):
            W.raise_last_error("RegisterClassEx(OSD)")
        hwnd = W.CreateWindowExW(
            W.WS_EX_TOPMOST | W.WS_EX_TOOLWINDOW | W.WS_EX_NOACTIVATE
            | W.WS_EX_TRANSPARENT | W.WS_EX_LAYERED,
            _CLASS_NAME, _CLASS_NAME, W.WS_POPUP,
            0, 0, 10, 10, None, None, wc.hInstance, None,
        )
        if not hwnd:
            W.raise_last_error("CreateWindowEx(OSD)")
        W.SetLayeredWindowAttributes(hwnd, 0, cfg.OSD_ALPHA, W.LWA_ALPHA)
        return hwnd

    def _font(self, dpi: int) -> int:
        """A font per DPI, cached -- the OSD follows the pointer across monitors."""
        hfont = self._fonts.get(dpi)
        if hfont is None:
            height = -(cfg.OSD_FONT_PT * dpi) // 72
            hfont = W.CreateFontW(height, 0, 0, 0, 400, 0, 0, 0,
                                  1, 0, 0, 5, 0, cfg.OSD_FONT)  # DEFAULT_CHARSET, CLEARTYPE
            self._fonts[dpi] = hfont
        return hfont

    # ------------------------------------------------------------------ public
    def show(self, text: str, timeout_ms: int = 1500, *, error: bool = False,
              progress: float | None = None) -> None:
        """Display `text`. timeout_ms=0 keeps it up until the next show()/hide().

        `progress`, 0..1, draws a filled bar under the text instead of just the number
        in it -- pass None (the default) for a plain status line with no bar.
        """
        with self._lock:
            self._text = text
            self._timeout = timeout_ms
            self._error = error
            self._progress = progress
        W.PostMessageW(self._hwnd, _WM_SHOW, 0, 0)

    def hide(self) -> None:
        W.PostMessageW(self._hwnd, _WM_HIDE, 0, 0)

    def destroy(self) -> None:
        if self._hwnd:
            W.DestroyWindow(self._hwnd)
            self._hwnd = 0

    # ------------------------------------------------------------------ layout
    def _measure(self, text: str, dpi: int, has_progress: bool) -> tuple[int, int]:
        pad = _PADDING * dpi // 96
        max_w = cfg.OSD_MAX_WIDTH * dpi // 96
        hdc = W.GetDC(self._hwnd)
        old = W.SelectObject(hdc, self._font(dpi))
        rect = W.RECT(0, 0, max_w - 2 * pad, 0)
        W.DrawTextW(hdc, text, -1, ctypes.byref(rect),
                    W.DT_CALCRECT | W.DT_WORDBREAK | W.DT_NOPREFIX)
        W.SelectObject(hdc, old)
        W.ReleaseDC(self._hwnd, hdc)
        height = rect.bottom
        if has_progress:
            height += (_BAR_GAP + _BAR_HEIGHT) * dpi // 96
        return rect.right + 2 * pad, height + 2 * pad

    def _place(self, width: int, height: int) -> tuple[int, int]:
        point = W.POINT()
        W.GetCursorPos(ctypes.byref(point))
        info = W.MONITORINFO()
        info.cbSize = ctypes.sizeof(W.MONITORINFO)
        monitor = W.MonitorFromPoint(point, W.MONITOR_DEFAULTTONEAREST)
        if not W.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return point.x + 16, point.y + 20
        work = info.rcWork
        if cfg.OSD_POSITION == "bottom-right":
            return work.right - width - 24, work.bottom - height - 24
        x, y = point.x + 16, point.y + 20
        # Keep the whole window on the monitor the pointer is on.
        x = max(work.left, min(x, work.right - width))
        y = max(work.top, min(y, work.bottom - height))
        return x, y

    # ------------------------------------------------------------------ wndproc
    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == _WM_SHOW:
                self._on_show(hwnd)
                return 0
            if msg in (_WM_HIDE, W.WM_TIMER):
                W.KillTimer(hwnd, _TIMER_ID)
                W.ShowWindow(hwnd, W.SW_HIDE)
                return 0
            if msg == W.WM_PAINT:
                self._on_paint(hwnd)
                return 0
            if msg == W.WM_DESTROY:
                for hfont in self._fonts.values():
                    W.DeleteObject(hfont)
                self._fonts.clear()
                W.DeleteObject(self._bg_brush)
                W.DeleteObject(self._track_brush)
                W.DeleteObject(self._fill_brush)
                return 0
        except Exception:
            log.exception("OSD wndproc failed (msg=0x%04X)", msg)
        return W.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _on_show(self, hwnd: int) -> None:
        with self._lock:
            text, timeout, progress = self._text, self._timeout, self._progress
        if not text:
            W.ShowWindow(hwnd, W.SW_HIDE)
            return
        dpi = W.dpi_for_window(hwnd)
        width, height = self._measure(text, dpi, progress is not None)
        x, y = self._place(width, height)
        W.SetWindowPos(hwnd, W.HWND_TOPMOST, x, y, width, height, W.SWP_NOACTIVATE)
        W.ShowWindow(hwnd, W.SW_SHOWNOACTIVATE)
        W.InvalidateRect(hwnd, None, True)
        W.KillTimer(hwnd, _TIMER_ID)
        if timeout > 0:
            W.SetTimer(hwnd, _TIMER_ID, timeout, None)

    def _on_paint(self, hwnd: int) -> None:
        with self._lock:
            text, error, progress = self._text, self._error, self._progress
        ps = W.PAINTSTRUCT()
        hdc = W.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            rect = W.RECT()
            W.GetClientRect(hwnd, ctypes.byref(rect))
            W.FillRect(hdc, ctypes.byref(rect), self._bg_brush)
            dpi = W.dpi_for_window(hwnd)
            pad = _PADDING * dpi // 96
            bar_h = _BAR_HEIGHT * dpi // 96
            text_bottom = rect.bottom - pad
            if progress is not None:
                text_bottom -= bar_h + _BAR_GAP * dpi // 96
            old = W.SelectObject(hdc, self._font(dpi))
            W.SetBkMode(hdc, W.TRANSPARENT)
            W.SetTextColor(hdc, W.rgb(cfg.OSD_FG_ERROR if error else cfg.OSD_FG))
            inner = W.RECT(pad, pad, rect.right - pad, text_bottom)
            W.DrawTextW(hdc, text, -1, ctypes.byref(inner),
                        W.DT_LEFT | W.DT_WORDBREAK | W.DT_NOPREFIX)
            W.SelectObject(hdc, old)
            if progress is not None:
                bar = W.RECT(pad, rect.bottom - pad - bar_h, rect.right - pad, rect.bottom - pad)
                W.FillRect(hdc, ctypes.byref(bar), self._track_brush)
                fill_width = int((bar.right - bar.left) * max(0.0, min(progress, 1.0)))
                if fill_width > 0:
                    fill = W.RECT(bar.left, bar.top, bar.left + fill_width, bar.bottom)
                    W.FillRect(hdc, ctypes.byref(fill), self._fill_brush)
        finally:
            W.EndPaint(hwnd, ctypes.byref(ps))
