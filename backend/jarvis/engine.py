"""Persistente Werkzeugläufe mit Freigabe und Exactly-once-Schutz für lokale Aktionen."""

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypedDict

from pydantic import BaseModel, ValidationError

# Es werden ausschließlich JSON-fähige Zustände gespeichert. Deserialisierung
# beliebiger Msgpack-Pythonobjekte ist für lokale DB-Dateien nicht nötig.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from jarvis.db import Database

Effect = Literal["read", "local_open", "write"]


class ToolUserError(ValueError):
    """Werkzeugfehler mit bewusst unkritischem Text für die Oberfläche."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    schema: type[BaseModel]
    handler: Callable[..., dict[str, Any]]
    scope: str
    effect: Effect
    timeout_s: int = 15
    cancel_aware: bool = False


class ToolState(TypedDict, total=False):
    run_id: str
    tool_name: str
    args: dict[str, Any]
    args_hash: str
    effect: Effect
    status: str
    result: dict[str, Any]
    error: str


def canonical_hash(tool_name: str, args: dict[str, Any]) -> str:
    encoded = json.dumps({"tool": tool_name, "args": args}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class RunEngine:
    def __init__(self, db: Database, checkpoint_path: Path, *, reconcile: bool = True):
        self.db = db
        self.definitions: dict[str, ToolDefinition] = {}
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
        self.saver = SqliteSaver(self.connection)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-tool")
        self.run_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-run")
        self.drive_lock = threading.Lock()
        self.cancel_events: dict[str, threading.Event] = {}
        builder = StateGraph(ToolState)
        builder.add_node("gate", self._gate)
        builder.add_node("execute", self._execute)
        builder.add_edge(START, "gate")
        builder.add_conditional_edges("gate", lambda state: "execute" if state.get("status") == "running" else END)
        builder.add_edge("execute", END)
        self.graph = builder.compile(checkpointer=self.saver)
        if reconcile:
            self.db.reconcile_runs()

    def close(self) -> None:
        self.run_executor.shutdown(wait=True, cancel_futures=True)
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.connection.close()

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self.definitions:
            raise ValueError(f"Werkzeug {tool.name} ist schon registriert")
        if tool.timeout_s < 1 or tool.timeout_s > 300:
            raise ValueError("Werkzeug-Timeout muss 1–300 Sekunden betragen")
        self.definitions[tool.name] = tool

    def capabilities(self) -> list[dict]:
        return [{"name": item.name, "scope": item.scope, "effect": item.effect,
                 "schema": item.schema.model_json_schema()}
                for item in self.definitions.values()]

    def start(self, tool_name: str, args: dict[str, Any],
              client_request_id: str | None = None) -> dict:
        run, state = self._prepare(tool_name, args, client_request_id)
        if run["status"] != "queued":
            return run
        return self._drive(run["id"], state)

    def enqueue(self, tool_name: str, args: dict[str, Any],
                client_request_id: str | None = None) -> dict:
        run, state = self._prepare(tool_name, args, client_request_id)
        if run["status"] == "queued":
            self.run_executor.submit(self._drive, run["id"], state)
        return run

    def _prepare(self, tool_name: str, args: dict[str, Any],
                 client_request_id: str | None) -> tuple[dict, ToolState]:
        tool = self.definitions.get(tool_name)
        if tool is None:
            raise ValueError("Unbekanntes Werkzeug")
        try:
            parsed = tool.schema.model_validate(args)
        except ValidationError as exc:
            raise ValueError("Ungültige Werkzeugargumente") from exc
        clean_args = parsed.model_dump(mode="json")
        args_json = json.dumps(clean_args, sort_keys=True, ensure_ascii=False)
        args_hash = canonical_hash(tool_name, clean_args)
        run = self.db.create_run(tool_name, args_json, args_hash, tool.effect, client_request_id)
        state: ToolState = {"run_id": run["id"], "tool_name": tool_name, "args": clean_args,
                            "args_hash": args_hash, "effect": tool.effect}
        return run, state

    def cancel(self, run_id: str) -> dict:
        run = self.db.request_cancel(run_id)
        if run["cancel_requested"]:
            self.cancel_events.setdefault(run_id, threading.Event()).set()
        return run

    def decide(self, run_id: str, args_hash: str, approved: bool) -> dict:
        with self.drive_lock:
            run = self.db.get_run(run_id)
            if run is None:
                raise ValueError("Auftrag nicht gefunden")
            self.db.set_approval(run_id, args_hash, "approved" if approved else "denied")
            return self._drive_serial(run_id, Command(resume={"approved": approved,
                                                            "args_hash": args_hash}))

    def _drive(self, run_id: str, command: ToolState | Command) -> dict:
        with self.drive_lock:
            return self._drive_serial(run_id, command)

    def _drive_serial(self, run_id: str, command: ToolState | Command) -> dict:
        if self.db.get_run(run_id)["status"] == "cancelled":
            return self.db.get_run(run_id)
        try:
            result = self.graph.invoke(command, config={"configurable": {"thread_id": run_id}})
            if "__interrupt__" in result:
                self.db.set_run(run_id, "waiting_approval")
                return self.db.get_run(run_id)
            status = result.get("status", "failed")
            self.db.set_run(run_id, status, json.dumps(result.get("result"), ensure_ascii=False)
                            if result.get("result") is not None else None, result.get("error"))
            self.db.log("run_" + status, f"Werkzeuglauf {run_id}: {result['tool_name']}")
        except Exception as exc:  # noqa: BLE001 - Status muss nach jedem Graphfehler persistiert werden.
            self.db.set_run(run_id, "failed", error=f"Laufzeitfehler: {type(exc).__name__}")
            self.db.log("run_failed", f"Werkzeuglauf {run_id}")
        finally:
            self.cancel_events.pop(run_id, None)
        return self.db.get_run(run_id)

    def _gate(self, state: ToolState) -> ToolState:
        if self.db.get_run(state["run_id"])["status"] == "cancelled":
            return {"status": "cancelled", "error": "Auftrag gestoppt"}
        if state["effect"] == "write":
            decision = interrupt({"tool": state["tool_name"], "args": state["args"],
                                  "args_hash": state["args_hash"]})
            approval = self.db.get_approval(state["run_id"])
            if (not isinstance(decision, dict) or not decision.get("approved")
                    or decision.get("args_hash") != state["args_hash"]
                    or not approval or approval["decision"] != "approved"
                    or approval["args_hash"] != state["args_hash"]):
                return {"status": "cancelled", "error": "Aktion nicht freigegeben"}
        return {"status": "running"}

    def _execute(self, state: ToolState) -> ToolState:
        tool = self.definitions[state["tool_name"]]
        cancel_event = self.cancel_events.setdefault(state["run_id"], threading.Event())
        if cancel_event.is_set() or self.db.get_run(state["run_id"])["status"] == "cancelled":
            return {"status": "cancelled", "error": "Auftrag vor Ausführung gestoppt"}
        args_hash = canonical_hash(tool.name, state["args"])
        if args_hash != state["args_hash"]:
            return {"status": "failed", "error": "Argumente wurden verändert"}
        ledger = self.db.ledger_start(state["run_id"], tool.name, args_hash, tool.effect)
        if ledger.get("cancelled"):
            return {"status": "cancelled", "error": "Auftrag vor Ausführung gestoppt"}
        if not ledger.get("new"):
            if ledger["args_hash"] != args_hash:
                return {"status": "failed", "error": "Idempotenzkonflikt"}
            if ledger["status"] == "succeeded":
                return {"status": "succeeded", "result": json.loads(ledger["result_json"])}
            return {"status": "interrupted", "error": "Werkzeugwirkung unklar; nicht erneut ausgeführt"}
        try:
            latest = self.db.get_run(state["run_id"])
            if cancel_event.is_set() or latest["cancel_requested"] or latest["status"] == "cancelled":
                self.db.ledger_finish(ledger["idempotency_key"], "cancelled", None,
                                      "Auftrag vor Ausführung gestoppt")
                return {"status": "cancelled", "error": "Auftrag vor Ausführung gestoppt"}
            parsed = tool.schema.model_validate(state["args"])
            future = self.executor.submit(tool.handler, parsed, cancel_event) if tool.cancel_aware else (
                self.executor.submit(tool.handler, parsed))
            result = future.result(timeout=tool.timeout_s)
            if not isinstance(result, dict):
                raise TypeError("Werkzeug muss ein Ergebnisobjekt liefern")
            if cancel_event.is_set() and tool.effect == "read":
                self.db.ledger_finish(ledger["idempotency_key"], "cancelled", None,
                                      "Leseergebnis nach Stopp verworfen")
                return {"status": "cancelled", "error": "Auftrag gestoppt"}
            encoded = json.dumps(result, ensure_ascii=False)
            self.db.ledger_finish(ledger["idempotency_key"], "succeeded", encoded)
            return {"status": "succeeded", "result": result}
        except FutureTimeout:
            if tool.effect == "read":
                future.cancel()
                self.db.ledger_finish(ledger["idempotency_key"], "failed", None,
                                      "Lesezeit überschritten")
                return {"status": "failed", "error": "Lesezeit überschritten"}
            self.db.ledger_finish(ledger["idempotency_key"], "unknown", None,
                                  "Timeout; Wirkung möglicherweise erfolgt")
            return {"status": "interrupted", "error": "Timeout; Werkzeugwirkung unklar"}
        except Exception as exc:  # noqa: BLE001 - Werkzeugfehler bleiben auf den Lauf begrenzt.
            error = str(exc)[:240] if isinstance(exc, ToolUserError) else f"Werkzeugfehler: {type(exc).__name__}"
            self.db.ledger_finish(ledger["idempotency_key"], "failed", None, error)
            return {"status": "failed", "error": error}
