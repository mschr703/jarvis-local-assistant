"""Local, source-backed file graph and reversible Obsidian export."""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

from jarvis.documents import DocumentStore

GRAPHIFY_VERSION = "0.9.101"
CODE_SUFFIXES = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs"}
NOTE_SUFFIXES = {".md", ".markdown"}
MAX_FILES = 100
MAX_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 250 * 1024
MAX_NODES = 500
MAX_EDGES = 1000
LINK = re.compile(r"\[\[([^\]|#]+)(?:\|[^\]]+)?\]\]|\[[^\]]+\]\(([^)#]+)(?:#[^)]*)?\)")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value: str) -> Path:
    candidate = Path(value.replace("\\", "/"))
    if candidate.is_absolute() or not candidate.parts or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise ValueError("Ungültiger relativer Graphpfad")
    return candidate


def _plain(value: object, limit: int = 160) -> str:
    return re.sub(r"[\r\n\x00-\x1f]+", " ", str(value))[:limit]


class FileGraph:
    def __init__(self, documents: DocumentStore, data_dir: Path):
        self.documents = documents
        self.records = Path(data_dir) / "file_graph_exports"
        self.lock = threading.RLock()

    def _source(self, folder_id: str) -> Path:
        folder = self.documents.get_folder(folder_id)
        if folder is None:
            raise ValueError("Freigabeordner nicht gefunden")
        return self.documents.granted_root(folder)

    @staticmethod
    def _collect(root: Path, stage: Path) -> dict[str, str]:
        files: dict[str, str] = {}
        total = 0
        directories = 0
        for base, dirs, names in os.walk(root, followlinks=False):
            directories += 1
            if directories > 200:
                raise ValueError("Zu viele Unterordner für die Graphvorschau")
            dirs[:] = [name for name in dirs if not (
                (Path(base) / name).is_symlink() or (Path(base) / name).is_junction()
            )]
            for name in names:
                source = Path(base) / name
                if source.suffix.lower() not in CODE_SUFFIXES | NOTE_SUFFIXES:
                    continue
                if source.is_symlink() or source.is_junction():
                    continue
                resolved = source.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    continue
                relative = resolved.relative_to(root)
                if any(part.startswith(".") for part in relative.parts):
                    continue
                if len(files) >= MAX_FILES:
                    raise ValueError("Mehr als 100 unterstützte Dateien im Freigabeordner")
                size = resolved.stat().st_size
                total += size
                if size > MAX_FILE_BYTES or total > MAX_BYTES:
                    raise ValueError("Graphdateien überschreiten das Größenlimit")
                data = resolved.read_bytes()
                if len(data) != size:
                    raise ValueError("Datei wurde beim Lesen verändert")
                key = relative.as_posix()
                files[key] = _sha(data)
                target = stage / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        return files

    @staticmethod
    def _graphify(stage: Path, code_files: list[str]) -> tuple[list[dict], list[dict]]:
        if not code_files:
            return [], []
        binary = Path(sys.executable).with_name(
            "graphify.exe" if sys.platform == "win32" else "graphify"
        )
        if not binary.is_file():
            raise ValueError("Graphify fehlt: uv sync --project backend ausführen")
        env = os.environ.copy()
        for name in tuple(env):
            if name.endswith("_API_KEY") or name in {
                "ANTHROPIC_BASE_URL", "OPENAI_BASE_URL", "OLLAMA_BASE_URL",
            }:
                env.pop(name, None)
        env["GRAPHIFY_BACKEND"] = "none"
        result = subprocess.run(
            [str(binary), "extract", str(stage), "--code-only", "--no-cluster",
             "--max-workers", "2", "--out", str(stage)],
            cwd=stage, env=env, capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode:
            raise ValueError("Graphify-Analyse fehlgeschlagen: " + _plain(result.stderr, 300))
        graph_file = stage / "graphify-out" / "graph.json"
        graph = json.loads(graph_file.read_text(encoding="utf-8"))
        return graph.get("nodes", []), graph.get("edges", [])

    def preview(self, folder_id: str) -> dict:
        root = self._source(folder_id)
        with tempfile.TemporaryDirectory(prefix="jarvis-file-graph-") as temporary:
            stage = Path(temporary) / "input"
            stage.mkdir()
            files = self._collect(root, stage)
            code_files = [name for name in files if Path(name).suffix.lower() in CODE_SUFFIXES]
            raw_nodes, raw_edges = self._graphify(stage, code_files)
            nodes: list[dict] = []
            edges: list[dict] = []
            known: set[str] = set()
            for item in raw_nodes[:MAX_NODES]:
                source = str(item.get("source_file", ""))
                try:
                    relative = _safe_relative(source).as_posix()
                except ValueError:
                    continue
                if relative not in files:
                    continue
                node_id = _plain(item.get("id", ""), 120)
                if not node_id or node_id in known:
                    continue
                known.add(node_id)
                nodes.append({
                    "id": node_id, "label": _plain(item.get("label", node_id)),
                    "kind": "code", "source_file": relative,
                    "source_location": _plain(item.get("source_location", ""), 30),
                })
            for item in raw_edges[:MAX_EDGES]:
                source = str(item.get("source_file", ""))
                try:
                    relative = _safe_relative(source).as_posix()
                except ValueError:
                    continue
                if relative not in files:
                    continue
                left = _plain(item.get("source", ""), 120)
                right = _plain(item.get("target", ""), 120)
                if left in known and right in known:
                    edges.append({
                        "source": left, "target": right,
                        "relation": _plain(item.get("relation", "")),
                        "confidence": item.get("confidence") if item.get("confidence") in
                        {"EXTRACTED", "INFERRED"} else "INFERRED",
                        "source_file": relative,
                        "source_location": _plain(item.get("source_location", ""), 30),
                    })
            for name in files:
                if Path(name).suffix.lower() not in NOTE_SUFFIXES:
                    continue
                node_id = "note:" + name
                if len(nodes) >= MAX_NODES:
                    break
                nodes.append({"id": node_id, "label": Path(name).stem,
                              "kind": "note", "source_file": name, "source_location": "L1"})
                known.add(node_id)
            for name in files:
                if Path(name).suffix.lower() not in NOTE_SUFFIXES:
                    continue
                text = (stage / name).read_text(encoding="utf-8", errors="replace")
                for match in LINK.finditer(text):
                    destination = match.group(1) or match.group(2)
                    target = (Path(name).parent / destination).as_posix()
                    if Path(target).suffix.lower() not in NOTE_SUFFIXES:
                        target += ".md"
                    try:
                        target = _safe_relative(target).as_posix()
                    except ValueError:
                        continue
                    if target not in files or "note:" + target not in known:
                        continue
                    if len(edges) >= MAX_EDGES:
                        break
                    line = text.count("\n", 0, match.start()) + 1
                    edges.append({
                        "source": "note:" + name, "target": "note:" + target,
                        "relation": "links_to", "confidence": "EXTRACTED",
                        "source_file": name, "source_location": f"L{line}",
                    })
        fingerprint = _sha(json.dumps(
            {"files": files, "nodes": nodes, "edges": edges}, sort_keys=True
        ).encode("utf-8"))
        return {"folder_id": folder_id, "source_root": str(root), "files": files,
                "nodes": nodes, "edges": edges, "fingerprint": fingerprint,
                "graphify_version": GRAPHIFY_VERSION}

    @staticmethod
    def _vault(path: str) -> Path:
        supplied = Path(path).expanduser()
        if not supplied.is_absolute() or not supplied.is_dir():
            raise ValueError("Vorhandenen absoluten Obsidian-Vault-Ordner angeben")
        if supplied.is_symlink() or supplied.is_junction():
            raise ValueError("Vault-Verknüpfungen sind nicht erlaubt")
        root = supplied.resolve(strict=True)
        settings = root / ".obsidian"
        if not settings.is_dir() or settings.is_symlink() or settings.is_junction():
            raise ValueError("Vault benötigt einen echten .obsidian-Ordner")
        return root

    @staticmethod
    def _target(vault: Path, folder_id: str) -> Path:
        parent = vault / "Jarvis Graphify"
        if parent.exists() and (parent.is_symlink() or parent.is_junction()):
            raise ValueError("Graphify-Unterordner ist umgeleitet")
        return parent / ("Graph-" + folder_id[:12])

    @staticmethod
    def _render(graph: dict) -> dict[str, bytes]:
        def note_path(node_id: str) -> str:
            return "nodes/" + _sha(node_id.encode("utf-8")) + ".md"

        def label(value: str) -> str:
            return _plain(value).replace("[", "\\[").replace("]", "\\]")

        lines = ["# Jarvis Datei-Graph", "",
                 f"Quelle: `{_plain(graph['source_root'], 500)}`", "",
                 f"Graphify: {GRAPHIFY_VERSION} · Code lokal per AST; Notizlinks lokal gelesen.",
                 "", "## Kanten"]
        by_id = {node["id"]: node for node in graph["nodes"]}
        outgoing: dict[str, list[dict]] = {node_id: [] for node_id in by_id}
        for edge in graph["edges"]:
            left = label(by_id[edge["source"]]["label"])
            right = label(by_id[edge["target"]]["label"])
            evidence = f"{edge['source_file']}:{edge['source_location']}"
            lines.append(f"- [{left}]({note_path(edge['source'])}) → "
                         f"[{right}]({note_path(edge['target'])}) · {edge['relation']} · "
                         f"{edge['confidence']} · `{evidence}`")
            outgoing[edge["source"]].append(edge)
        lines.extend(["", "## Knoten"])
        rendered: dict[str, bytes] = {}
        for node in graph["nodes"]:
            path = note_path(node["id"])
            lines.append(f"- [{label(node['label'])}]({path}) · {node['kind']} · "
                         f"`{node['source_file']}:{node['source_location']}`")
            details = [f"# {_plain(node['label'])}", "",
                       f"Typ: {_plain(node['kind'])}",
                       f"Quelle: `{node['source_file']}:{node['source_location']}`",
                       "", "## Verbindungen"]
            for edge in outgoing[node["id"]]:
                target = by_id[edge["target"]]
                details.append(
                    f"- [{label(target['label'])}]({Path(note_path(target['id'])).name}) · "
                    f"{edge['relation']} · {edge['confidence']} · "
                    f"`{edge['source_file']}:{edge['source_location']}`"
                )
            rendered[path] = ("\n".join(details) + "\n").encode("utf-8")
        rendered["Graph.md"] = ("\n".join(lines) + "\n").encode("utf-8")
        return rendered

    def export_preview(self, folder_id: str, vault_path: str) -> dict:
        graph = self.preview(folder_id)
        vault = self._vault(vault_path)
        target = self._target(vault, folder_id)
        if target.exists():
            raise ValueError("Zielunterordner existiert bereits; nichts wird überschrieben")
        return {"folder_id": folder_id, "vault": str(vault), "target": str(target),
                "files": list(self._render(graph)), "fingerprint": graph["fingerprint"],
                "nodes": len(graph["nodes"]), "edges": len(graph["edges"])}

    def export(self, folder_id: str, vault_path: str, fingerprint: str) -> dict:
        with self.lock:
            preview = self.export_preview(folder_id, vault_path)
            if preview["fingerprint"] != fingerprint:
                raise ValueError("Quelldateien haben sich seit der Vorschau geändert")
            graph = self.preview(folder_id)
            if graph["fingerprint"] != fingerprint:
                raise ValueError("Quelldateien haben sich seit der Vorschau geändert")
            vault = self._vault(vault_path)
            target = self._target(vault, folder_id)
            target.parent.mkdir(exist_ok=True)
            target.mkdir(exist_ok=False)
            export_id = str(uuid4())
            contents = self._render(graph)
            files = [{"path": name, "sha256": _sha(data)} for name, data in contents.items()]
            manifest = {"id": export_id, "target": str(target), "vault": str(vault),
                        "folder_id": folder_id, "fingerprint": fingerprint, "files": files}
            manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
            try:
                for name, data in contents.items():
                    destination = target / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                (target / "manifest.json").write_bytes(manifest_data)
                self.records.mkdir(parents=True, exist_ok=True)
                (self.records / f"{export_id}.json").write_bytes(manifest_data)
            except Exception:
                for name in [*contents, "manifest.json"]:
                    (target / name).unlink(missing_ok=True)
                for directory in (target / "nodes", target):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                raise
            return manifest

    def rollback(self, export_id: str) -> dict:
        with self.lock:
            if not re.fullmatch(r"[0-9a-f-]{36}", export_id):
                raise ValueError("Ungültige Export-ID")
            record = self.records / f"{export_id}.json"
            if not record.is_file():
                raise ValueError("Exportmanifest nicht gefunden")
            data = record.read_bytes()
            manifest = json.loads(data)
            vault = self._vault(manifest["vault"])
            target = self._target(vault, manifest["folder_id"])
            if str(target) != manifest["target"] or not target.is_dir() or (
                target.is_symlink() or target.is_junction()
            ):
                raise ValueError("Exportziel wurde verändert")
            expected = {item["path"]: item["sha256"] for item in manifest["files"]}
            expected["manifest.json"] = _sha(data)
            for name, digest in expected.items():
                candidate = target / _safe_relative(name)
                if candidate.is_symlink() or not candidate.is_file() or _sha(
                    candidate.read_bytes()
                ) != digest:
                    raise ValueError("Exportdatei wurde verändert; Rücknahme abgebrochen")
            for name in expected:
                (target / name).unlink()
            if (target / "nodes").is_dir():
                try:
                    (target / "nodes").rmdir()
                except OSError:
                    pass
            try:
                target.rmdir()
            except OSError:
                pass  # Eigene zusätzliche Dateien bleiben unberührt.
            record.unlink()
            return {"removed": True, "files": len(expected), "target": str(target)}

    def list_exports(self) -> list[dict]:
        if not self.records.is_dir():
            return []
        result = []
        for path in sorted(self.records.glob("*.json")):
            try:
                item = json.loads(path.read_text(encoding="utf-8"))
                result.append({"id": item["id"], "target": item["target"],
                               "folder_id": item["folder_id"], "files": len(item["files"])})
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return result
