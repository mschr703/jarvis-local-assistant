"""Bounded, local inventory of explicitly granted code project folders."""

import os
import stat
from collections import Counter
from pathlib import Path

from jarvis.documents import DocumentStore

MAX_DIRECTORIES = 400
MAX_FILES = 3000
MAX_SHOWN_PER_ROLE = 50
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_LINES = 250

LANGUAGES = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".cs": "C#",
    ".c": "C/C++", ".cc": "C/C++", ".cpp": "C/C++",
    ".h": "C/C++", ".hpp": "C/C++",
}
TEXT_SUFFIXES = set(LANGUAGES) | {".json", ".toml", ".yaml", ".yml", ".md", ".mdx",
                                  ".txt", ".xml", ".gradle", ".css", ".html"}
TEXT_NAMES = {"Dockerfile", "Makefile", "Gemfile", "Procfile", "requirements.txt"}
IGNORED_DIRS = {"node_modules", "venv", ".venv", ".git", ".next", ".nuxt",
                "dist", "build", "target", "coverage", "__pycache__", ".pytest_cache",
                ".mypy_cache", ".ruff_cache", ".tox", ".turbo"}
ENTRY_NAMES = {"main.py", "__main__.py", "app.py", "manage.py", "main.ts", "main.tsx",
               "main.js", "main.jsx", "index.ts", "index.tsx", "index.js", "index.jsx",
               "server.ts", "server.js", "main.go", "main.rs", "lib.rs", "Program.cs"}
CONFIG_NAMES = {"package.json", "pyproject.toml", "requirements.txt", "go.mod",
                "Cargo.toml", "tsconfig.json", "pnpm-workspace.yaml", "Dockerfile",
                "Makefile", "Gemfile", "pom.xml", "build.gradle", "settings.gradle",
                "vite.config.ts", "vite.config.js", "next.config.js", "next.config.ts"}


def _hidden(path: Path) -> bool:
    """Skip dot paths and the Windows hidden file attribute."""
    attributes = getattr(path.stat(follow_symlinks=False), "st_file_attributes", 0)
    return path.name.startswith(".") or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0))


def _role(relative: Path) -> tuple[str | None, str]:
    name = relative.name
    parts = relative.parts
    if (any(part.lower() in {"test", "tests", "__tests__"} for part in parts[:-1])
            or name.startswith("test_") or name.endswith("_test.py")
            or any(name.endswith(marker) for marker in (".test.ts", ".test.tsx", ".test.js",
                                                        ".spec.ts", ".spec.tsx", ".spec.js"))):
        return "tests", "Testordner oder Testdateiname"
    if name in CONFIG_NAMES or name.startswith(("eslint.config.", "vite.config.")):
        return "config", "Bekannter Konfigurationsdateiname"
    if name in ENTRY_NAMES:
        return "entrypoints", "Üblicher Einstiegspunkt-Dateiname; Inhalt selbst prüfen"
    if name.lower().startswith(("readme", "contributing")) and relative.suffix.lower() in {
            ".md", ".mdx", ".txt"}:
        return "docs", "Projektbeschreibung-Dateiname"
    return None, ""


