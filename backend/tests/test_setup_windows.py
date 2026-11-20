"""Exercise the public Windows installer without installing packages or calling a model."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _fake_command(folder: Path, name: str, version: str) -> None:
    (folder / f"{name}.cmd").write_text(
        f"@echo off\nif /I \"%~1\"==\"--version\" echo {version}\nexit /b 0\n",
        encoding="ascii",
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell installer")
@pytest.mark.parametrize(
    ("configured", "extra_args", "process_provider", "ollama_present", "expected_success",
     "expected_text", "expected_pulls"),
    [
        (None, (), None, False, True, "Einrichtung abgeschlossen", ()),
        ("JARVIS_PROVIDER=ollama\n", ("-SkipModelDownload",), None, False, False,
         "'ollama' fehlt", ()),
        ("JARVIS_PROVIDER=openrouter\n", ("-IncludeSearchModel",), None, False, False,
         "'ollama' fehlt", ()),
        ("JARVIS_PROVIDER=openrouter\n", ("-SkipModelDownload",), "ollama", False,
         False, "'ollama' fehlt", ()),
        ("JARVIS_PROVIDER=invalid-secret-value\n", (), None, False, False,
         "JARVIS_PROVIDER in .env muss ollama oder openrouter sein", ()),
        ("JARVIS_PROVIDER=ollama\n", (), None, True, True,
         "Einrichtung abgeschlossen", ("pull qwen3:4b-instruct",)),
        ("JARVIS_PROVIDER=openrouter\n", ("-IncludeSearchModel",), None, True, True,
         "Einrichtung abgeschlossen", ("pull embeddinggemma:300m",)),
    ],
)
def test_setup_chooses_required_provider_without_real_download(
        tmp_path, configured, extra_args, process_provider, ollama_present,
        expected_success, expected_text, expected_pulls):
    powershell = shutil.which("powershell.exe")
    assert powershell is not None
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(ROOT / "scripts" / "setup.ps1", scripts / "setup.ps1")
    (tmp_path / ".env.example").write_text(
        "JARVIS_PROVIDER=openrouter\nJARVIS_OPENROUTER_MODEL=openai/gpt-4o-mini\n",
        encoding="utf-8",
    )
    if configured is not None:
        (tmp_path / ".env").write_text(configured, encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, version in (("python", "Python 3.13.0"), ("node", "v24.0.0"),
                          ("pnpm", "11.0.0"), ("uv", "uv 0.13.7")):
        _fake_command(fake_bin, name, version)
    if ollama_present:
        (fake_bin / "ollama.cmd").write_text(
            '@echo off\necho %*>>"%JARVIS_TEST_CALLS%"\nexit /b 0\n', encoding="ascii")
    env = os.environ.copy()
    windows_root = Path(env.get("SystemRoot") or env.get("WINDIR") or r"C:\Windows")
    env["PATH"] = os.pathsep.join((str(fake_bin), str(windows_root / "System32")))
    calls = tmp_path / "model-calls.txt"
    env["JARVIS_TEST_CALLS"] = str(calls)
    env.pop("JARVIS_PROVIDER", None)
    if process_provider is not None:
        env["JARVIS_PROVIDER"] = process_provider
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(scripts / "setup.ps1"), *extra_args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    output = result.stdout + result.stderr
    assert (result.returncode == 0) is expected_success, output
    assert expected_text in output
    assert (tmp_path / ".env").is_file()
    if configured is None:
        assert (tmp_path / ".env").read_text(encoding="utf-8").startswith(
            "JARVIS_PROVIDER=openrouter")
    else:
        assert (tmp_path / ".env").read_text(encoding="utf-8") == configured
    assert "invalid-secret-value" not in output
    assert (tuple(calls.read_text(encoding="ascii").splitlines()) if calls.exists() else ()) == expected_pulls
