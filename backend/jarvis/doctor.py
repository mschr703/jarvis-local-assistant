"""Read-only Windows setup diagnosis without printing private configuration values."""

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[2]
CHAT_MODEL = "qwen3:4b-instruct"
SEARCH_MODEL = "embeddinggemma:300m"


def version_tuple(value: str) -> tuple[int, ...] | None:
    match = re.search(r"(?:^|\s|v)(\d+)\.(\d+)(?:\.(\d+))?", value)
    return tuple(int(part) for part in match.groups() if part is not None) if match else None


def local_ollama_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return (parsed.scheme == "http" and parsed.hostname in
                {"localhost", "127.0.0.1", "::1"} and parsed.port is not None
                and not parsed.username and not parsed.password
                and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def settings_from_env(root: Path) -> dict[str, str]:
    """Read only known, non-secret settings; never echo raw lines or other keys."""
    settings = {"JARVIS_PROVIDER": "ollama", "JARVIS_OLLAMA_URL": "http://127.0.0.1:11434",
                "JARVIS_OLLAMA_MODEL": CHAT_MODEL}
    path = root / ".env"
    if not path.is_file():
        return settings
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ValueError("Lokale Konfiguration ist nicht lesbar") from exc
    for line in lines:
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key in settings and not line.lstrip().startswith("#"):
            settings[key] = value.strip().strip('"\'')
    return settings


def program_check(name: str, minimum: tuple[int, ...] | None = None) -> bool:
    command = shutil.which(name)
    if command is None:
        return False
    try:
        result = subprocess.run([command, "--version"], capture_output=True, text=True,
                                timeout=5, check=True)
        output = result.stdout + " " + result.stderr
    except (OSError, subprocess.SubprocessError):
        return False
    version = version_tuple(output)
    return bool(version and (minimum is None or version >= minimum))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        raise HTTPError(request.full_url, code, "Weiterleitung gesperrt", headers, response)


def installed_models(url: str) -> set[str] | None:
    if not local_ollama_url(url):
        return None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(f"{url.rstrip('/')}/api/tags"), timeout=3) as response:
            raw = response.read(1_000_001)
        if len(raw) > 1_000_000:
            return None
        models = json.loads(raw)["models"]
        if not isinstance(models, list) or any(not isinstance(item, dict) for item in models):
            return None
        return {item["name"] for item in models if isinstance(item.get("name"), str)}
    except (OSError, URLError, ValueError, KeyError, TypeError):
        return None


def build_report(root: Path = ROOT) -> dict:
    checks = []

    def add(name: str, ok: bool, required: bool, message: str,
            good: str | None = None) -> None:
        checks.append({"name": name, "ok": ok, "required": required,
                       "message": (good or f"{name} bereit.") if ok else message})

    add("Windows", platform.system() == "Windows", True,
        "Windows 11 verwenden." if platform.system() != "Windows" else "Windows erkannt.")
    python_ready = sys.version_info >= (3, 13) and program_check("python", (3, 13))
    add("Python", python_ready, True,
        "Python 3.13 oder neuer installieren und den Befehl python zum PATH hinzufügen.")
    add("Node.js", program_check("node", (24,)), True,
        "Node.js 24 oder neuer installieren, falls dieser Punkt fehlt.")
    add("pnpm", program_check("pnpm", (11,)), True,
        "pnpm 11 oder neuer installieren, falls dieser Punkt fehlt.")
    add("uv", program_check("uv") or program_check("python", (3, 13))
        and _uv_module_available(), True,
        "uv installieren, falls dieser Punkt fehlt.")
    add("Backend", (root / "backend" / ".venv" / "Scripts" / "python.exe").is_file(), True,
        "Fehlt: .\\scripts\\setup.ps1 ausführen.")
    add("Frontend", (root / "frontend" / "node_modules" / "vite" / "bin" / "vite.js").is_file(),
        True, "Fehlt: .\\scripts\\setup.ps1 ausführen.")
    add(".env", (root / ".env").is_file(), True,
        "Fehlt: .\\scripts\\setup.ps1 ausführen.")

    try:
        settings = settings_from_env(root)
    except ValueError:
        add(".env lesbar", False, True,
            "Lokale .env ist nicht lesbar oder keine UTF-8-Datei.")
        settings = {"JARVIS_PROVIDER": "ollama",
                    "JARVIS_OLLAMA_URL": "http://127.0.0.1:11434",
                    "JARVIS_OLLAMA_MODEL": CHAT_MODEL}
    if settings["JARVIS_PROVIDER"] == "ollama":
        add("Ollama-Programm", program_check("ollama"), True,
            "Ollama für Windows installieren, falls dieser Punkt fehlt.")
        url = settings["JARVIS_OLLAMA_URL"]
        valid = local_ollama_url(url)
        add("Lokale Modelladresse", valid, True,
            "JARVIS_OLLAMA_URL muss auf einen lokalen HTTP-Dienst zeigen.")
        models = installed_models(url) if valid else None
        add("Ollama-Dienst", models is not None, True,
            "Ollama starten, falls dieser Punkt fehlt.")
        add("Chatmodell", models is not None and settings["JARVIS_OLLAMA_MODEL"] in models,
            True, f"Fehlt: ollama pull {CHAT_MODEL} oder konfiguriertes Modell installieren.")
        add("Suchmodell", models is not None and SEARCH_MODEL in models, False,
            f"Optional: ollama pull {SEARCH_MODEL} für sinngemäße Suche.")
    elif settings["JARVIS_PROVIDER"] == "openrouter":
        add("OpenRouter", True, False,
            "Cloud-Zugang und Budget werden von diesem lokalen Check nicht geprüft.",
            good="Cloud-Zugang und Budget werden von diesem lokalen Check nicht geprüft.")
    else:
        add("Provider", False, True,
            "JARVIS_PROVIDER muss ollama oder openrouter sein.")
    return {"ok": all(item["ok"] for item in checks if item["required"]), "checks": checks}


def _uv_module_available() -> bool:
    try:
        return subprocess.run([sys.executable, "-m", "uv", "--version"],
                              capture_output=True, timeout=5, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Lokalen Jarvis-Setup-Check ausführen")
    parser.add_argument("--json", action="store_true", help="Ergebnis ohne geheime Werte als JSON ausgeben")
    args = parser.parse_args()
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=True))
    else:
        for check in report["checks"]:
            symbol = "OK" if check["ok"] else "FEHLT" if check["required"] else "OPTIONAL"
            print(f"[{symbol}] {check['name']}: {check['message']}")
        print("Jarvis ist startbereit." if report["ok"] else
              "Pflichtvoraussetzungen fehlen. Die Hinweise oben abarbeiten.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
