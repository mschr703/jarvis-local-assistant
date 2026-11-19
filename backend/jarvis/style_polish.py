"""Conservative checks for an optional local style proposal."""

import re
from collections import Counter

_PROTECTED = (
    re.compile(r"https?://[^\s<>()]+", re.IGNORECASE),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"\[[1-9]\d*\]"),
    re.compile(r"\b\d+(?:[.,:/-]\d+)*\b"),
    re.compile(r"„[^“\n]+“|\"[^\"\n]+\""),
)

# Independently written German checks inspired by the MIT-licensed taxonomy in
# https://github.com/blader/humanizer. These are editorial hints, not AI detection.
_STYLE_PATTERNS = (
    ("staged_opening", "Inszenierte Einleitung",
     "Direkt mit der konkreten Aussage beginnen.",
     re.compile(r"\b(?:lass(?:t)? uns (?:eintauchen|erkunden)|"
                r"hier ist,? was (?:du|sie) wissen (?:musst|müssen)|"
                r"im folgenden (?:werden|möchte ich))\b", re.IGNORECASE)),
    ("generic_context", "Allgemeine Kontextfloskel",
     "Nur behalten, wenn der Kontext eine konkrete Information trägt.",
     re.compile(r"\b(?:in der heutigen schnelllebigen welt|"
                r"in einer sich ständig verändernden welt|"
                r"in der heutigen digitalen welt)\b", re.IGNORECASE)),
    ("filler_notice", "Hinweisfloskel",
     "Prüfen, ob der folgende Satz ohne diese Einleitung klarer ist.",
     re.compile(r"\b(?:es ist wichtig zu beachten,? dass|"
                r"an dieser stelle sei erwähnt,? dass|"
                r"man sollte nicht vergessen,? dass)\b", re.IGNORECASE)),
    ("generic_conclusion", "Formelhafter Schluss",
     "Eine konkrete Schlussfolgerung formulieren oder den Satz streichen.",
     re.compile(r"\b(?:zusammenfassend lässt sich sagen,? dass|"
                r"abschließend lässt sich festhalten,? dass|"
                r"alles in allem zeigt sich,? dass)\b", re.IGNORECASE)),
)


def writing_signals(content: str, limit: int = 20) -> list[dict[str, str | int]]:
    """Find a few explicit German writing clichés outside literal quotations."""
    quote_spans = [match.span() for match in _PROTECTED[-1].finditer(content)]
    found: list[dict[str, str | int]] = []
    for signal_id, label, suggestion, pattern in _STYLE_PATTERNS:
        for match in pattern.finditer(content):
            if any(start <= match.start() < end for start, end in quote_spans):
                continue
            found.append({"id": signal_id, "label": label,
                          "excerpt": match.group(), "start": match.start(),
                          "end": match.end(), "suggestion": suggestion})
    return sorted(found, key=lambda item: int(item["start"]))[:limit]


def protected_changes(before: str, after: str) -> list[str]:
    """Report token classes that changed, including additions as well as removals."""
    labels = ("URL", "E-Mail-Adresse", "Quellenmarke", "Zahl/Datum", "Zitat")
    return [label for label, pattern in zip(labels, _PROTECTED, strict=True)
            if Counter(pattern.findall(before)) != Counter(pattern.findall(after))]
