"""Konfigurierbarer Windows-Hotkey ohne reservierte Windows-Taste."""

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


def parse_hotkey(value: str) -> tuple[int, int]:
    pieces = [part.strip().upper() for part in value.split("+")]
    if len(pieces) < 2 or len(set(pieces)) != len(pieces):
        raise ValueError("Hotkey muss z. B. CTRL+ALT+J sein")
    modifiers = {"CTRL": MOD_CONTROL, "ALT": MOD_ALT, "SHIFT": MOD_SHIFT}
    if not all(part in modifiers for part in pieces[:-1]):
        raise ValueError("Hotkey unterstützt CTRL, ALT und SHIFT als Modifier")
    key = pieces[-1]
    if len(key) != 1 or not ("A" <= key <= "Z" or "0" <= key <= "9"):
        raise ValueError("Hotkey-Taste muss A–Z oder 0–9 sein")
    if not ("CTRL" in pieces[:-1] or "ALT" in pieces[:-1]):
        raise ValueError("Hotkey braucht CTRL oder ALT")
    return MOD_NOREPEAT | sum(modifiers[part] for part in pieces[:-1]), ord(key)
