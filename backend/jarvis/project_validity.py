"""Bewusst gesetztes Vertrauen und lokale Datumsgrenzen für Projektbeziehungen."""

import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

CONFIDENCE = {"unverified", "plausible", "confirmed"}


def clean_metadata(confidence: str, valid_from: str | None,
                   valid_until: str | None) -> tuple[str, str | None, str | None]:
    if confidence not in CONFIDENCE:
        raise ValueError("Unbekannter Vertrauensgrad")
    for value in (valid_from, valid_until):
        if value is not None:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError("Gültigkeitsdatum muss YYYY-MM-DD sein")
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Ungültiges Gültigkeitsdatum") from exc
    if valid_from and valid_until and valid_from > valid_until:
        raise ValueError("Gültigkeitsende liegt vor dem Beginn")
    return confidence, valid_from, valid_until


def local_date(timezone: str, instant: datetime | None = None) -> date:
    now = instant or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("Stichtag benötigt eine Zeitzone")
    return now.astimezone(ZoneInfo(timezone)).date()


def validity_status(valid_from: str | None, valid_until: str | None,
                    as_of: date) -> str:
    current = as_of.isoformat()
    if valid_from is not None and current < valid_from:
        return "noch_nicht_gueltig"
    if valid_until is not None and current > valid_until:
        return "abgelaufen"
    return "aktuell"
