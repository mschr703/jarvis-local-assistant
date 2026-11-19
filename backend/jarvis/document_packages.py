"""Bounded local ZIP exports from freshly verified document originals."""

import io
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile

from jarvis.db import utcnow
from jarvis.documents import DocumentStore

MAX_PACKAGE_DOCUMENTS = 10
MAX_PACKAGE_BYTES = 32 * 1024 * 1024


def build_document_package(documents: DocumentStore, document_ids: list[str]) -> bytes:
    if not 1 <= len(document_ids) <= MAX_PACKAGE_DOCUMENTS:
        raise ValueError("Für ein Unterlagenpaket 1 bis 10 Dokumente wählen")
    if len(document_ids) != len(set(document_ids)):
        raise ValueError("Jedes Dokument nur einmal wählen")

    selected: list[tuple[str, bytes]] = []
    entries = []
    total = 0
    for number, document_id in enumerate(document_ids, 1):
        item, original = documents.original_bytes(document_id)
        total += len(original)
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("Unterlagenpaket überschreitet insgesamt 32 MiB")
        filename = re.split(r"[\\/]", item["path"])[-1]
        if not filename or filename in {".", ".."}:
            raise ValueError("Dokumentname ist für ein Unterlagenpaket ungeeignet")
        safe_name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", filename).rstrip(" .")[:120]
        if not safe_name:
            raise ValueError("Dokumentname ist für ein Unterlagenpaket ungeeignet")
        archive_name = f"{number:02d}_{safe_name}"
        selected.append((archive_name, original))
        entries.append({"archive_name": archive_name, "filename": filename,
                        "document_id": document_id, "sha256": item["sha256"],
                        "size": len(original)})

    output = io.BytesIO()
    manifest = {"format_version": 1, "created_at": utcnow(), "files": entries}
    with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for archive_name, original in selected:
            archive.writestr(archive_name, original)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False,
                                                      indent=2).encode("utf-8"))
    return output.getvalue()
