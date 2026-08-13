"""The Win32 surface this program uses, declared once with proper argtypes.

Everything here is plain ctypes -- no pywin32, no comtypes. The reason for declaring
argtypes/restype on every function rather than relying on the defaults is 64-bit
correctness: ctypes defaults an unannotated return value to `int` (32-bit), which
silently truncates handles and LRESULTs.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import POINTER, Structure, Union, WinDLL, WinError, sizeof
from ctypes import wintypes as w

user32 = WinDLL("user32", use_last_error=True)
kernel32 = WinDLL("kernel32", use_last_error=True)
gdi32 = WinDLL("gdi32", use_last_error=True)
shell32 = WinDLL("shell32", use_last_error=True)
ole32 = WinDLL("ole32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t

# Re-exported so callers need not import ctypes.wintypes themselves.
MSG = w.MSG
POINT = w.POINT
RECT = w.RECT

# ------------------------------- constants ----------------------------------
WH_KEYBOARD_LL = 13
LLKHF_INJECTED = 0x10

WM_NULL = 0x0000
WM_DESTROY = 0x0002
WM_QUIT = 0x0012
WM_PAINT = 0x000F
WM_CLOSE = 0x0010
WM_TIMER = 0x0113
WM_APP = 0x8000

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_RBUTTONUP = 0x0205
WM_LBUTTONUP = 0x0202

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C

WS_POPUP = 0x80000000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9

SWP_NOACTIVATE = 0x0010
SWP_NOZORDER = 0x0004
HWND_TOPMOST = -1

LWA_ALPHA = 0x00000002

DT_LEFT = 0x0000
DT_CALCRECT = 0x0400
DT_WORDBREAK = 0x0010
DT_NOPREFIX = 0x0800
TRANSPARENT = 1

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_EXTENDEDKEY = 0x0001

MONITOR_DEFAULTTONEAREST = 2

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
IDI_APPLICATION = 32512
IDC_ARROW = 32512

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
MF_UNCHECKED = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183

DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)


# ------------------------------- structures ---------------------------------
class KBDLLHOOKSTRUCT(Structure):
    _fields_ = [
        ("vkCode", w.DWORD),
        ("scanCode", w.DWORD),
        ("flags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class WNDCLASSEXW(Structure):
    _fields_ = [
        ("cbSize", w.UINT),
        ("style", w.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE),
        ("hIcon", w.HICON),
        ("hCursor", ctypes.c_void_p),
        ("hbrBackground", w.HBRUSH),
        ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR),
        ("hIconSm", w.HICON),
    ]


class PAINTSTRUCT(Structure):
    _fields_ = [
        ("hdc", w.HDC),
        ("fErase", w.BOOL),
        ("rcPaint", w.RECT),
        ("fRestore", w.BOOL),
        ("fIncUpdate", w.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


class MONITORINFO(Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("rcMonitor", w.RECT),
        ("rcWork", w.RECT),
        ("dwFlags", w.DWORD),
    ]


class MOUSEINPUT(Structure):
    _fields_ = [
        ("dx", w.LONG),
        ("dy", w.LONG),
        ("mouseData", w.DWORD),
        ("dwFlags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(Structure):
    _fields_ = [
        ("wVk", w.WORD),
        ("wScan", w.WORD),
        ("dwFlags", w.DWORD),
        ("time", w.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class _INPUTUNION(Union):
    # MOUSEINPUT is the largest member; it must be present or INPUT comes out too
    # small and SendInput rejects the cbSize.
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


class NOTIFYICONDATAW(Structure):
    _fields_ = [
        ("cbSize", w.DWORD),
        ("hWnd", w.HWND),
        ("uID", w.UINT),
        ("uFlags", w.UINT),
        ("uCallbackMessage", w.UINT),
        ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128),
        ("dwState", w.DWORD),
        ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256),
        ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64),
        ("dwInfoFlags", w.DWORD),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


def _decl(dll, name, restype, *argtypes):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


# ------------------------------- hooks & messages ---------------------------
SetWindowsHookExW = _decl(user32, "SetWindowsHookExW", w.HHOOK,
                          ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD)
UnhookWindowsHookEx = _decl(user32, "UnhookWindowsHookEx", w.BOOL, w.HHOOK)
CallNextHookEx = _decl(user32, "CallNextHookEx", LRESULT,
                       w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM)

GetMessageW = _decl(user32, "GetMessageW", w.BOOL,
                    POINTER(w.MSG), w.HWND, w.UINT, w.UINT)
PeekMessageW = _decl(user32, "PeekMessageW", w.BOOL,
                     POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT)
SendMessageW = _decl(user32, "SendMessageW", LRESULT,
                     w.HWND, w.UINT, w.WPARAM, w.LPARAM)
SetFocus = _decl(user32, "SetFocus", w.HWND, w.HWND)
PM_REMOVE = 0x0001
WM_GETTEXT = 0x000D
WM_SETTEXT = 0x000C
TranslateMessage = _decl(user32, "TranslateMessage", w.BOOL, POINTER(w.MSG))
DispatchMessageW = _decl(user32, "DispatchMessageW", LRESULT, POINTER(w.MSG))
PostMessageW = _decl(user32, "PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
PostQuitMessage = _decl(user32, "PostQuitMessage", None, ctypes.c_int)
# The canonical way to stop another thread's message loop -- used to quit from the
# worker thread, since the loop runs on the main one.
PostThreadMessageW = _decl(user32, "PostThreadMessageW", w.BOOL,
                           w.DWORD, w.UINT, w.WPARAM, w.LPARAM)
MessageBoxW = _decl(user32, "MessageBoxW", ctypes.c_int,
                    w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT)
MB_ICONINFORMATION = 0x00000040
MB_ICONERROR = 0x00000010
MB_TOPMOST = 0x00040000
DefWindowProcW = _decl(user32, "DefWindowProcW", LRESULT,
                       w.HWND, w.UINT, w.WPARAM, w.LPARAM)

# ------------------------------- windows ------------------------------------
RegisterClassExW = _decl(user32, "RegisterClassExW", w.ATOM, POINTER(WNDCLASSEXW))
CreateWindowExW = _decl(user32, "CreateWindowExW", w.HWND,
                        w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                        w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID)
DestroyWindow = _decl(user32, "DestroyWindow", w.BOOL, w.HWND)
ShowWindow = _decl(user32, "ShowWindow", w.BOOL, w.HWND, ctypes.c_int)
SetWindowPos = _decl(user32, "SetWindowPos", w.BOOL, w.HWND, w.HWND,
                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.UINT)
SetLayeredWindowAttributes = _decl(user32, "SetLayeredWindowAttributes", w.BOOL,
                                   w.HWND, w.COLORREF, w.BYTE, w.DWORD)
InvalidateRect = _decl(user32, "InvalidateRect", w.BOOL,
                       w.HWND, POINTER(w.RECT), w.BOOL)
GetClientRect = _decl(user32, "GetClientRect", w.BOOL, w.HWND, POINTER(w.RECT))
BeginPaint = _decl(user32, "BeginPaint", w.HDC, w.HWND, POINTER(PAINTSTRUCT))
EndPaint = _decl(user32, "EndPaint", w.BOOL, w.HWND, POINTER(PAINTSTRUCT))
SetTimer = _decl(user32, "SetTimer", ULONG_PTR, w.HWND, ULONG_PTR, w.UINT, w.LPVOID)
KillTimer = _decl(user32, "KillTimer", w.BOOL, w.HWND, ULONG_PTR)
LoadCursorW = _decl(user32, "LoadCursorW", ctypes.c_void_p, w.HINSTANCE, w.LPCWSTR)
LoadIconW = _decl(user32, "LoadIconW", w.HICON, w.HINSTANCE, w.LPCWSTR)
LoadImageW = _decl(user32, "LoadImageW", w.HANDLE, w.HINSTANCE, w.LPCWSTR,
                   w.UINT, ctypes.c_int, ctypes.c_int, w.UINT)
DestroyIcon = _decl(user32, "DestroyIcon", w.BOOL, w.HICON)
FindWindowW = _decl(user32, "FindWindowW", w.HWND, w.LPCWSTR, w.LPCWSTR)
GetSystemMetrics = _decl(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
SM_CXSMICON = 49
SM_CYSMICON = 50

# ------------------------------- foreground / focus -------------------------
GetForegroundWindow = _decl(user32, "GetForegroundWindow", w.HWND)
SetForegroundWindow = _decl(user32, "SetForegroundWindow", w.BOOL, w.HWND)
BringWindowToTop = _decl(user32, "BringWindowToTop", w.BOOL, w.HWND)
IsWindow = _decl(user32, "IsWindow", w.BOOL, w.HWND)
IsIconic = _decl(user32, "IsIconic", w.BOOL, w.HWND)
AttachThreadInput = _decl(user32, "AttachThreadInput", w.BOOL, w.DWORD, w.DWORD, w.BOOL)
GetWindowThreadProcessId = _decl(user32, "GetWindowThreadProcessId", w.DWORD,
                                 w.HWND, POINTER(w.DWORD))
GetWindowTextW = _decl(user32, "GetWindowTextW", ctypes.c_int,
                       w.HWND, w.LPWSTR, ctypes.c_int)
GetCurrentThreadId = _decl(kernel32, "GetCurrentThreadId", w.DWORD)
GetModuleHandleW = _decl(kernel32, "GetModuleHandleW", w.HMODULE, w.LPCWSTR)

# ------------------------------- input --------------------------------------
SendInput = _decl(user32, "SendInput", w.UINT, w.UINT, POINTER(INPUT), ctypes.c_int)
GetAsyncKeyState = _decl(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
MapVirtualKeyW = _decl(user32, "MapVirtualKeyW", w.UINT, w.UINT, w.UINT)

# ------------------------------- clipboard ----------------------------------
OpenClipboard = _decl(user32, "OpenClipboard", w.BOOL, w.HWND)
CloseClipboard = _decl(user32, "CloseClipboard", w.BOOL)
EmptyClipboard = _decl(user32, "EmptyClipboard", w.BOOL)
EnumClipboardFormats = _decl(user32, "EnumClipboardFormats", w.UINT, w.UINT)
GetClipboardData = _decl(user32, "GetClipboardData", w.HANDLE, w.UINT)
SetClipboardData = _decl(user32, "SetClipboardData", w.HANDLE, w.UINT, w.HANDLE)
GetClipboardFormatNameW = _decl(user32, "GetClipboardFormatNameW", ctypes.c_int,
                                w.UINT, w.LPWSTR, ctypes.c_int)
RegisterClipboardFormatW = _decl(user32, "RegisterClipboardFormatW", w.UINT, w.LPCWSTR)
GlobalAlloc = _decl(kernel32, "GlobalAlloc", w.HGLOBAL, w.UINT, ctypes.c_size_t)
GlobalFree = _decl(kernel32, "GlobalFree", w.HGLOBAL, w.HGLOBAL)
GlobalLock = _decl(kernel32, "GlobalLock", w.LPVOID, w.HGLOBAL)
GlobalUnlock = _decl(kernel32, "GlobalUnlock", w.BOOL, w.HGLOBAL)
GlobalSize = _decl(kernel32, "GlobalSize", ctypes.c_size_t, w.HGLOBAL)

# ------------------------------- gdi ----------------------------------------
CreateFontW = _decl(gdi32, "CreateFontW", w.HFONT,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_int, w.DWORD, w.DWORD, w.DWORD, w.DWORD,
                    w.DWORD, w.DWORD, w.DWORD, w.DWORD, w.LPCWSTR)
SelectObject = _decl(gdi32, "SelectObject", w.HGDIOBJ, w.HDC, w.HGDIOBJ)
DeleteObject = _decl(gdi32, "DeleteObject", w.BOOL, w.HGDIOBJ)
CreateSolidBrush = _decl(gdi32, "CreateSolidBrush", w.HBRUSH, w.COLORREF)
SetTextColor = _decl(gdi32, "SetTextColor", w.COLORREF, w.HDC, w.COLORREF)
SetBkMode = _decl(gdi32, "SetBkMode", ctypes.c_int, w.HDC, ctypes.c_int)
FillRect = _decl(user32, "FillRect", ctypes.c_int, w.HDC, POINTER(w.RECT), w.HBRUSH)
DrawTextW = _decl(user32, "DrawTextW", ctypes.c_int,
                  w.HDC, w.LPCWSTR, ctypes.c_int, POINTER(w.RECT), w.UINT)
GetDC = _decl(user32, "GetDC", w.HDC, w.HWND)
ReleaseDC = _decl(user32, "ReleaseDC", ctypes.c_int, w.HWND, w.HDC)

# ------------------------------- monitors & dpi -----------------------------
GetCursorPos = _decl(user32, "GetCursorPos", w.BOOL, POINTER(w.POINT))
MonitorFromPoint = _decl(user32, "MonitorFromPoint", w.HANDLE, w.POINT, w.DWORD)
GetMonitorInfoW = _decl(user32, "GetMonitorInfoW", w.BOOL, w.HANDLE, POINTER(MONITORINFO))

# ------------------------------- tray ---------------------------------------
Shell_NotifyIconW = _decl(shell32, "Shell_NotifyIconW", w.BOOL, w.DWORD,
                          POINTER(NOTIFYICONDATAW))
CreatePopupMenu = _decl(user32, "CreatePopupMenu", w.HMENU)
DestroyMenu = _decl(user32, "DestroyMenu", w.BOOL, w.HMENU)
AppendMenuW = _decl(user32, "AppendMenuW", w.BOOL, w.HMENU, w.UINT, ULONG_PTR, w.LPCWSTR)
TrackPopupMenu = _decl(user32, "TrackPopupMenu", w.BOOL, w.HMENU, w.UINT,
                       ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND, w.LPVOID)

# ------------------------------- process ------------------------------------
advapi32 = WinDLL("advapi32", use_last_error=True)

CreateMutexW = _decl(kernel32, "CreateMutexW", w.HANDLE, w.LPVOID, w.BOOL, w.LPCWSTR)
OpenProcess = _decl(kernel32, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
CloseHandle = _decl(kernel32, "CloseHandle", w.BOOL, w.HANDLE)
GetCurrentProcessId = _decl(kernel32, "GetCurrentProcessId", w.DWORD)
OpenProcessToken = _decl(advapi32, "OpenProcessToken", w.BOOL,
                         w.HANDLE, w.DWORD, POINTER(w.HANDLE))
GetTokenInformation = _decl(advapi32, "GetTokenInformation", w.BOOL,
                            w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, POINTER(w.DWORD))
GetSidSubAuthorityCount = _decl(advapi32, "GetSidSubAuthorityCount",
                                POINTER(ctypes.c_ubyte), w.LPVOID)
GetSidSubAuthority = _decl(advapi32, "GetSidSubAuthority", POINTER(w.DWORD),
                           w.LPVOID, w.DWORD)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
_TOKEN_INTEGRITY_LEVEL = 25
SECURITY_MANDATORY_MEDIUM_RID = 0x2000
SECURITY_MANDATORY_HIGH_RID = 0x3000


class _SID_AND_ATTRIBUTES(Structure):
    _fields_ = [("Sid", w.LPVOID), ("Attributes", w.DWORD)]


class TOKEN_MANDATORY_LABEL(Structure):
    _fields_ = [("Label", _SID_AND_ATTRIBUTES)]


def process_integrity_level(pid: int) -> int | None:
    """The process's mandatory integrity level RID, or None if it cannot be read.

    Used to predict whether messages can be posted to it at all: Windows blocks most
    window messages travelling from a lower integrity level to a higher one, so an
    instance started elevated cannot be driven from an ordinary prompt.
    """
    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        token = w.HANDLE()
        if not OpenProcessToken(handle, TOKEN_QUERY, ctypes.byref(token)):
            return None
        try:
            size = w.DWORD()
            GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, None, 0,
                                ctypes.byref(size))
            if not size.value:
                return None
            buffer = (ctypes.c_byte * size.value)()
            if not GetTokenInformation(token, _TOKEN_INTEGRITY_LEVEL, buffer,
                                       size.value, ctypes.byref(size)):
                return None
            label = ctypes.cast(buffer, POINTER(TOKEN_MANDATORY_LABEL)).contents
            count = GetSidSubAuthorityCount(label.Label.Sid)
            rid = GetSidSubAuthority(label.Label.Sid, count.contents.value - 1)
            return rid.contents.value
        finally:
            CloseHandle(token)
    finally:
        CloseHandle(handle)


def current_integrity_level() -> int | None:
    return process_integrity_level(GetCurrentProcessId())


CoInitializeEx = _decl(ole32, "CoInitializeEx", ctypes.c_long, w.LPVOID, w.DWORD)
COINIT_MULTITHREADED = 0x0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = -2147417850  # 0x80010106
_com_ready = threading.local()


def ensure_com() -> None:
    """Initialise COM on the calling thread, once per thread.

    Needed because WASAPI is COM-based and PortAudio does not initialise it for you:
    starting a capture stream from a bare worker thread fails with a misleading
    "Unanticipated host error ... WdmSyncIoctl" (measured -- the same stream opens fine
    on the main thread, which already has COM up, and fine on a worker thread once this
    has been called).
    """
    if getattr(_com_ready, "done", False):
        return
    hr = CoInitializeEx(None, COINIT_MULTITHREADED)
    # S_OK, S_FALSE (already initialised on this thread) and RPC_E_CHANGED_MODE
    # (initialised in the other apartment model) all leave COM usable here.
    if hr < 0 and hr != _RPC_E_CHANGED_MODE:
        raise WinError(hr, "CoInitializeEx failed")
    _com_ready.done = True


def int_resource(value: int):
    """MAKEINTRESOURCE: pass an integer resource id where the API wants an LPCWSTR."""
    return ctypes.cast(ctypes.c_void_p(value), ctypes.c_wchar_p)


def rgb(color: tuple[int, int, int]) -> int:
    """(R, G, B) -> COLORREF, which is 0x00BBGGRR."""
    r, g, b = color
    return r | (g << 8) | (b << 16)


def set_dpi_aware() -> None:
    """Best-effort per-monitor DPI awareness, so the OSD is crisp on any monitor."""
    try:
        fn = _decl(user32, "SetProcessDpiAwarenessContext", w.BOOL, ctypes.c_void_p)
        fn(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
    except (AttributeError, OSError):
        pass


def system_dpi() -> int:
    """DPI of the primary monitor, for windows that do not exist yet.

    `dpi_for_window` needs an hwnd; the microphone chooser has to size its viewport and
    scale its font before Dear PyGui creates one.
    """
    try:
        fn = _decl(user32, "GetDpiForSystem", w.UINT)
        return fn() or 96
    except (AttributeError, OSError):
        return 96


def dpi_for_window(hwnd: int) -> int:
    try:
        fn = _decl(user32, "GetDpiForWindow", w.UINT, w.HWND)
        return fn(hwnd) or 96
    except (AttributeError, OSError):
        return 96


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    GetWindowTextW(hwnd, buf, len(buf))
    return buf.value


def raise_last_error(what: str) -> None:
    err = ctypes.get_last_error()
    if err:
        raise WinError(err, f"{what} failed")
    raise OSError(f"{what} failed")


__all__ = [name for name in dir() if not name.startswith("_")]
