"""Windows console helpers for the TUI.

* ``get_clipboard_text()`` reads the *system* clipboard (CF_UNICODETEXT)
  via ctypes.  prompt_toolkit's ``Application.clipboard`` is an
  ``InMemoryClipboard`` that never sees the OS clipboard, so right-click /
  Ctrl+V paste needs this.
* ``disable_quick_edit()`` turns off the console's QuickEdit mode.  While a
  QuickEdit selection is active conhost blocks every console write; the
  TUI redraws from the asyncio thread, so a stray selection freezes the
  whole app (UI, key handling and downloads) until Esc/Enter is pressed.

Both are no-ops on non-Windows platforms.
"""

from __future__ import annotations

import sys
from typing import Callable

_CF_UNICODETEXT = 13
_STD_INPUT_HANDLE = -10
_ENABLE_QUICK_EDIT_MODE = 0x0040
_ENABLE_EXTENDED_FLAGS = 0x0080


def get_clipboard_text() -> str:
    """Return the Windows clipboard text, or "" if unavailable."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        import time
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
        user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        if not user32.IsClipboardFormatAvailable(_CF_UNICODETEXT):
            return ""

        # Another process may hold the clipboard briefly; retry a few times.
        for _ in range(5):
            if user32.OpenClipboard(None):
                break
            time.sleep(0.01)
        else:
            return ""

        try:
            handle = user32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return ""
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return ""
            try:
                return ctypes.wstring_at(ptr)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""


def disable_quick_edit() -> Callable[[], None]:
    """Disable QuickEdit on the console input; return a restore callback."""
    if sys.platform != "win32":
        return lambda: None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetConsoleMode.restype = wintypes.BOOL
        kernel32.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.SetConsoleMode.restype = wintypes.BOOL

        handle = kernel32.GetStdHandle(wintypes.DWORD(_STD_INPUT_HANDLE & 0xFFFFFFFF))
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return lambda: None   # not a console (redirected / pty)

        original = mode.value
        # ENABLE_EXTENDED_FLAGS must be set or the QuickEdit bit is ignored.
        new_mode = (original | _ENABLE_EXTENDED_FLAGS) & ~_ENABLE_QUICK_EDIT_MODE
        if new_mode == original or not kernel32.SetConsoleMode(handle, new_mode):
            return lambda: None

        def _restore() -> None:
            try:
                kernel32.SetConsoleMode(handle, original)
            except Exception:
                pass

        return _restore
    except Exception:
        return lambda: None
