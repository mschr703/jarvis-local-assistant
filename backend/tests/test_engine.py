import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import BaseModel, ConfigDict

from jarvis.db import Database
from jarvis.engine import RunEngine, ToolDefinition, ToolUserError, canonical_hash


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


def engine_for(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    return db, RunEngine(db, tmp_path / "checkpoints.db")


def test_read_run_validates_and_deduplicates(tmp_path):
    db, engine = engine_for(tmp_path)
    calls = []
    engine.register(ToolDefinition("echo", EchoArgs, lambda args: calls.append(args.value) or
                                   {"value": args.value}, "test:read", "read"))
    with pytest.raises(ValueError, match="Ungültige"):
        engine.start("echo", {"value": "hi", "extra": "ignored"})
    first = engine.start("echo", {"value": "hi"}, "request-123")
    assert first["status"] == "succeeded"
    assert json.loads(first["result_json"]) == {"value": "hi"}
    assert engine.start("echo", {"value": "hi"}, "request-123")["id"] == first["id"]
    assert calls == ["hi"]
    with pytest.raises(ValueError, match="anderen Argumenten"):
        engine.start("echo", {"value": "bye"}, "request-123")
    assert db.list_runs()[0]["status"] == "succeeded"
    engine.close()


def test_only_explicitly_safe_tool_errors_reach_the_user(tmp_path):
    _, engine = engine_for(tmp_path)

    def safe_error(_args):
        raise ToolUserError("TLS-Zertifikat nicht verifizierbar")

    def raw_error(_args):
        raise ValueError("privates internes Detail")

    engine.register(ToolDefinition("safe_error", EchoArgs, safe_error, "test:read", "read"))
    engine.register(ToolDefinition("raw_error", EchoArgs, raw_error, "test:read", "read"))
    safe = engine.start("safe_error", {"value": "A"})
    raw = engine.start("raw_error", {"value": "B"})
    assert safe["status"] == "failed"
    assert safe["error"] == "TLS-Zertifikat nicht verifizierbar"
    assert raw["status"] == "failed"
    assert raw["error"] == "Werkzeugfehler: ValueError"
    engine.close()


def test_write_approval_bound_to_arguments_and_survives_restart(tmp_path):
    _db, engine = engine_for(tmp_path)
    calls = []
    tool = ToolDefinition("write_test", EchoArgs, lambda args: calls.append(args.value) or
                          {"saved": args.value}, "test:write", "write")
    engine.register(tool)
    run = engine.start("write_test", {"value": "A"})
    assert run["status"] == "waiting_approval"
    assert calls == []
    with pytest.raises(ValueError, match="passt nicht"):
        engine.decide(run["id"], canonical_hash("write_test", {"value": "B"}), True)
    engine.close()

    _, resumed = engine_for(tmp_path)
    resumed.register(tool)
    result = resumed.decide(run["id"], run["args_hash"], True)
    assert result["status"] == "succeeded"
    assert calls == ["A"]
    resumed.close()


def test_unknown_external_effect_not_replayed(tmp_path):
    db, engine = engine_for(tmp_path)
    calls = []
    engine.register(ToolDefinition("open_test", EchoArgs,
                                   lambda args: calls.append(args.value) or {"opened": args.value},
                                   "test:open", "local_open"))
    args = {"value": "A"}
    run = db.create_run("open_test", json.dumps(args), canonical_hash("open_test", args),
                        "local_open", "request-456")
    db.ledger_start(run["id"], "open_test", run["args_hash"], "local_open")
    engine.close()

    _, restarted = engine_for(tmp_path)
    restarted.register(ToolDefinition("open_test", EchoArgs,
                                      lambda args: calls.append(args.value) or {"opened": args.value},
                                      "test:open", "local_open"))
    assert restarted.start("open_test", args, "request-456")["status"] == "interrupted"
    assert calls == []
    restarted.close()


def test_running_read_can_be_stopped_without_publishing_result(tmp_path):
    db, engine = engine_for(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def slow_read(args):
        entered.set()
        assert release.wait(5)
        return {"value": args.value}

    engine.register(ToolDefinition("slow", EchoArgs, slow_read, "test:read", "read", 10))
    run = engine.enqueue("slow", {"value": "secret"})
    assert entered.wait(5)
    stopping = engine.cancel(run["id"])
    assert stopping["cancel_requested"] == 1
    release.set()
    deadline = time.monotonic() + 5
    while db.get_run(run["id"])["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    final = db.get_run(run["id"])
    assert final["status"] == "cancelled"
    assert final["result_json"] is None
    engine.close()


def test_cancel_aware_read_receives_stop_event(tmp_path):
    db, engine = engine_for(tmp_path)
    entered, stopped = threading.Event(), threading.Event()

    def cancellable(_args, cancel_event):
        entered.set()
        if cancel_event.wait(5):
            stopped.set()
        return {}

    engine.register(ToolDefinition("cancellable", EchoArgs, cancellable,
                                   "test:read", "read", 10, cancel_aware=True))
    run = engine.enqueue("cancellable", {"value": "public"})
    assert entered.wait(5)
    engine.cancel(run["id"])
    assert stopped.wait(5)
    deadline = time.monotonic() + 5
    while db.get_run(run["id"])["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert db.get_run(run["id"])["status"] == "cancelled"
    engine.close()


def test_cancel_between_gate_and_ledger_never_starts_tool(tmp_path, monkeypatch):
    db, engine = engine_for(tmp_path)
    calls = []
    engine.register(ToolDefinition("write_test", EchoArgs,
                                   lambda args: calls.append(args.value) or {"saved": True},
                                   "test:write", "write"))
    run = engine.start("write_test", {"value": "A"})
    original_ledger_start = db.ledger_start

    def cancel_before_ledger(run_id, tool_name, args_hash, effect):
        assert engine.cancel(run_id)["status"] == "cancelled"
        return original_ledger_start(run_id, tool_name, args_hash, effect)

    monkeypatch.setattr(db, "ledger_start", cancel_before_ledger)
    result = engine.decide(run["id"], run["args_hash"], True)
    assert result["status"] == "cancelled"
    assert calls == []
    with db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM tool_ledger").fetchone()[0] == 0
    engine.close()


def test_cancel_after_ledger_before_handler_never_starts_tool(tmp_path, monkeypatch):
    db, engine = engine_for(tmp_path)
    calls = []
    engine.register(ToolDefinition("write_test", EchoArgs,
                                   lambda args: calls.append(args.value) or {"saved": True},
                                   "test:write", "write"))
    run = engine.start("write_test", {"value": "A"})
    original_ledger_start = db.ledger_start

    def cancel_after_ledger(run_id, tool_name, args_hash, effect):
        ledger = original_ledger_start(run_id, tool_name, args_hash, effect)
        assert engine.cancel(run_id)["cancel_requested"] == 1
        return ledger

    monkeypatch.setattr(db, "ledger_start", cancel_after_ledger)
    result = engine.decide(run["id"], run["args_hash"], True)
    assert result["status"] == "cancelled"
    assert calls == []
    with db.connect() as connection:
        assert connection.execute("SELECT status FROM tool_ledger").fetchone()[0] == "cancelled"
    engine.close()


def test_timed_out_local_action_is_not_replayed(tmp_path):
    db, engine = engine_for(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_open(args):
        calls.append(args.value)
        entered.set()
        release.wait(5)
        return {"opened": True}

    engine.register(ToolDefinition("slow_open", EchoArgs, slow_open,
                                   "test:open", "local_open", timeout_s=1))
    run = engine.start("slow_open", {"value": "A"}, "timeout-request")
    assert entered.is_set() and run["status"] == "interrupted"
    release.set()
    assert engine.start("slow_open", {"value": "A"}, "timeout-request")["status"] == "interrupted"
    assert calls == ["A"]
    assert db.get_run(run["id"])["status"] == "interrupted"
    engine.close()


def test_timed_out_read_is_failed_without_unknown_side_effect(tmp_path):
    db, engine = engine_for(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def slow_read(_args):
        entered.set()
        release.wait(5)
        return {"text": "spät"}

    engine.register(ToolDefinition("slow_read", EchoArgs, slow_read,
                                   "test:read", "read", timeout_s=1))
    try:
        run = engine.start("slow_read", {"value": "A"})
        assert entered.is_set() and run["status"] == "failed"
        assert run["error"] == "Lesezeit überschritten"
        with db.connect() as connection:
            assert connection.execute("SELECT status FROM tool_ledger").fetchone()[0] == "failed"
    finally:
        release.set()
        engine.close()


def test_queued_run_is_visibly_interrupted_after_restart(tmp_path):
    db, engine = engine_for(tmp_path)
    args = {"value": "A"}
    run = db.create_run("read", json.dumps(args), canonical_hash("read", args), "read")
    assert run["status"] == "queued"
    engine.close()
    restarted = RunEngine(db, tmp_path / "checkpoints.db")
    assert db.get_run(run["id"])["status"] == "interrupted"
    restarted.close()


def test_parallel_same_request_id_creates_one_run(tmp_path):
    db = Database(tmp_path / "jarvis.db")
    args = {"value": "A"}
    digest = canonical_hash("read", args)
    with ThreadPoolExecutor(max_workers=8) as pool:
        runs = list(pool.map(lambda _: db.create_run("read", json.dumps(args), digest,
                                                     "read", "same-request-id"), range(8)))
    assert len({run["id"] for run in runs}) == 1
    assert len(db.list_runs()) == 1
