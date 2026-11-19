"""Bewusste Windows-Auswahlübernahme per globalem Hotkey, ohne Hintergrundüberwachung."""

import ctypes
import http.cookiejar
import json
import os
import sys
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

from dotenv import dotenv_values
from jarvis.hotkey_config import parse_hotkey

ROOT = Path(__file__).resolve().parents[1]
API = "http://127.0.0.1:8765"
UI = "http://127.0.0.1:5173"
WM_HOTKEY = 0x0312
CF_UNICODETEXT = 13
CF_HDROP = 15
VK_CONTROL = 0x11
VK_C = 0x43
KEYEVENTF_KEYUP = 0x0002
MAX_TEXT = 100_000
MAX_FILES = 20
MAX_CLIPBOARD_BYTES = 64 * 1024 * 1024
NON_MEMORY_FORMATS = {2, 3, 9, 14, 0x80}  # Bitmap, Metafile, Palette, EMF, Owner-Display.


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("padding", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("data", INPUT_UNION)]


def configure_win32() -> tuple[object, object, object]:
    if sys.platform != "win32":
        raise RuntimeError("Der globale Hotkey braucht Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                      wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                      wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
    user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    return user32, kernel32, shell32


def open_clipboard(user32: object, owner: int | None = None) -> None:
    for _ in range(10):
        if user32.OpenClipboard(owner):
            return
        time.sleep(0.05)
    raise RuntimeError("Zwischenablage ist gerade von einer anderen Anwendung belegt")


def clipboard_snapshot(user32: object, kernel32: object) -> list[tuple[int, bytes]]:
    """Kopiert alle vorhandenen HGLOBAL-Formate vor dem simulierten Strg+C."""
    open_clipboard(user32)
    try:
        result = []
        total = 0
        format_id = 0
        while True:
            ctypes.set_last_error(0)
            format_id = user32.EnumClipboardFormats(format_id)
            if not format_id:
                if ctypes.get_last_error():
                    raise RuntimeError("Zwischenablageformate konnten nicht vollständig gelesen werden")
                break
            if format_id in NON_MEMORY_FORMATS:
                raise RuntimeError("Zwischenablage enthält ein nicht sicher wiederherstellbares Bildformat")
            handle = user32.GetClipboardData(format_id)
            size = kernel32.GlobalSize(handle) if handle else 0
            if not handle or not size or total + size > MAX_CLIPBOARD_BYTES:
                raise RuntimeError("Zwischenablage enthält ein nicht sicher wiederherstellbares Format")
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise RuntimeError("Zwischenablage konnte nicht vollständig gesichert werden")
            try:
                result.append((format_id, ctypes.string_at(pointer, size)))
            finally:
                kernel32.GlobalUnlock(handle)
            total += size
        return result
    finally:
        user32.CloseClipboard()


def restore_clipboard(user32: object, kernel32: object, formats: list[tuple[int, bytes]]) -> None:
    pending = []
    try:
        for format_id, data in formats:
            handle = kernel32.GlobalAlloc(0x0002, len(data))
            if not handle:
                raise RuntimeError("Speicher für Zwischenablage fehlt")
            pending.append((format_id, handle))
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise RuntimeError("Zwischenablage konnte nicht vorbereitet werden")
            ctypes.memmove(pointer, data, len(data))
            kernel32.GlobalUnlock(handle)
    except BaseException:
        for _, handle in pending:
            kernel32.GlobalFree(handle)
        raise
    owner = user32.CreateWindowExW(0, "STATIC", "JarvisClipboard", 0, 0, 0, 0, 0,
                                  None, None, None, None)
    if not owner:
        for _, handle in pending:
            kernel32.GlobalFree(handle)
        raise RuntimeError("Zwischenablagefenster konnte nicht erstellt werden")
    try:
        open_clipboard(user32, owner)
        try:
            if not user32.EmptyClipboard():
                raise RuntimeError("Zwischenablage konnte nicht geleert werden")
            for format_id, handle in tuple(pending):
                if not user32.SetClipboardData(format_id, handle):
                    raise RuntimeError("Zwischenablageformat konnte nicht wiederhergestellt werden")
                pending.remove((format_id, handle))
        finally:
            user32.CloseClipboard()
    finally:
        user32.DestroyWindow(owner)
        for _, handle in pending:
            kernel32.GlobalFree(handle)


def send_ctrl_c(user32: object) -> None:
    keys = ((VK_CONTROL, 0), (VK_C, 0), (VK_C, KEYEVENTF_KEYUP),
            (VK_CONTROL, KEYEVENTF_KEYUP))
    events = (INPUT * len(keys))(*(INPUT(type=1, data=INPUT_UNION(ki=KEYBDINPUT(
        wVk=key, dwFlags=flags))) for key, flags in keys))
    if user32.SendInput(len(events), events, ctypes.sizeof(INPUT)) != len(events):
        raise RuntimeError("Tasteneingabe wurde von Windows oder der Quellanwendung blockiert")


def clipboard_selection(user32: object, kernel32: object, shell32: object) -> tuple[str, list[str]]:
    open_clipboard(user32)
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            handle = user32.GetClipboardData(CF_HDROP)
            if handle:
                count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
                if count > MAX_FILES:
                    raise RuntimeError("Höchstens 20 Explorer-Dateien auf einmal auswählen")
                paths = []
                for index in range(count):
                    length = shell32.DragQueryFileW(handle, index, None, 0)
                    if length > 4096:
                        raise RuntimeError("Ausgewählter Dateipfad ist zu lang")
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    shell32.DragQueryFileW(handle, index, buffer, len(buffer))
                    paths.append(buffer.value)
                return "", paths
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if handle:
                if kernel32.GlobalSize(handle) > (MAX_TEXT + 1) * 2:
                    raise RuntimeError("Markierter Text ist zu lang (maximal 100.000 Zeichen)")
                pointer = kernel32.GlobalLock(handle)
                if pointer:
                    try:
                        return ctypes.wstring_at(pointer, min(kernel32.GlobalSize(handle) // 2, MAX_TEXT)).split("\0", 1)[0], []
                    finally:
                        kernel32.GlobalUnlock(handle)
        return "", []
    finally:
        user32.CloseClipboard()


def capture_selection(user32: object, kernel32: object, shell32: object,
                      hotkey_vk: int) -> tuple[str, list[str], str]:
    original = clipboard_snapshot(user32, kernel32)
    before = user32.GetClipboardSequenceNumber()
    copied: int | None = None
    try:
        # WM_HOTKEY kommt oft schon vor dem Loslassen der Tasten an.
        deadline = time.monotonic() + 1.0
        while user32.GetAsyncKeyState(hotkey_vk) & 0x8000 and time.monotonic() < deadline:
            time.sleep(0.02)
        send_ctrl_c(user32)
        deadline = time.monotonic() + 1.5
        while user32.GetClipboardSequenceNumber() == before and time.monotonic() < deadline:
            time.sleep(0.03)
        copied = user32.GetClipboardSequenceNumber()
        if copied == before:
            return "", [], "Die Quellanwendung hat keine kopierbare Auswahl geliefert."
        text, files = clipboard_selection(user32, kernel32, shell32)
        notice = "" if text or files else "Die Auswahl enthält keinen unterstützten Text oder Explorer-Dateipfad."
        return text, files, notice
    finally:
        # Eine fremde Änderung nach unserem Kopiervorgang darf nicht überschrieben werden.
        if copied is not None and copied != before and user32.GetClipboardSequenceNumber() == copied:
            restore_clipboard(user32, kernel32, original)


def handoff(text: str, files: list[str], notice: str) -> str:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
                                         urllib.request.ProxyHandler({}))
    with opener.open(API + "/session", timeout=5) as response:
        csrf = json.load(response)["csrf"]
    body = json.dumps({"text": text, "files": files, "notice": notice}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(API + "/quick-captures", data=body, headers={
        "Content-Type": "application/json", "X-Jarvis-CSRF": csrf,
    }, method="POST")
    with opener.open(request, timeout=5) as response:
        token = json.load(response)["token"]
    return UI + "/?capture=" + token


def main() -> int:
    user32, kernel32, shell32 = configure_win32()
    env_hotkey = dotenv_values(ROOT / ".env").get("JARVIS_HOTKEY")
    hotkey = os.environ.get("JARVIS_HOTKEY") or env_hotkey or "CTRL+ALT+J"
    try:
        modifiers, vk = parse_hotkey(hotkey)
    except ValueError as exc:
        print(f"Jarvis-Hotkey ungültig: {exc}", file=sys.stderr, flush=True)
        return 2
    if not user32.RegisterHotKey(None, 1, modifiers, vk):
        print(f"Jarvis-Hotkey {hotkey} ist belegt oder nicht verfügbar. JARVIS_HOTKEY in .env ändern.",
              file=sys.stderr, flush=True)
        return 2
    print(f"Jarvis-Hotkey aktiv: {hotkey}", flush=True)
    try:
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message != WM_HOTKEY:
                continue
            try:
                text, files, notice = capture_selection(user32, kernel32, shell32, vk)
            except Exception as exc:  # noqa: BLE001 - Hotkey soll nach einem App-Fehler nutzbar bleiben.
                text, files, notice = "", [], f"Auswahl konnte nicht übernommen werden: {exc}"
            try:
                os.startfile(handoff(text, files, notice))
            except Exception as exc:  # noqa: BLE001 - Der Helfer bleibt für den nächsten Tastendruck aktiv.
                print(f"Jarvis konnte nicht geöffnet werden: {type(exc).__name__}",
                      file=sys.stderr, flush=True)
    finally:
        user32.UnregisterHotKey(None, 1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
