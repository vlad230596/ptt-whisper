"""Notification-area icon: the only always-visible sign that the app is alive.

Worth having because the failure mode of a background hotkey app is silence -- if the
hook dies you want something to look at. The tooltip carries the current state and the
right-click menu duplicates every hotkey command.

Lives on the same thread and message loop as the hook and the OSD.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import winapi as W

log = logging.getLogger(__name__)

_WM_TRAY = W.WM_APP + 3
_ICON_ID = 1
# Public: `service.py` finds the running instance by looking for this window class, and
# a window of this class exists exactly as long as the app is up.
CLASS_NAME = "PushToTalkTray"
_FIRST_CMD = 0x1000


@dataclass(slots=True)
class MenuItem:
    label: str
    action: Callable[[], None]
    checked: Callable[[], bool] | None = None


class Tray:
    def __init__(
        self,
        tooltip: str,
        items: list[MenuItem],
        idle_icon: str | None = None,
        active_icon: str | None = None,
    ) -> None:
        self._items = items
        self._proc = W.WNDPROC(self._wndproc)
        self._hwnd = self._create_window()
        # Icons we loaded ourselves have to be destroyed; the stock fallback is shared
        # and must not be.
        self._owned: set[int] = set()
        self._icons = {
            "idle": self._load_icon(idle_icon),
            "active": self._load_icon(active_icon),
        }
        self._data = W.NOTIFYICONDATAW()
        self._data.cbSize = ctypes.sizeof(W.NOTIFYICONDATAW)
        self._data.hWnd = self._hwnd
        self._data.uID = _ICON_ID
        self._data.uFlags = W.NIF_MESSAGE | W.NIF_ICON | W.NIF_TIP
        self._data.uCallbackMessage = _WM_TRAY
        self._data.hIcon = self._icons["idle"]
        self._data.szTip = tooltip[:127]
        if not W.Shell_NotifyIconW(W.NIM_ADD, ctypes.byref(self._data)):
            W.raise_last_error("Shell_NotifyIcon(NIM_ADD)")

    def _load_icon(self, path: str | None) -> int:
        """The .ico if it is there, the stock application icon if it is not.

        A missing icon file must not stop the program from running -- it is cosmetic,
        and `tools/make_icon.py` regenerates it.
        """
        if path and Path(path).is_file():
            handle = W.LoadImageW(
                None, str(path), W.IMAGE_ICON,
                W.GetSystemMetrics(W.SM_CXSMICON), W.GetSystemMetrics(W.SM_CYSMICON),
                W.LR_LOADFROMFILE,
            )
            if handle:
                self._owned.add(handle)
                return handle
            log.warning("could not load the icon %s", path)
        else:
            log.info("icon %s not found; run tools/make_icon.py", path)
        return W.LoadIconW(None, W.int_resource(W.IDI_APPLICATION))

    def _create_window(self) -> int:
        wc = W.WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(W.WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        wc.hInstance = W.GetModuleHandleW(None)
        wc.lpszClassName = CLASS_NAME
        if not W.RegisterClassExW(ctypes.byref(wc)):
            W.raise_last_error("RegisterClassEx(tray)")
        hwnd = W.CreateWindowExW(0, CLASS_NAME, CLASS_NAME, 0, 0, 0, 0, 0,
                                 None, None, wc.hInstance, None)
        if not hwnd:
            W.raise_last_error("CreateWindowEx(tray)")
        return hwnd

    def set_status(self, text: str, *, active: bool = False) -> None:
        """Update the tooltip and the icon. Safe from any thread."""
        self._data.szTip = text[:127]
        self._data.hIcon = self._icons["active" if active else "idle"]
        self._data.uFlags = W.NIF_TIP | W.NIF_ICON
        W.Shell_NotifyIconW(W.NIM_MODIFY, ctypes.byref(self._data))

    def destroy(self) -> None:
        if self._hwnd:
            W.Shell_NotifyIconW(W.NIM_DELETE, ctypes.byref(self._data))
            W.DestroyWindow(self._hwnd)
            self._hwnd = 0
        for handle in self._owned:
            W.DestroyIcon(handle)
        self._owned.clear()
        self._icons.clear()

    def _show_menu(self, hwnd: int) -> None:
        menu = W.CreatePopupMenu()
        try:
            for index, item in enumerate(self._items):
                if item.label == "-":
                    W.AppendMenuW(menu, W.MF_SEPARATOR, 0, None)
                    continue
                flags = W.MF_STRING
                if item.checked is not None and item.checked():
                    flags |= W.MF_CHECKED
                W.AppendMenuW(menu, flags, _FIRST_CMD + index, item.label)
            point = W.POINT()
            W.GetCursorPos(ctypes.byref(point))
            # Required by TrackPopupMenu, or the menu never gets a chance to close.
            W.SetForegroundWindow(hwnd)
            chosen = W.TrackPopupMenu(menu, W.TPM_RIGHTBUTTON | W.TPM_RETURNCMD,
                                      point.x, point.y, 0, hwnd, None)
        finally:
            W.DestroyMenu(menu)
        if chosen >= _FIRST_CMD:
            item = self._items[chosen - _FIRST_CMD]
            item.action()

    def _wndproc(self, hwnd: int, msg: int, wparam: int, lparam: int) -> int:
        try:
            if msg == _WM_TRAY and lparam in (W.WM_RBUTTONUP, W.WM_LBUTTONUP):
                self._show_menu(hwnd)
                return 0
            if msg == W.WM_CLOSE:
                # How `pushtotalk stop` asks the app to quit: this goes through the
                # normal shutdown path rather than terminating the process.
                W.PostQuitMessage(0)
                return 0
        except Exception:
            log.exception("tray wndproc failed")
        return W.DefWindowProcW(hwnd, msg, wparam, lparam)
