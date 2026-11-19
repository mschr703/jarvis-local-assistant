"""Öffnet eine isolierte erlaubte Textdatei über die Windows-Dateizuordnung."""

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from jarvis.db import Database
from jarvis.desktop import DesktopTools, OpenFileArgs
from jarvis.documents import DocumentStore


def main() -> None:
    if sys.platform != "win32":
        raise RuntimeError("Diese Desktopprobe benötigt Windows")
    with TemporaryDirectory(prefix="jarvis-open-file-") as temporary:
        root = Path(temporary)
        source_dir = root / "sources"
        source_dir.mkdir()
        source = source_dir / "freigabe.txt"
        source.write_text("Jarvis-Dateiöffnung mit isolierter Umgebung.\n", encoding="utf-8")
        documents = DocumentStore(Database(root / "data" / "jarvis.db"))
        folder = documents.add_folder(str(source_dir), "local_only")
        assert documents.index_folder(folder["id"])["indexed"] == 1
        document = documents.documents()[0]
        os.environ["JARVIS_OPENROUTER_API_KEY"] = "fixture-not-a-real-key"
        desktop = DesktopTools(documents, root / "data")
        args = OpenFileArgs(file_id=document["id"], expected_sha256=document["sha256"])
        result = desktop.open_file(args)
        assert result["status"] == "opened" and result["path"] == str(source.resolve())
        source.write_text("Extern geändert.\n", encoding="utf-8")
        try:
            desktop.open_file(args)
        except ValueError as exc:
            assert "seit dem gespeicherten Arbeitsstand geändert" in str(exc)
        else:
            raise AssertionError("Veränderte Datei wurde trotz gespeichertem Hash geöffnet")
        print(json.dumps({"file_status": result["status"], "changed_file": "blocked",
                          "secret": "fixture_only"}))


if __name__ == "__main__":
    main()
