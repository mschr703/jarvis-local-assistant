"""Echte Windows-Auswahlprobe: Text wird übernommen, vorherige Zwischenablage bleibt erhalten."""

import ctypes
import threading
import time
import tkinter as tk
from ctypes import wintypes

import hotkey
from hotkey import (
    CF_HDROP,
    CF_UNICODETEXT,
    capture_selection,
    clipboard_selection,
    clipboard_snapshot,
    configure_win32,
    restore_clipboard,
)


class DROPFILES(ctypes.Structure):
    _fields_ = [("pFiles", wintypes.DWORD), ("x", wintypes.LONG), ("y", wintypes.LONG),
                ("fNC", wintypes.BOOL), ("fWide", wintypes.BOOL)]


def main() -> None:
    user32, kernel32, shell32 = configure_win32()
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    original = clipboard_snapshot(user32, kernel32)
    root = None
    try:
        root = tk.Tk()
        root.title("Jarvis-Hotkey-Test")
        root.geometry("430x150+80+80")
        root.attributes("-topmost", True)
        box = tk.Text(root, height=3)
        box.pack(fill="both", expand=True)
        box.insert("1.0", "Jarvis markierter Testtext")
        box.tag_add("sel", "1.0", "1.end")
        root.update()
        root.clipboard_clear()
        root.clipboard_append("Zwischenablage vorher")
        root.update()
        native_window = user32.GetAncestor(root.winfo_id(), 2)
        user32.keybd_event(0x12, 0, 0, 0)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        user32.SetForegroundWindow(native_window)
        box.focus_force()
        root.update()
        assert user32.GetForegroundWindow() == native_window
        result = {}

        def capture():
            try:
                result["capture"] = capture_selection(user32, kernel32, shell32, ord("J"))
            except Exception as exc:  # noqa: BLE001 - Test meldet den Originalfehler.
                result["error"] = exc

        worker = threading.Thread(target=capture)
        worker.start()
        deadline = time.monotonic() + 5
        while worker.is_alive() and time.monotonic() < deadline:
            root.update()
            time.sleep(0.01)
        worker.join(timeout=0.1)
        assert not worker.is_alive(), "Auswahlprobe hing"
        if "error" in result:
            raise result["error"]
        text, files, notice = result["capture"]
        assert text == "Jarvis markierter Testtext", (text, files, notice)
        assert files == []
        assert clipboard_selection(user32, kernel32, shell32) == ("Zwischenablage vorher", [])
        selected_file = str(__file__)
        header = DROPFILES(pFiles=ctypes.sizeof(DROPFILES), fWide=1)
        payload = ctypes.string_at(ctypes.byref(header), ctypes.sizeof(header))
        payload += (selected_file + "\0\0").encode("utf-16le")
        restore_clipboard(user32, kernel32, [(CF_HDROP, payload)])
        assert clipboard_selection(user32, kernel32, shell32) == ("", [selected_file])
        assert clipboard_snapshot(user32, kernel32)[0][0] == CF_HDROP
        user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
        user32.RegisterClipboardFormatW.restype = wintypes.UINT
        custom = user32.RegisterClipboardFormatW("JarvisHotkeySmokeData")
        formats = [(CF_UNICODETEXT, "Vorher\0".encode("utf-16le")),
                   (custom, b"Zusatzformat\0")]
        restore_clipboard(user32, kernel32, formats)
        saved = dict(clipboard_snapshot(user32, kernel32))
        assert all(saved[format_id] == data for format_id, data in formats)
        original_limit = hotkey.MAX_CLIPBOARD_BYTES
        hotkey.MAX_CLIPBOARD_BYTES = 1
        try:
            try:
                capture_selection(user32, kernel32, shell32, ord("J"))
            except RuntimeError as exc:
                assert "nicht sicher wiederherstellbares Format" in str(exc)
            else:
                raise AssertionError("Unsicheres Clipboard-Format wurde kopiert")
        finally:
            hotkey.MAX_CLIPBOARD_BYTES = original_limit
        assert dict(clipboard_snapshot(user32, kernel32)) == saved
        print({"text": text, "clipboard_restored": True, "file_paths": 1,
               "extra_format_restored": True, "unsupported_format_unchanged": True,
               "notice": notice})
    finally:
        if root is not None:
            root.destroy()
        restore_clipboard(user32, kernel32, original)


if __name__ == "__main__":
    main()
