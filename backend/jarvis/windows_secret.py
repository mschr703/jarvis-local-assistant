"""Kleine Windows-DPAPI-Anbindung für einen benutzergebundenen Backup-Schlüssel."""

import ctypes
import sys
from ctypes import wintypes


class DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("Geplante Backups benötigen Windows-DPAPI")
    if not data or len(data) > 16 * 1024:
        raise ValueError("Backup-Geheimnis hat eine ungültige Länge")
    crypt32 = ctypes.WinDLL("Crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
    if protect:
        function = crypt32.CryptProtectData
        function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_wchar_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(DataBlob)]
    else:
        function = crypt32.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(DataBlob), ctypes.c_void_p, ctypes.c_void_p,
                             ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(DataBlob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    raw = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    original = DataBlob(len(data), raw)
    output = DataBlob()
    try:
        ok = (function(ctypes.byref(original), "Jarvis-Backup", None, None, None, 0x1,
                       ctypes.byref(output)) if protect else
              function(ctypes.byref(original), None, None, None, None, 0x1,
                       ctypes.byref(output)))
        if not ok:
            raise RuntimeError("Windows-DPAPI konnte das Backup-Geheimnis nicht verarbeiten")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
    finally:
        ctypes.memset(raw, 0, len(data))


def protect_secret(secret: str) -> bytes:
    if not isinstance(secret, str) or not secret:
        raise ValueError("Backup-Passwort darf nicht leer sein")
    return _dpapi(secret.encode("utf-8"), True)


def unprotect_secret(blob: bytes) -> str:
    try:
        return _dpapi(blob, False).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("Geschütztes Backup-Geheimnis ist ungültig") from exc
