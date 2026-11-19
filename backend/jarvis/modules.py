"""Explicitly installed local feature modules, isolated from the public core source tree.

The installed directory is trusted code: installation is a deliberate local action, never a
network download. A module is imported only when enabled. Disabling blocks new requests and
keeps its tables in jarvis.db so the normal database backup includes its data.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from jarvis.db import Database
from jarvis.engine import RunEngine, ToolDefinition, ToolUserError

MODULE_API = 1
MODULE_ID = re.compile(r"^[a-z][a-z0-9_]{2,39}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
MISSING_PACKAGE_ERROR = "Modulpaket fehlt; aus vertrauter Quelle neu installieren"


class ModuleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=MODULE_ID.pattern)
    name: str = Field(min_length=1, max_length=60)
    version: str = Field(pattern=VERSION.pattern)
    api: int = Field(ge=1)
    data_version: int = Field(ge=1)
    navigation: str = Field(min_length=1, max_length=40)
    view: str = Field(pattern=r"^web_component_v1$")
    element: str = Field(pattern=r"^jarvis-[a-z0-9]+(?:-[a-z0-9]+)*$")
    permissions: list[Literal["local_data"]] = Field(default_factory=list, max_length=12)
    dependencies: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("name", "navigation")
    @classmethod
    def visible_label(cls, value: str) -> str:
        if not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("Modulname und Navigation brauchen sichtbaren Text")
        return value.strip()

    @field_validator("permissions")
    @classmethod
    def unique_permissions(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Doppelte Modulrechte")
        return value

    @field_validator("dependencies")
    @classmethod
    def valid_dependencies(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)) or any(not MODULE_ID.fullmatch(item) for item in value):
            raise ValueError("Ungültige oder doppelte Modulabhängigkeit")
        return value

    @model_validator(mode="after")
    def no_self_dependency(self):
        if self.id in self.dependencies:
            raise ValueError("Modul hängt von sich selbst ab")
        return self


def _safe_directory(path: Path, root: Path) -> bool:
    return (path.is_dir() and not path.is_symlink() and not path.is_junction()
            and path.resolve().parent == root.resolve())


def checked_package(source: Path) -> ModuleManifest:
    source = Path(source)
    if not source.is_dir() or source.is_symlink() or source.is_junction():
        raise ValueError("Modulquelle muss ein gewöhnlicher Ordner sein")
    manifest = source / "jarvis-module.json"
    entry = source / "module.py"
    ui = source / "ui.js"
    for path in (manifest, entry, ui):
        if not path.is_file() or path.is_symlink() or path.is_junction():
            raise ValueError("Manifest, module.py und ui.js müssen gewöhnliche Dateien sein")
    if (manifest.stat().st_size > 16_384 or entry.stat().st_size > 2_000_000
            or ui.stat().st_size > 200_000):
        raise ValueError("Modulpaket ist zu groß")
    parsed = ModuleManifest.model_validate(json.loads(manifest.read_text(encoding="utf-8")))
    if parsed.id != source.name or parsed.api != MODULE_API:
        raise ValueError("Modul-ID oder API-Version passt nicht")
    return parsed


def package_digest(source: Path) -> str:
    digest = hashlib.sha256()
    for name, limit in (("jarvis-module.json", 16_384), ("module.py", 2_000_000),
                        ("ui.js", 200_000)):
        path = Path(source) / name
        if not path.is_file() or path.is_symlink() or path.is_junction():
            raise ValueError("Paketdatei fehlt oder ist ein Link")
        content = path.read_bytes()
        if len(content) > limit:
            raise ValueError("Paketdatei ist zu groß")
        digest.update(name.encode("utf-8") + b"\0" + len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def checked_installed_directory(target: Path, root: Path, *, allow_missing: bool = False) -> None:
    if (root.is_symlink() or root.is_junction() or not _safe_directory(target, root)):
        raise ValueError("Modul ist nicht installiert")
    required = {"jarvis-module.json", "module.py", "ui.js"}
    found = set()
    for child in target.iterdir():
        if child.name == "__pycache__":
            if not _safe_directory(child, target):
                raise ValueError("Modulcache ist kein sicherer Ordner")
            for cached in child.iterdir():
                if (not cached.is_file() or cached.suffix != ".pyc"
                        or cached.is_symlink() or cached.is_junction()):
                    raise ValueError("Modulcache enthält zusätzliche Dateien")
            continue
        if child.name not in required or not child.is_file():
            raise ValueError("Modulordner enthält zusätzliche Dateien; manuell prüfen")
        if child.is_symlink() or child.is_junction():
            raise ValueError("Modulordner enthält einen Link")
        found.add(child.name)
    if not allow_missing and found != required:
        raise ValueError("Modulordner ist unvollständig")


def unfinished_module_run(db: Database, module_id: str) -> bool:
    prefix = f"{module_id}__"
    with db.connect() as connection:
        return connection.execute(
            "SELECT 1 FROM runs WHERE substr(tool_name,1,?)=? "
            "AND status IN ('queued','running','waiting_approval') LIMIT 1",
            (len(prefix), prefix)).fetchone() is not None


def install_package(source: Path, data_dir: Path, db: Database | None = None) -> str:
    manifest = checked_package(source)
    source_digest = package_digest(source)
    root = Path(data_dir) / "modules"
    if root.is_symlink() or root.is_junction():
        raise ValueError("Modulverzeichnis darf kein Link sein")
    root.mkdir(parents=True, exist_ok=True)
    target = root / manifest.id
    if target.exists() or target.is_symlink() or target.is_junction():
        raise ValueError("Modul ist bereits installiert; zuerst deaktivieren und entfernen")
    target.mkdir()
    try:
        for name in ("jarvis-module.json", "module.py", "ui.js"):
            shutil.copyfile(Path(source) / name, target / name)
        copied_digest = package_digest(target)
        if copied_digest != source_digest:
            raise ValueError("Modulquelle hat sich während der Installation geändert")
        if db is not None:
            db.set_settings({f"module.{manifest.id}.package_sha256": copied_digest})
    except BaseException:
        shutil.rmtree(target)
        raise
    return manifest.id


def upgrade_package(source: Path, data_dir: Path, db: Database,
                    expected_id: str | None = None) -> str:
    manifest = checked_package(source)
    if expected_id is not None and manifest.id != expected_id:
        raise ValueError("Paketordner gehört zu einem anderen Modul")
    root = Path(data_dir) / "modules"
    target = root / manifest.id
    checked_installed_directory(target, root)
    if target.resolve() == Path(source).resolve():
        raise ValueError("Installierter Ordner kann nicht seine eigene Updatequelle sein")
    if db.get_settings().get(f"module.{manifest.id}.enabled") == "1":
        raise ValueError("Modul vor dem Update deaktivieren")
    if unfinished_module_run(db, manifest.id):
        raise ValueError("Offene Modulwerkzeugläufe zuerst abschließen oder ablehnen")
    checked_package(target)
    stored_version = db.get_settings().get(f"module.{manifest.id}.data_version", "0")
    try:
        previous_version = int(stored_version)
    except ValueError as exc:
        raise ValueError("Gespeicherte Modul-Datenversion ist ungültig") from exc
    if previous_version > manifest.data_version:
        raise ValueError("Modulcode ist älter als die gespeicherten Moduldaten")
    source_digest = package_digest(source)
    nonce = uuid4().hex
    stage = root.parent / f".jarvis-module-stage-{manifest.id}-{nonce}"
    backup = root.parent / f".jarvis-module-backup-{manifest.id}-{nonce}"
    stage.mkdir()
    old_moved = False
    new_moved = False
    try:
        for name in ("jarvis-module.json", "module.py", "ui.js"):
            shutil.copyfile(Path(source) / name, stage / name)
        if package_digest(stage) != source_digest:
            raise ValueError("Modulquelle hat sich während des Updates geändert")
        target.rename(backup)
        old_moved = True
        stage.rename(target)
        new_moved = True
        db.set_settings({f"module.{manifest.id}.package_sha256": source_digest})
    except BaseException:
        if new_moved:
            shutil.rmtree(target)
        if old_moved:
            backup.rename(target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    shutil.rmtree(backup, ignore_errors=True)
    return manifest.id


def remove_package(module_id: str, data_dir: Path, db: Database) -> str:
    if not MODULE_ID.fullmatch(module_id):
        raise ValueError("Ungültige Modul-ID")
    root = Path(data_dir) / "modules"
    target = root / module_id
    checked_installed_directory(target, root, allow_missing=True)
    if db.get_settings().get(f"module.{module_id}.enabled") == "1":
        raise ValueError("Modul zuerst in Jarvis deaktivieren")
    if unfinished_module_run(db, module_id):
        raise ValueError("Offene Modulwerkzeugläufe zuerst abschließen oder ablehnen")
    shutil.rmtree(target)
    return module_id


class ModuleRegistry:
    def __init__(self, db: Database, directory: Path, engine: RunEngine | None = None):
        self.db = db
        self.directory = Path(directory)
        self.engine = engine
        self._lock = threading.RLock()
        self._apps: dict[str, FastAPI] = {}
        self._tool_handlers: dict[str, Callable[..., dict]] = {}
        self._tool_specs: dict[str, tuple] = {}
        self._tool_owners: dict[str, str] = {}
        self._errors: dict[str, str] = {}
        self._manifests: dict[str, ModuleManifest] = {}
        self.refresh()

    def install_from(self, source: Path) -> dict[str, Any]:
        with self._lock:
            module_id = install_package(source, self.directory.parent, self.db)
            self.refresh()
            return self._summary(module_id)

    def upgrade_from(self, module_id: str, source: Path) -> dict[str, Any]:
        with self._lock:
            if module_id in self._apps:
                raise ValueError("Modul vor dem Update deaktivieren")
            upgraded = upgrade_package(source, self.directory.parent, self.db, module_id)
            self._errors.pop(upgraded, None)
            self.refresh()
            return self._summary(upgraded)

    def remove_installed(self, module_id: str) -> dict[str, Any]:
        with self._lock:
            if module_id in self._apps:
                raise ValueError("Modul zuerst deaktivieren")
            remove_package(module_id, self.directory.parent, self.db)
            self.refresh()
            return {"id": module_id, "removed": True, "data_retained": True}

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            self.refresh()
            return self.list()

    def refresh(self) -> None:
        """Discover manifests without importing module code."""
        with self._lock:
            self._manifests.clear()
            previous_errors = self._errors
            manifest_errors: dict[str, str] = {}
            if not self.directory.exists():
                self._errors = {}
                self._apps.clear()
                self._tool_handlers.clear()
                self._mark_missing_requested()
                return
            if self.directory.is_symlink() or self.directory.is_junction():
                self._errors = {"_directory": "Modulverzeichnis darf kein Link sein"}
                self._apps.clear()
                self._tool_handlers.clear()
                self._mark_missing_requested()
                return
            try:
                paths = sorted(self.directory.iterdir())
            except OSError as exc:
                self._errors = {"_directory": f"Modulverzeichnis nicht lesbar: {type(exc).__name__}"}
                self._apps.clear()
                self._tool_handlers.clear()
                self._mark_missing_requested()
                return
            for path in paths:
                try:
                    safe = _safe_directory(path, self.directory)
                except OSError as exc:
                    manifest_errors[path.name] = f"Modulordner nicht lesbar: {type(exc).__name__}"
                    continue
                if not safe:
                    continue
                manifest_path = path / "jarvis-module.json"
                entry_path = path / "module.py"
                ui_path = path / "ui.js"
                if (not manifest_path.is_file() or manifest_path.is_symlink()
                        or not entry_path.is_file() or entry_path.is_symlink()
                        or not ui_path.is_file() or ui_path.is_symlink()):
                    manifest_errors[path.name] = "Manifest, module.py oder ui.js fehlt oder ist ein Link"
                    continue
                try:
                    if manifest_path.stat().st_size > 16_384:
                        raise ValueError("Manifest ist zu groß")
                    manifest = ModuleManifest.model_validate(
                        json.loads(manifest_path.read_text(encoding="utf-8")))
                    if manifest.id != path.name:
                        raise ValueError("Modul-ID und Verzeichnisname unterscheiden sich")
                    if manifest.api != MODULE_API:
                        raise ValueError("Inkompatible Modul-API")
                    self._verify_package(manifest.id, path)
                    self._manifests[manifest.id] = manifest
                except (ValueError, OSError) as exc:
                    manifest_errors[path.name] = str(exc)
            self._errors = {module_id: message for module_id, message in previous_errors.items()
                            if module_id in self._manifests and message != MISSING_PACKAGE_ERROR}
            self._errors.update(manifest_errors)
            for module_id in list(self._apps):
                if module_id not in self._manifests:
                    self._deactivate(module_id)
            self._deactivate_missing_dependents()
            self._mark_missing_requested()

    def _mark_missing_requested(self) -> None:
        for key, value in self.db.get_settings().items():
            if not (key.startswith("module.") and key.endswith(".enabled") and value == "1"):
                continue
            module_id = key[len("module."):-len(".enabled")]
            if MODULE_ID.fullmatch(module_id) and module_id not in self._manifests:
                self._errors.setdefault(module_id, MISSING_PACKAGE_ERROR)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            result = []
            stored = self.db.get_settings()
            for manifest in self._manifests.values():
                item = manifest.model_dump()
                item["enabled"] = manifest.id in self._apps
                item["error"] = self._errors.get(manifest.id)
                item["requested_enabled"] = stored.get(f"module.{manifest.id}.enabled") == "1"
                result.append(item)
            for module_id, error in self._errors.items():
                if module_id not in self._manifests:
                    result.append({"id": module_id, "name": module_id, "version": "?", "api": 0,
                                   "data_version": 0, "element": "",
                                   "navigation": module_id, "view": "web_component_v1", "permissions": [],
                                   "dependencies": [], "enabled": False,
                                   "requested_enabled": stored.get(f"module.{module_id}.enabled") == "1",
                                   "error": error})
            return result

    def activate_saved(self) -> None:
        stored = self.db.get_settings()
        pending = {module_id for module_id in self._manifests
                   if stored.get(f"module.{module_id}.enabled") == "1"}
        while pending:
            progressed = False
            for module_id in sorted(pending):
                dependencies = self._manifests[module_id].dependencies
                if any(dependency in pending for dependency in dependencies):
                    continue
                pending.remove(module_id)
                progressed = True
                try:
                    self.enable(module_id)
                except (ValueError, RuntimeError, OSError) as exc:
                    self._errors.setdefault(module_id, str(exc))
            if not progressed:
                for module_id in pending:
                    self._errors[module_id] = "Zyklische Modulabhängigkeit"
                break

    def enable(self, module_id: str) -> dict[str, Any]:
        with self._lock:
            manifest = self._manifests.get(module_id)
            if manifest is None:
                raise ValueError("Modul ist nicht installiert")
            if module_id in self._apps:
                return self._summary(module_id)
            if any(dependency not in self._apps for dependency in manifest.dependencies):
                raise ValueError("Benötigtes Modul ist nicht aktiv")
            for active_id in self._apps:
                if self._manifests[active_id].element == manifest.element:
                    raise ValueError(f"Moduloberfläche wird bereits von {active_id} verwendet")
            package = self.directory / module_id
            if not _safe_directory(package, self.directory):
                raise ValueError("Modulordner ist nicht verfügbar")
            path = package / "module.py"
            if not path.is_file() or path.is_symlink() or path.is_junction():
                raise ValueError("Moduldatei ist nicht verfügbar")
            spec = importlib.util.spec_from_file_location(f"jarvis_external_{module_id}", path)
            if spec is None or spec.loader is None:
                raise ValueError("Moduldatei kann nicht geladen werden")
            module = importlib.util.module_from_spec(spec)
            try:
                self._verify_package(module_id, package)
                spec.loader.exec_module(module)
                migrate = getattr(module, "migrate", None)
                factory = getattr(module, "create_app", None)
                if not callable(migrate) or not callable(factory):
                    raise TypeError("Modul benötigt migrate(db) und create_app(db)")
                key = f"module.{module_id}.data_version"
                raw_version = self.db.get_settings().get(key, "0")
                try:
                    old_version = int(raw_version)
                except ValueError as exc:
                    raise ValueError("Gespeicherte Modul-Datenversion ist ungültig") from exc
                if old_version > manifest.data_version:
                    raise ValueError("Modulcode ist älter als die gespeicherten Moduldaten")
                if old_version < manifest.data_version:
                    with self.db.connect() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        migrate(connection, old_version)
                        connection.execute(
                            "INSERT INTO app_settings(key,value) VALUES(?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (key, str(manifest.data_version)))
                module_app = factory(self.db)
                if not isinstance(module_app, FastAPI):
                    raise TypeError("create_app muss eine FastAPI-App liefern")
                definitions = getattr(module, "tool_definitions", None)
                if definitions is not None:
                    if not callable(definitions):
                        raise TypeError("tool_definitions muss aufrufbar sein")
                    self._register_tools(module_id, definitions(self.db))
            except (Exception, SystemExit) as exc:
                # An optional package must not take the core down at startup.
                self._errors[module_id] = f"{type(exc).__name__}: {exc}"
                raise RuntimeError("Modul konnte nicht aktiviert werden") from exc
            self._apps[module_id] = module_app
            self._errors.pop(module_id, None)
            self.db.set_settings({f"module.{module_id}.enabled": "1"})
            return self._summary(module_id)

    def disable(self, module_id: str) -> dict[str, Any]:
        with self._lock:
            if module_id not in self._manifests:
                if module_id not in self._errors or not MODULE_ID.fullmatch(module_id):
                    raise ValueError("Modul ist nicht installiert")
                self._deactivate(module_id)
                self.db.set_settings({f"module.{module_id}.enabled": "0"})
                return {"id": module_id, "enabled": False, "error": self._errors[module_id]}
            for dependent in self._manifests.values():
                if dependent.id in self._apps and module_id in dependent.dependencies:
                    raise ValueError(f"Aktives Modul {dependent.id} benötigt dieses Modul")
            self._deactivate(module_id)
            self.db.set_settings({f"module.{module_id}.enabled": "0"})
            return self._summary(module_id)

    def _deactivate(self, module_id: str) -> None:
        self._apps.pop(module_id, None)
        for name in list(self._tool_handlers):
            if self._tool_owners.get(name) == module_id:
                del self._tool_handlers[name]

    def _summary(self, module_id: str) -> dict[str, Any]:
        result = self._manifests[module_id].model_dump()
        result["enabled"] = module_id in self._apps
        result["error"] = self._errors.get(module_id)
        return result

    def app_for(self, module_id: str) -> FastAPI | None:
        with self._lock:
            module_app = self._apps.get(module_id)
            if module_app is None:
                return None
            try:
                self._verify_package(module_id, self.directory / module_id)
            except (ValueError, OSError) as exc:
                self._deactivate(module_id)
                self._errors[module_id] = str(exc)
                self._deactivate_missing_dependents()
                return None
            return module_app

    def _verify_package(self, module_id: str, path: Path) -> None:
        if not _safe_directory(path, self.directory):
            raise ValueError("Modulordner ist nicht verfügbar")
        digest = package_digest(path)
        key = f"module.{module_id}.package_sha256"
        expected = self.db.get_settings().get(key)
        if expected is not None and expected != digest:
            raise ValueError("Paketdateien wurden seit Installation verändert; neu installieren")
        if expected is None:
            # Adopt a local package placed before install receipts were introduced.
            self.db.set_settings({key: digest})

    def _deactivate_missing_dependents(self) -> None:
        changed = True
        while changed:
            changed = False
            for module_id in list(self._apps):
                if any(dependency not in self._apps
                       for dependency in self._manifests[module_id].dependencies):
                    self._deactivate(module_id)
                    self._errors[module_id] = "Benötigtes Modul ist nicht mehr aktiv"
                    changed = True

    def _register_tools(self, module_id: str, definitions: list[ToolDefinition]) -> None:
        if self.engine is None:
            return
        if not isinstance(definitions, list) or len(definitions) > 16:
            raise ValueError("Modulwerkzeuge müssen eine begrenzte Liste sein")
        planned: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if not isinstance(definition, ToolDefinition):
                raise TypeError("Ungültiges Modulwerkzeug")
            if not MODULE_ID.fullmatch(definition.name):
                raise ValueError("Ungültiger Modulwerkzeugname")
            if (not isinstance(definition.schema, type)
                    or not issubclass(definition.schema, BaseModel)
                    or not callable(definition.handler)):
                raise TypeError("Modulwerkzeug benötigt Eingabeschema und Handler")
            if (not isinstance(definition.scope, str) or not definition.scope
                    or len(definition.scope) > 100 or definition.effect not in
                    ("read", "local_open", "write")
                    or type(definition.timeout_s) is not int
                    or not 1 <= definition.timeout_s <= 300
                    or type(definition.cancel_aware) is not bool):
                raise ValueError("Ungültige Modulwerkzeug-Eigenschaften")
            name = f"{module_id}__{definition.name}"
            if name in planned or len(name) > 80:
                raise ValueError("Doppelter oder zu langer Modulwerkzeugname")
            planned[name] = definition
        specs = {name: (definition.schema.model_json_schema(), definition.scope,
                        definition.effect, definition.timeout_s, definition.cancel_aware)
                 for name, definition in planned.items()}
        for name, definition in planned.items():
            spec = specs[name]
            if name in self._tool_specs:
                if self._tool_owners[name] != module_id:
                    raise ValueError("Modulwerkzeugname kollidiert mit anderem Modul")
                if self._tool_specs[name] != spec:
                    raise ValueError("Modulwerkzeug-Schema wurde geändert; Jarvis neu starten")
        new_names = []
        try:
            for name, definition in planned.items():
                if name in self._tool_specs:
                    continue
                registered = replace(
                    definition, name=name,
                    handler=lambda *args, _name=name, **kwargs: self._dispatch_tool(
                        _name, *args, **kwargs),
                    enabled=lambda _id=module_id: self.app_for(_id) is not None)
                self.engine.register(registered)
                self._tool_specs[name] = specs[name]
                self._tool_owners[name] = module_id
                new_names.append(name)
        except Exception:
            for name in new_names:
                self.engine.definitions.pop(name, None)
                self._tool_specs.pop(name, None)
                self._tool_owners.pop(name, None)
            raise
        for name, definition in planned.items():
            self._tool_handlers[name] = definition.handler

    def _dispatch_tool(self, name: str, *args: Any, **kwargs: Any) -> dict:
        with self._lock:
            module_id = self._tool_owners.get(name)
            handler = self._tool_handlers.get(name) if module_id in self._apps else None
        if handler is None:
            raise ToolUserError("Modulwerkzeug ist deaktiviert")
        return handler(*args, **kwargs)


class ModuleDispatcher:
    def __init__(self, registry: ModuleRegistry):
        self.registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await JSONResponse({"detail": "Nur HTTP unterstützt"}, status_code=404)(scope, receive, send)
            return
        path = scope.get("path", "")
        if not path.startswith("/modules/"):
            await JSONResponse({"detail": "Modul nicht gefunden"}, status_code=404)(scope, receive, send)
            return
        module_id, _, remaining = path[len("/modules/"):].partition("/")
        module_app = await run_in_threadpool(self.registry.app_for, module_id)
        if module_app is None:
            await JSONResponse({"detail": "Modul nicht aktiv"}, status_code=404)(scope, receive, send)
            return
        inner_scope = dict(scope)
        if remaining == "ui.js":
            package = self.registry.directory / module_id
            ui_path = package / "ui.js"
            if (not ui_path.is_file() or ui_path.is_symlink() or ui_path.is_junction()
                    or not _safe_directory(package, self.registry.directory)
                    or ui_path.stat().st_size > 200_000):
                await JSONResponse({"detail": "Moduloberfläche fehlt"}, status_code=404)(
                    scope, receive, send)
                return
            await FileResponse(ui_path, media_type="text/javascript; charset=utf-8",
                               headers={"Cache-Control": "no-store"})(scope, receive, send)
            return
        inner_scope["path"] = "/" + remaining
        inner_scope["root_path"] = ""
        await module_app(inner_scope, receive, send)
