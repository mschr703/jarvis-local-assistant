"""Kurzlebige, einmalig abrufbare Übergaben vom bewusst ausgelösten Windows-Hotkey."""

import secrets
import threading
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QuickCaptureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(default="", max_length=100_000)
    files: list[str] = Field(default_factory=list, max_length=20)
    notice: str = Field(default="", max_length=240)

    @field_validator("files")
    @classmethod
    def absolute_files(cls, files: list[str]) -> list[str]:
        if any(not Path(path).is_absolute() or len(path) > 4096 for path in files):
            raise ValueError("Dateiauswahl braucht absolute Pfade")
        return files


class QuickCaptureStore:
    def __init__(self, ttl_seconds: float = 120):
        self.ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._items: dict[str, tuple[float, QuickCaptureInput]] = {}

    def add(self, capture: QuickCaptureInput) -> str:
        with self._lock:
            self._expire()
            while len(self._items) >= 10:
                self._items.pop(next(iter(self._items)))
            token = secrets.token_urlsafe(24)
            self._items[token] = (time.monotonic() + self.ttl_seconds, capture)
            return token

    def take(self, token: str) -> QuickCaptureInput | None:
        with self._lock:
            self._expire()
            item = self._items.pop(token, None)
            return item[1] if item else None

    def _expire(self) -> None:
        now = time.monotonic()
        for token, (deadline, _) in tuple(self._items.items()):
            if deadline <= now:
                self._items.pop(token, None)
