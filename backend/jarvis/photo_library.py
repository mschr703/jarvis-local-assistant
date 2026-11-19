"""Explicit local photo folder with verified previews and manual event links."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from jarvis.db import Database, utcnow

MAX_PHOTOS = 300
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_PHOTO_PIXELS = 20_000_000
MAX_ALBUM_PHOTOS = 10
MAX_ALBUM_BYTES = 64 * 1024 * 1024
FORMATS = {"JPEG": (".jpg", ".jpeg"), "PNG": (".png",), "WEBP": (".webp",)}
MEDIA_TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class PhotoConflict(ValueError):
    """An asset or its source has changed since confirmation."""


def _root(path: str) -> Path:
    supplied = Path(path).expanduser()
    if not supplied.is_absolute() or not supplied.is_dir():
        raise ValueError("Es wird ein vorhandener absoluter Fotoordner benötigt")
    resolved = supplied.resolve(strict=True)
    if supplied != resolved or supplied.is_symlink() or supplied.is_junction():
        raise ValueError("Umgeleiteter Fotoordner ist nicht erlaubt")
    return resolved


def _image_format(data: bytes, suffix: str) -> str:
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            if (image_format not in FORMATS or suffix.lower() not in FORMATS[image_format]
                    or image.width * image.height > MAX_PHOTO_PIXELS):
                raise ValueError("Fotoformat oder Bildgröße ist nicht unterstützt")
            image.verify()
            return image_format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("Foto kann nicht sicher gelesen werden") from exc


class PhotoLibrary:
    def __init__(self, db: Database):
        self.db = db

    def folder(self) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM photo_folders LIMIT 1").fetchone()
            return dict(row) if row else None

    def add_folder(self, path: str) -> dict:
        root = _root(path)
        with self.db.connect() as db:
            existing = db.execute("SELECT * FROM photo_folders LIMIT 1").fetchone()
            if existing:
                if existing["path"] == str(root):
                    return dict(existing)
                raise ValueError("Zuerst die bisherige Fotoordnerfreigabe entfernen")
            identifier, now = str(uuid4()), utcnow()
            db.execute("INSERT INTO photo_folders(id,path,created_at) VALUES(?,?,?)",
                       (identifier, str(root), now))
            return {"id": identifier, "path": str(root), "created_at": now, "version": 1}

    def remove_folder(self, folder_id: str) -> None:
        with self.db.connect() as db:
            removed = db.execute("DELETE FROM photo_folders WHERE id=?", (folder_id,))
            if not removed.rowcount:
                raise LookupError("Fotoordner nicht gefunden")

    def rebind_folder(self, folder_id: str, path: str, expected_version: int) -> dict:
        new_root = _root(path)
        with self.db.connect() as db:
            folder = db.execute("SELECT * FROM photo_folders WHERE id=?", (folder_id,)).fetchone()
            if folder is None:
                raise LookupError("Fotoordner nicht gefunden")
            if folder["version"] != expected_version:
                raise PhotoConflict("Fotoordner wurde inzwischen geändert")
            if folder["path"] == str(new_root):
                raise ValueError("Neuer Fotoordnerpfad muss sich unterscheiden")
            assets = [dict(row) for row in db.execute(
                "SELECT id,name,size,sha256 FROM photo_assets WHERE folder_id=? ORDER BY id",
                (folder_id,))]
        mtimes = {}
        for asset in assets:
            try:
                data, mtime_ns = self._read_source(new_root, asset["name"])
            except (OSError, ValueError) as exc:
                raise PhotoConflict(f"Foto {asset['name']} fehlt oder ist nicht lesbar") from exc
            if len(data) != asset["size"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
                raise PhotoConflict(f"Foto {asset['name']} stimmt nicht mit dem Register überein")
            mtimes[asset["id"]] = mtime_ns
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT path,version FROM photo_folders WHERE id=?",
                                 (folder_id,)).fetchone()
            if (not current or current["version"] != expected_version
                    or current["path"] != folder["path"]):
                raise PhotoConflict("Fotoordner wurde inzwischen geändert")
            current_assets = [dict(row) for row in db.execute(
                "SELECT id,name,size,sha256 FROM photo_assets WHERE folder_id=? ORDER BY id",
                (folder_id,))]
            if current_assets != assets:
                raise PhotoConflict("Fotoregister wurde inzwischen geändert")
            db.execute("UPDATE photo_folders SET path=?,version=version+1 WHERE id=?",
                       (str(new_root), folder_id))
            for asset_id, mtime_ns in mtimes.items():
                db.execute("UPDATE photo_assets SET mtime_ns=? WHERE id=?", (mtime_ns, asset_id))
        return self.folder()

    @staticmethod
    def _source(root: Path, name: str) -> Path:
        if not name or name != Path(name).name or name in {".", ".."}:
            raise ValueError("Ungültiger Fotodateiname")
        path = root / name
        if (path.is_symlink() or path.is_junction()
                or not path.is_file() or path.resolve(strict=True) != path):
            raise PhotoConflict("Foto ist nicht mehr am bestätigten Ort")
        return path

    @classmethod
    def _read_source(cls, root: Path, name: str) -> tuple[bytes, int]:
        path = cls._source(root, name)
        before = path.stat()
        if not 0 < before.st_size <= MAX_PHOTO_BYTES:
            raise ValueError("Foto ist leer oder größer als 15 MiB")
        with path.open("rb") as file:
            data = file.read(MAX_PHOTO_BYTES + 1)
        after = path.stat()
        if (len(data) != before.st_size or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or path.resolve(strict=True) != path):
            raise PhotoConflict("Foto wurde während des Lesens geändert")
        return data, after.st_mtime_ns

    def index(self, folder_id: str) -> dict:
        folder = self.folder()
        if not folder or folder["id"] != folder_id:
            raise LookupError("Fotoordner nicht gefunden")
        root = _root(folder["path"])
        counts = {"indexed": 0, "unchanged": 0, "skipped": 0, "limited": False}
        suffixes = {suffix for values in FORMATS.values() for suffix in values}
        candidates = sorted((path for path in root.iterdir() if path.suffix.lower() in suffixes),
                            key=lambda path: path.name.casefold())
        if len(candidates) > MAX_PHOTOS:
            counts["limited"] = True
            counts["skipped"] += len(candidates) - MAX_PHOTOS
        for candidate in candidates[:MAX_PHOTOS]:
            try:
                data, mtime_ns = self._read_source(root, candidate.name)
                image_format = _image_format(data, candidate.suffix)
            except (OSError, ValueError):
                counts["skipped"] += 1
                continue
            digest = hashlib.sha256(data).hexdigest()
            with self.db.connect() as db:
                current = db.execute("SELECT * FROM photo_assets WHERE folder_id=? AND name=?",
                                     (folder_id, candidate.name)).fetchone()
                if current and current["sha256"] == digest:
                    db.execute("UPDATE photo_assets SET mtime_ns=?,indexed_at=? WHERE id=?",
                               (mtime_ns, utcnow(), current["id"]))
                    counts["unchanged"] += 1
                elif current:
                    db.execute("UPDATE photo_assets SET sha256=?,size=?,mtime_ns=?,media_type=?,"
                               "event_id=NULL,version=version+1,indexed_at=? WHERE id=?",
                               (digest, len(data), mtime_ns, MEDIA_TYPES[image_format],
                                utcnow(), current["id"]))
                    counts["indexed"] += 1
                else:
                    total = db.execute("SELECT count(*) FROM photo_assets").fetchone()[0]
                    if total >= MAX_PHOTOS:
                        counts["skipped"] += 1
                        counts["limited"] = True
                        continue
                    db.execute("INSERT INTO photo_assets(id,folder_id,name,sha256,size,mtime_ns,"
                               "media_type,indexed_at) VALUES(?,?,?,?,?,?,?,?)",
                               (str(uuid4()), folder_id, candidate.name, digest, len(data),
                                mtime_ns, MEDIA_TYPES[image_format], utcnow()))
                    counts["indexed"] += 1
        return counts

    def _asset(self, asset_id: str) -> dict:
        with self.db.connect() as db:
            row = db.execute("SELECT a.*,f.path AS folder_path,e.title AS event_title,"
                             "e.date_local AS event_date FROM photo_assets a "
                             "JOIN photo_folders f ON f.id=a.folder_id "
                             "LEFT JOIN memory_events e ON e.id=a.event_id "
                             "WHERE a.id=?", (asset_id,)).fetchone()
            if not row:
                raise LookupError("Foto nicht gefunden")
            return dict(row)

    def _public(self, asset: dict) -> dict:
        try:
            root = _root(asset["folder_path"])
            path = self._source(root, asset["name"])
            stat = path.stat()
            status = ("current" if stat.st_size == asset["size"]
                      and stat.st_mtime_ns == asset["mtime_ns"] else "changed")
        except (OSError, ValueError):
            status = "unavailable"
        return {key: asset[key] for key in ("id", "name", "sha256", "size", "media_type",
                                            "event_id", "event_title", "event_date", "version",
                                            "indexed_at")} | {"status": status}

    def list(self) -> list[dict]:
        with self.db.connect() as db:
            rows = db.execute("SELECT a.*,f.path AS folder_path,e.title AS event_title,"
                              "e.date_local AS event_date FROM photo_assets a "
                              "JOIN photo_folders f ON f.id=a.folder_id "
                              "LEFT JOIN memory_events e ON e.id=a.event_id "
                              "ORDER BY a.name COLLATE NOCASE LIMIT ?", (MAX_PHOTOS,)).fetchall()
        return [self._public(dict(row)) for row in rows]

    def _verified_bytes(self, asset: dict) -> bytes:
        try:
            root = _root(asset["folder_path"])
            data, mtime_ns = self._read_source(root, asset["name"])
        except (OSError, ValueError) as exc:
            raise PhotoConflict("Fotoquelle ist nicht mehr verfügbar") from exc
        if (len(data) != asset["size"] or mtime_ns != asset["mtime_ns"]
                or hashlib.sha256(data).hexdigest() != asset["sha256"]):
            raise PhotoConflict("Fotoquelle wurde geändert; Ordner neu einlesen")
        return data

    def preview(self, asset_id: str) -> bytes:
        asset = self._asset(asset_id)
        data = self._verified_bytes(asset)
        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_PHOTO_PIXELS:
                    raise PhotoConflict("Foto ist zu groß")
                image = ImageOps.exif_transpose(image)
                image.thumbnail((640, 640))
                output = io.BytesIO()
                if image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                image.save(output, format="JPEG", quality=82, optimize=True)
                return output.getvalue()
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise PhotoConflict("Foto kann nicht angezeigt werden") from exc

    def set_event(self, asset_id: str, event_id: str | None, expected_version: int) -> dict:
        asset = self._asset(asset_id)
        if event_id:
            self._verified_bytes(asset)
        with self.db.connect() as db:
            if event_id and not db.execute("SELECT 1 FROM memory_events WHERE id=?",
                                           (event_id,)).fetchone():
                raise ValueError("Ereignis nicht gefunden")
            changed = db.execute("UPDATE photo_assets SET event_id=?,version=version+1 "
                                 "WHERE id=? AND version=?",
                                 (event_id, asset_id, expected_version))
            if not changed.rowcount:
                if db.execute("SELECT 1 FROM photo_assets WHERE id=?", (asset_id,)).fetchone():
                    raise PhotoConflict("Fotozuordnung wurde inzwischen geändert")
                raise LookupError("Foto nicht gefunden")
        return self._public(self._asset(asset_id))

    def album(self, event_id: str) -> bytes:
        with self.db.connect() as db:
            event = db.execute("SELECT * FROM memory_events WHERE id=?", (event_id,)).fetchone()
            if event is None:
                raise LookupError("Ereignis nicht gefunden")
            rows = db.execute("SELECT a.*,f.path AS folder_path FROM photo_assets a "
                              "JOIN photo_folders f ON f.id=a.folder_id "
                              "WHERE a.event_id=? ORDER BY a.name COLLATE NOCASE",
                              (event_id,)).fetchall()
        if not rows:
            raise ValueError("Für dieses Ereignis sind keine Fotos zugeordnet")
        if len(rows) > MAX_ALBUM_PHOTOS:
            raise ValueError("Ein Album enthält höchstens zehn zugeordnete Fotos")
        if sum(row["size"] for row in rows) > MAX_ALBUM_BYTES:
            raise ValueError("Album überschreitet 64 MiB")
        photo_bytes: list[tuple[str, bytes, dict]] = []
        total = 0
        for index, row in enumerate(rows, 1):
            asset = dict(row)
            data = self._verified_bytes(asset)
            total += len(data)
            if total > MAX_ALBUM_BYTES:
                raise ValueError("Album überschreitet 64 MiB")
            stem = re.sub(r"[^\w.-]", "_", Path(asset["name"]).stem, flags=re.UNICODE)
            archive_name = f"photos/{index:02d}-{stem[:60]}{Path(asset['name']).suffix.lower()}"
            photo_bytes.append((archive_name, data, asset))
        event_public = {key: event[key] for key in
                        ("date_local", "title", "description", "location", "source_kind")}
        manifest = {"format": "jarvis-life-album-v1", "event": event_public,
                    "photos": [{"name": asset["name"], "archive_name": archive_name,
                                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                               for archive_name, data, asset in photo_bytes]}
        lines = [f"# {event['date_local']} · {event['title']}", "",
                 f"Ort: {event['location'] or 'Nicht angegeben'}", "",
                 event["description"] or "Keine Beschreibung.", "", "## Fotos", ""]
        lines.extend(f"- {asset['name']} → {archive_name}" for archive_name, _, asset in photo_bytes)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("album.md", "\n".join(lines).encode("utf-8"))
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False,
                                                         indent=2).encode("utf-8"))
            for archive_name, data, _asset in photo_bytes:
                archive.writestr(archive_name, data)
        return output.getvalue()
