"""Constrained local CAD jobs for the G07 two-hole holder."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jarvis.engine import RunEngine, ToolDefinition, ToolUserError

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "integrations" / "cad" / "holder.py"
VERIFIER = ROOT / "integrations" / "cad" / "verify_holder.py"
CAD_COMMAND = ("-m", "uv", "tool", "run", "--no-config", "--managed-python",
               "--python", "3.14", "--from", "cad-server==0.10.3", "cad")
MAX_PNG_BYTES = 4_000_000
MAX_EXPORT_BYTES = 20_000_000


class HolderBuildArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=3, max_length=500)
    width_mm: float = Field(gt=0, le=500)
    depth_mm: float = Field(gt=0, le=500)
    thickness_mm: float = Field(gt=0, le=100)
    hole_diameter_mm: float = Field(gt=0, le=100)
    hole_spacing_mm: float = Field(gt=0, le=500)

    @model_validator(mode="after")
    def validate_holder(self) -> HolderBuildArgs:
        values = (self.width_mm, self.depth_mm, self.thickness_mm,
                  self.hole_diameter_mm, self.hole_spacing_mm)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Maße müssen endliche Zahlen sein")
        if self.hole_spacing_mm + self.hole_diameter_mm >= self.width_mm:
            raise ValueError("Bohrungen benötigen Abstand zum Rand")
        if self.hole_diameter_mm >= self.depth_mm:
            raise ValueError("Bohrungsdurchmesser muss kleiner als die Tiefe sein")
        if not self.description.strip():
            raise ValueError("Beschreibung fehlt")
        return self


class HolderProposalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    description: str = Field(min_length=10, max_length=500)


def validate_dimension_proposal(description: str, proposal: dict) -> HolderBuildArgs:
    """The local model may classify supplied numbers, never invent dimensions."""
    supplied = [float(match.replace(",", ".")) for match in
                re.findall(r"(?<![\w.])-?\d+(?:[.,]\d+)?(?=\s*mm\b)",
                           description, flags=re.IGNORECASE)]
    if not supplied:
        raise ToolUserError("Die Beschreibung enthält keine Maße")
    fields = ("width_mm", "depth_mm", "thickness_mm", "hole_diameter_mm",
              "hole_spacing_mm")
    for field in fields:
        value = proposal.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolUserError("Das lokale Modell lieferte keinen vollständigen Maßvorschlag")
        if not any(abs(value - number) < 0.001 for number in supplied):
            raise ToolUserError("Das lokale Modell ergänzte ein nicht genanntes Maß")
    try:
        return HolderBuildArgs(description=description, **{field: proposal[field] for field in fields})
    except ValueError as exc:
        raise ToolUserError("Die vorgeschlagenen Maße sind ungültig") from exc


class HolderExportArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_id: UUID
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    format: Literal["step", "stl"]


def render_holder(args: HolderBuildArgs) -> str:
    """Only numeric constants from a reviewed template may vary."""
    source = TEMPLATE.read_text(encoding="utf-8")
    substitutions = {
        "WIDTH_MM": args.width_mm, "DEPTH_MM": args.depth_mm,
        "THICKNESS_MM": args.thickness_mm,
        "HOLE_DIAMETER_MM": args.hole_diameter_mm,
        "HOLE_SPACING_MM": args.hole_spacing_mm,
    }
    for name, number in substitutions.items():
        pattern = rf"(?m)^{name} = \d+(?:\.\d+)?$"
        source, count = re.subn(pattern, f"{name} = {number!r}", source)
        if count != 1:
            raise RuntimeError(f"CAD-Vorlage verändert: {name}")
    return source


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CadJob:
    def __init__(self, data_dir: Path, cancel_event: threading.Event):
        self.data_dir = data_dir
        self.cancel_event = cancel_event
        self.python = shutil.which("python")
        if not self.python:
            raise ToolUserError("Python fehlt für die lokale CAD-Laufzeit")
        self.temporary: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None
        self.env: dict[str, str] = {}
        self.deadline = time.monotonic() + 260

    def __enter__(self) -> Self:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="jarvis-cad-job-", dir=self.data_dir,
                                                      ignore_cleanup_errors=True)
        self.root = Path(self.temporary.name).resolve()
        if not self.root.is_relative_to(self.data_dir.resolve()):
            raise RuntimeError("Unsicheres CAD-Arbeitsverzeichnis")
        self.env = os.environ.copy()
        for key in ("JARVIS_OPENROUTER_API_KEY", "OPENROUTER_API_KEY"):
            self.env.pop(key, None)
        self.env.update(CAD_TELEMETRY="0", CAD_UPDATE_CHECK="0", DO_NOT_TRACK="1",
                        CAD_ENGINE_DIR=str(self.root / "engine"),
                        CAD_STATE_DIR=str(self.root / "state"), CAD_ENGINE_PORT="0",
                        CAD_ENGINE_WORKERS="1", CAD_ENGINE_STANDBY="0")
        return self

    def _command(self, *args: str) -> list[str]:
        return [self.python or sys.executable, *CAD_COMMAND, *args]

    def _engine_pids(self) -> list[int]:
        try:
            result = subprocess.run(self._command("status"), cwd=ROOT, env=self.env,
                                    capture_output=True, text=True, timeout=8, check=False)
            server = re.search(r"engine server (\d+)", result.stdout)
            if not server:
                return []
            workers = re.findall(r"\b(?:idle|busy)\s+\S+\s+(\d+)\b", result.stdout)
            return list(dict.fromkeys([int(server.group(1)), *(int(pid) for pid in workers)]))
        except (OSError, subprocess.TimeoutExpired):
            return []

    @staticmethod
    def _kill_tree(pid: int) -> None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=10, check=False)
        else:
            import signal
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def run(self, *args: str) -> str:
        if self.cancel_event.is_set():
            raise ToolUserError("CAD-Auftrag gestoppt")
        if self.root is None:
            raise RuntimeError("CAD-Job nicht gestartet")
        log_path = self.root / "cad-command.log"
        with log_path.open("w+", encoding="utf-8", errors="replace") as log:
            process = subprocess.Popen(self._command(*args), cwd=ROOT, env=self.env,
                                       stdout=log, stderr=subprocess.STDOUT)
            try:
                while process.poll() is None:
                    if self.cancel_event.is_set() or time.monotonic() >= self.deadline:
                        engine_pids = self._engine_pids()
                        self._kill_tree(process.pid)
                        for pid in engine_pids:
                            self._kill_tree(pid)
                        process.wait(timeout=10)
                        if self.cancel_event.is_set():
                            raise ToolUserError("CAD-Auftrag gestoppt")
                        raise ToolUserError("CAD-Zeitgrenze überschritten")
                    time.sleep(0.2)
                log.seek(0)
                output = log.read()[-4000:]
                if process.returncode:
                    raise ToolUserError("CAD-Bau fehlgeschlagen: " + output[-300:])
                return output
            finally:
                if process.poll() is None:
                    self._kill_tree(process.pid)

    def __exit__(self, *_exc: object) -> None:
        for pid in self._engine_pids():
            self._kill_tree(pid)
        if self.temporary:
            self.temporary.cleanup()
            root = self.root
            if (root is not None and root.name.startswith("jarvis-cad-job-")
                    and root.is_relative_to(self.data_dir.resolve())):
                for _ in range(4):
                    if not root.exists():
                        break
                    time.sleep(0.25)
                    shutil.rmtree(root, ignore_errors=True)


class CadTools:
    def __init__(self, data_dir: Path):
        self.models = data_dir / "cad-models"
        self.jobs = data_dir / "cad-jobs"

    def model_directory(self, model_id: UUID) -> Path:
        if self.models.is_symlink() or self.models.is_junction():
            raise ToolUserError("Unsicherer CAD-Speicher")
        directory = self.models / str(model_id)
        if directory.is_symlink() or directory.is_junction():
            raise ToolUserError("Unsicheres CAD-Modell")
        return directory

    def build(self, args: HolderBuildArgs, cancel_event: threading.Event) -> dict:
        model_id = uuid4()
        directory = self.model_directory(model_id)
        directory.mkdir(parents=True, exist_ok=False)
        script = directory / "holder.py"
        script.write_text(render_holder(args), encoding="utf-8")
        digest = sha256(script)
        try:
            with CadJob(self.jobs, cancel_event) as job:
                output = job.run("run", str(script))
                match = re.search(r"\bid:\s*([A-Za-z0-9_-]+)", output)
                if not match:
                    raise ToolUserError("CAD-Lauf lieferte keine Modell-ID")
                preview = directory / "preview.png"
                job.run("snapshot", match.group(1), str(preview), "--width", "960", "--height", "720")
                if (not preview.is_file() or preview.stat().st_size > MAX_PNG_BYTES
                        or preview.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n"):
                    raise ToolUserError("CAD-Vorschau ist ungültig")
            if cancel_event.is_set():
                raise ToolUserError("CAD-Auftrag gestoppt")
            manifest = {"model_id": str(model_id), "template": "g07-holder-v1",
                        "description": args.description.strip(), "dimensions_mm": {
                            "width": args.width_mm, "depth": args.depth_mm,
                            "thickness": args.thickness_mm,
                            "hole_diameter": args.hole_diameter_mm,
                            "hole_spacing": args.hole_spacing_mm}, "source_sha256": digest}
            (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                                       encoding="utf-8")
            return manifest | {"preview": True}
        except BaseException:
            # Only the just-created model directory may be removed.
            if (directory.exists() and not directory.is_symlink() and not directory.is_junction()
                    and directory.resolve().is_relative_to(self.models.resolve())
                    and directory.name == str(model_id)):
                shutil.rmtree(directory, ignore_errors=True)
            raise

    def export(self, args: HolderExportArgs, cancel_event: threading.Event) -> dict:
        directory = self.model_directory(args.model_id)
        script = directory / "holder.py"
        manifest_path = directory / "manifest.json"
        if (not script.is_file() or script.is_symlink() or not manifest_path.is_file()
                or manifest_path.is_symlink()):
            raise ToolUserError("CAD-Modell nicht gefunden")
        if script.stat().st_size > 20_000 or manifest_path.stat().st_size > 5_000:
            raise ToolUserError("CAD-Modell ist zu groß")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (manifest.get("model_id") != str(args.model_id)
                or manifest.get("source_sha256") != args.expected_sha256
                or sha256(script) != args.expected_sha256):
            raise ToolUserError("CAD-Modell wurde verändert; Export verweigert")
        dimensions = manifest.get("dimensions_mm", {})
        try:
            holder = HolderBuildArgs(
                description=manifest["description"], width_mm=dimensions["width"],
                depth_mm=dimensions["depth"], thickness_mm=dimensions["thickness"],
                hole_diameter_mm=dimensions["hole_diameter"],
                hole_spacing_mm=dimensions["hole_spacing"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ToolUserError("CAD-Modellmaße sind ungültig") from exc
        if manifest.get("template") != "g07-holder-v1" or script.read_text(encoding="utf-8") != render_holder(holder):
            raise ToolUserError("CAD-Skript entspricht nicht der geprüften Vorlage")
        destination = directory / f"holder.{args.format}"
        if destination.is_symlink():
            raise ToolUserError("Unsicherer CAD-Exportpfad")
        with CadJob(self.jobs, cancel_event) as job:
            output = job.run("run", str(script))
            match = re.search(r"\bid:\s*([A-Za-z0-9_-]+)", output)
            if not match:
                raise ToolUserError("CAD-Lauf lieferte keine Modell-ID")
            staged = job.root / f"holder.{args.format}"
            job.run("export", match.group(1), str(staged))
            if (not staged.is_file() or staged.stat().st_size > MAX_EXPORT_BYTES
                    or staged.stat().st_size < 100):
                raise ToolUserError("CAD-Export ist ungültig")
            job.run("run", str(VERIFIER), "--single", args.format, str(staged),
                    json.dumps(manifest["dimensions_mm"], separators=(",", ":")))
            if cancel_event.is_set():
                raise ToolUserError("CAD-Auftrag gestoppt")
            # A new export never overwrites an earlier approved file.
            staged_hash = sha256(staged)
            if destination.exists():
                if sha256(destination) != staged_hash:
                    raise ToolUserError("Bestehender CAD-Export weicht ab")
            else:
                if cancel_event.is_set():
                    raise ToolUserError("CAD-Auftrag gestoppt")
                os.replace(staged, destination)
        return {"model_id": str(args.model_id), "template": manifest["template"],
                "format": args.format,
                "source_sha256": args.expected_sha256, "file_sha256": sha256(destination),
                "size_bytes": destination.stat().st_size}


def register_cad_tools(engine: RunEngine, data_dir: Path) -> CadTools:
    cad = CadTools(data_dir)
    engine.register(ToolDefinition("cad_holder_build", HolderBuildArgs, cad.build,
                                   scope="local_cad_holder", effect="read", timeout_s=300,
                                   cancel_aware=True))
    engine.register(ToolDefinition("cad_holder_export", HolderExportArgs, cad.export,
                                   scope="local_cad_holder", effect="write", timeout_s=300,
                                   cancel_aware=True))
    return cad
