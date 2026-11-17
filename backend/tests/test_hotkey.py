import pytest

from jarvis.hotkey_config import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, parse_hotkey


def test_configurable_hotkey_parsing():
    assert parse_hotkey("ctrl+alt+j") == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("J"))
    assert parse_hotkey(" CTRL + SHIFT + 9 ") == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("9"))


@pytest.mark.parametrize("hotkey", ["J", "SHIFT+J", "WIN+J", "CTRL+F12",
                                           "CTRL+ALT+J+K", "CTRL+CTRL+J", "CTRL+ALT+"])
def test_invalid_or_reserved_hotkey_rejected(hotkey):
    with pytest.raises(ValueError):
        parse_hotkey(hotkey)
