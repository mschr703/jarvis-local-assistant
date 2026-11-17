"""Read-only overview of user evaluations and their original evidence."""

from datetime import datetime

from jarvis.db import Database

KINDS = ("chat", "plan", "tool", "research")


def _duration(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    return max(0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds())


def overview(store: Database) -> dict:
    """Count all stored entries, then summarize only explicitly reviewed ones.

    A plan and its tool steps are separate entries. Model cost belongs only to a
    chat answer or plan with an unambiguous usage ID; research has no such ID.
    """
    totals = {}
    records = []
    with store.connect() as db:
        totals["chat"] = db.execute(
            "SELECT COUNT(*) FROM messages WHERE role='assistant'").fetchone()[0]
        totals["plan"] = db.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        totals["tool"] = db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        totals["research"] = db.execute("SELECT COUNT(*) FROM research_reports").fetchone()[0]
        queries = {
            "chat": """SELECT e.*,m.id AS evidence_id,m.chat_id AS parent_id,
                COALESCE((SELECT q.content FROM messages q WHERE q.chat_id=m.chat_id
                    AND q.role='user' AND q.created_at<=m.created_at
                    ORDER BY q.created_at DESC,q.id DESC LIMIT 1),m.content) AS label,
                m.status,m.provider,u.cost_usd,u.cost_kind,u.status AS usage_status,
                u.created_at AS started_at,u.updated_at AS finished_at
                FROM chat_answer_evaluations e JOIN messages m ON m.id=e.message_id
                LEFT JOIN model_usage u ON u.id=m.usage_id""",
            "plan": """SELECT e.*,p.id AS evidence_id,NULL AS parent_id,
                p.objective AS label,p.status,p.provider,u.cost_usd,u.cost_kind,
                u.status AS usage_status,u.created_at AS started_at,
                u.updated_at AS finished_at
                FROM plan_evaluations e JOIN plans p ON p.id=e.plan_id
                LEFT JOIN model_usage u ON u.id=p.usage_id""",
            "tool": """SELECT e.*,r.id AS evidence_id,NULL AS parent_id,
                r.tool_name AS label,r.status,NULL AS provider,NULL AS cost_usd,
                NULL AS cost_kind,NULL AS usage_status,l.started_at,l.finished_at
                FROM run_evaluations e JOIN runs r ON r.id=e.run_id
                LEFT JOIN tool_ledger l ON l.rowid=(SELECT rowid FROM tool_ledger
                    WHERE run_id=r.id ORDER BY started_at DESC,rowid DESC LIMIT 1)""",
            "research": """SELECT e.*,r.id AS evidence_id,NULL AS parent_id,
                r.question AS label,'available' AS status,r.provider,
                NULL AS cost_usd,NULL AS cost_kind,NULL AS usage_status,
                NULL AS started_at,NULL AS finished_at
                FROM research_evaluations e JOIN research_reports r ON r.id=e.report_id""",
        }
        for kind, query in queries.items():
            for row in db.execute(query):
                item = dict(row)
                item["kind"] = kind
                started_at, finished_at = item.pop("started_at"), item.pop("finished_at")
                item["duration_seconds"] = (_duration(started_at, finished_at)
                                            if kind == "tool" or item["usage_status"] == "complete"
                                            else None)
                item.pop("usage_status")
                item.pop("created_at")
                item.pop("version")
                item.pop("message_id", None)
                item.pop("plan_id", None)
                item.pop("run_id", None)
                item.pop("report_id", None)
                records.append(item)

    categories = {}
    for kind in KINDS:
        reviewed = [item for item in records if item["kind"] == kind]
        categories[kind] = {
            "total": totals[kind],
            "reviewed": len(reviewed),
            "unreviewed": totals[kind] - len(reviewed),
            "verdicts": {verdict: sum(item["verdict"] == verdict for item in reviewed)
                         for verdict in ("achieved", "partial", "failed", "unclear")},
            "unsupported_sources": sum(item.get("source_quality") == "unsupported"
                                       for item in reviewed),
            "regression_cases": sum(bool(item["regression_case"]) for item in reviewed),
            "measured_duration_seconds": round(sum(item["duration_seconds"] or 0
                                                    for item in reviewed), 3),
            "measured_duration_count": sum(item["duration_seconds"] is not None
                                           for item in reviewed),
            "reported_api_usd": round(sum(item["cost_usd"] or 0 for item in reviewed
                                          if item["provider"] == "openrouter"
                                          and item["cost_kind"] == "reported"), 8),
            "estimated_or_reserved_usd": round(sum(item["cost_usd"] or 0
                                                    for item in reviewed
                                                    if item["provider"] == "openrouter"
                                                    and item["cost_kind"] not in
                                                    {None, "reported", "not_sent"}), 8),
        }
    records.sort(key=lambda item: (item["updated_at"], item["evidence_id"]), reverse=True)
    return {"categories": categories, "recent": records[:20],
            "regression_cases": [item for item in records if item["regression_case"]][:20]}