class CodeProjectStore:
    def __init__(self, documents: DocumentStore):
        self.documents = documents

    def _root(self, folder_id: str) -> Path:
        folder = self.documents.get_folder(folder_id)
        if folder is None:
            raise LookupError("Freigabeordner nicht gefunden")
        return self.documents.granted_root(folder)

    def overview(self, folder_id: str) -> dict:
        root = self._root(folder_id)
        categories: dict[str, list[dict]] = {kind: [] for kind in
                                            ("entrypoints", "tests", "config", "docs")}
        languages: Counter[str] = Counter()
        modules: Counter[str] = Counter()
        scanned_files = scanned_directories = skipped = 0
        limited = False

        def walk_error(_error: OSError) -> None:
            nonlocal skipped
            skipped += 1

        for base, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
            scanned_directories += 1
            if scanned_directories > MAX_DIRECTORIES:
                limited = True
                break
            current = Path(base)
            try:
                if not current.resolve(strict=True).is_relative_to(root):
                    skipped += 1
                    dirs[:] = []
                    continue
            except OSError:
                skipped += 1
                dirs[:] = []
                continue
            safe_dirs = []
            for name in sorted(dirs):
                candidate = current / name
                try:
                    if (not _hidden(candidate) and name.lower() not in IGNORED_DIRS
                            and not candidate.is_symlink() and not candidate.is_junction()
                            and candidate.resolve(strict=True).is_relative_to(root)):
                        safe_dirs.append(name)
                    else:
                        skipped += 1
                except OSError:
                    skipped += 1
            dirs[:] = safe_dirs
            for name in sorted(names):
                candidate = current / name
                try:
                    if _hidden(candidate) or candidate.is_symlink() or candidate.is_junction():
                        skipped += 1
                        continue
                    resolved = candidate.resolve(strict=True)
                    if not resolved.is_relative_to(root) or not resolved.is_file():
                        skipped += 1
                        continue
                    size = resolved.stat().st_size
                except OSError:
                    skipped += 1
                    continue
                scanned_files += 1
                if scanned_files > MAX_FILES:
                    limited = True
                    break
                relative = resolved.relative_to(root)
                language = LANGUAGES.get(relative.suffix.lower())
                if language:
                    languages[language] += 1
                    modules[relative.parts[0] if len(relative.parts) > 1 else "(Wurzel)"] += 1
                role, reason = _role(relative)
                if role:
                    categories[role].append({"path": relative.as_posix(), "size": size,
                                             "reason": reason})
            if limited:
                break

        counts = {kind: len(files) for kind, files in categories.items()}
        return {"folder_id": folder_id, "root": str(root),
                "git_checkout": ((root / ".git").exists()
                                 and not (root / ".git").is_symlink()
                                 and not (root / ".git").is_junction()),
                "scanned_files": min(scanned_files, MAX_FILES),
                "scanned_directories": min(scanned_directories, MAX_DIRECTORIES),
                "skipped": skipped, "limited": limited,
                "languages": dict(sorted(languages.items())),
                "modules": [{"path": name, "code_files": count} for name, count in
                            modules.most_common(20)], "counts": counts,
                "files": {kind: files[:MAX_SHOWN_PER_ROLE] for kind, files in categories.items()},
                "lists_limited": {kind: len(files) > MAX_SHOWN_PER_ROLE
                                  for kind, files in categories.items()}}

    def read_file(self, folder_id: str, relative_path: str) -> dict:
        root = self._root(folder_id)
        relative = Path(relative_path.replace("\\", "/"))
        if (not relative.parts or relative.is_absolute() or relative.drive
                or any(part in {"", ".", ".."} or part.startswith(".")
                       or part.lower() in IGNORED_DIRS for part in relative.parts)):
            raise ValueError("Ungültiger Projektpfad")
        if relative.suffix.lower() not in TEXT_SUFFIXES and relative.name not in TEXT_NAMES:
            raise ValueError("Nur unterstützte Textdateien können angezeigt werden")
        target = root
        for part in relative.parts:
            target = target / part
            if _hidden(target) or target.is_symlink() or target.is_junction():
                raise ValueError("Versteckte Pfade und Verknüpfungen sind nicht erlaubt")
        try:
            resolved = target.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError("Datei liegt außerhalb der Freigabe")
            before = resolved.stat()
            if before.st_size > MAX_PREVIEW_BYTES:
                raise ValueError("Datei ist für die Vorschau zu groß")
            raw = resolved.read_bytes()
            after = resolved.stat()
            if (len(raw) != before.st_size or before.st_size != after.st_size
                    or before.st_mtime_ns != after.st_mtime_ns
                    or target.resolve(strict=True) != resolved):
                raise ValueError("Datei wurde beim Lesen verändert")
        except (FileNotFoundError, PermissionError) as exc:
            raise ValueError("Projektdatei nicht lesbar") from exc
        if b"\x00" in raw:
            raise ValueError("Binärdatei kann nicht angezeigt werden")
        lines = raw.decode("utf-8", errors="replace").splitlines()
        return {"folder_id": folder_id, "path": relative.as_posix(),
                "lines": [{"number": index + 1, "text": line}
                          for index, line in enumerate(lines[:MAX_PREVIEW_LINES])],
                "line_count": len(lines), "limited": len(lines) > MAX_PREVIEW_LINES,
                "size": len(raw)}
