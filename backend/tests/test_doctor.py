"""The public setup diagnosis is local, read-only and secret-free."""

import json
import sys

from jarvis import doctor


def test_version_and_local_url_validation():
    assert doctor.version_tuple("v24.11.0") == (24, 11, 0)
    assert doctor.version_tuple("pnpm 11.25.0") == (11, 25, 0)
    assert doctor.version_tuple("unknown") is None
    assert doctor.local_ollama_url("http://127.0.0.1:11434")
    assert doctor.local_ollama_url("http://[::1]:11434/")
    for url in ("https://localhost:11434", "http://remote.example:11434",
                "http://user:pass@localhost:11434", "http://localhost:11434/private",
                "http://localhost:11434/?token=secret", "http://localhost:bad"):
        assert not doctor.local_ollama_url(url)


def test_report_checks_required_and_optional_models_without_secret(tmp_path, monkeypatch):
    (tmp_path / "backend" / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / "backend" / ".venv" / "Scripts" / "python.exe").touch()
    (tmp_path / "frontend" / "node_modules" / "vite" / "bin").mkdir(parents=True)
    (tmp_path / "frontend" / "node_modules" / "vite" / "bin" / "vite.js").touch()
    secret = "sk-private-doctor-test-value"
    (tmp_path / ".env").write_text(
        f"JARVIS_PROVIDER=ollama\nJARVIS_OPENROUTER_API_KEY={secret}\n",
        encoding="utf-8")
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor, "program_check", lambda *_args: True)
    monkeypatch.setattr(doctor, "installed_models", lambda _url: {doctor.CHAT_MODEL})
    monkeypatch.setattr(doctor, "ocr_languages", lambda: {"deu", "eng"})
    report = doctor.build_report(tmp_path)
    assert report["ok"] is True
    assert any(item["name"] == "Scan-PDF-OCR" and item["ok"] and not item["required"]
               for item in report["checks"])
    search = next(item for item in report["checks"] if item["name"] == "Suchmodell")
    assert search["required"] is False and search["ok"] is False
    monkeypatch.setattr(doctor, "ocr_languages", lambda: None)
    missing_ocr = doctor.build_report(tmp_path)
    assert missing_ocr["ok"] is True
    assert any(item["name"] == "Scan-PDF-OCR" and not item["ok"]
               for item in missing_ocr["checks"])
    assert secret not in json.dumps(report)
    (tmp_path / ".env").write_text(
        f"JARVIS_PROVIDER=ollama\nJARVIS_OLLAMA_URL=http://{secret}@remote.example:11434\n",
        encoding="utf-8")
    report = doctor.build_report(tmp_path)
    assert report["ok"] is False
    assert secret not in json.dumps(report)


def test_invalid_or_unreadable_configuration_does_not_echo_content(tmp_path, monkeypatch):
    (tmp_path / ".env").write_bytes(b"JARVIS_PROVIDER=ollama\nsecret=\xff")
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor, "program_check", lambda *_args: True)
    monkeypatch.setattr(doctor, "installed_models", lambda _url: set())
    report = doctor.build_report(tmp_path)
    assert any(item["name"] == ".env lesbar" and not item["ok"]
               for item in report["checks"])
    assert "secret" not in json.dumps(report)
    (tmp_path / ".env").write_text("JARVIS_PROVIDER=unknown-secret-provider\n", encoding="utf-8")
    report = doctor.build_report(tmp_path)
    assert any(item["name"] == "Provider" and not item["ok"]
               for item in report["checks"])
    assert "unknown-secret-provider" not in json.dumps(report)


def test_json_output_is_ascii_and_has_no_configuration_value(monkeypatch, capsys):
    monkeypatch.setattr(doctor, "build_report", lambda: {
        "ok": True, "checks": [{"name": "Prüfung", "ok": True, "required": True,
                                 "message": "bereit"}]})
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--json"])
    assert doctor.main() == 0
    output = capsys.readouterr().out
    assert output.isascii() and "\\u00fc" in output
    assert json.loads(output)["ok"] is True


def test_python_command_is_required_even_when_script_uses_an_absolute_interpreter(
        tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(doctor, "program_check",
                        lambda name, *_args: name != "python")
    monkeypatch.setattr(doctor, "installed_models", lambda _url: {doctor.CHAT_MODEL})
    report = doctor.build_report(tmp_path)
    python = next(item for item in report["checks"] if item["name"] == "Python")
    assert python["ok"] is False and python["required"] is True
    assert report["ok"] is False
