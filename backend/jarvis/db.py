"""SQLite-Speicher mit expliziter Schema-Version und kurzen Transaktionen."""

import json
import math
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from jarvis.semantic_cache import PersistentEmbeddingCache

SEARCH_SOURCE_CHANGED = {
    "folder_indexed", "folder_removed", "folder_policy_changed",
    "document_excluded", "document_included", "memory_updated", "memory_deleted",
    "memory_correct", "memory_delete",
    "work_state_updated", "work_state_deleted",
}


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            def apply_migration(sql: str) -> None:
                db.executescript("BEGIN IMMEDIATE;\n" + sql + "\nCOMMIT;")

            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 62:
                raise RuntimeError(f"Datenbank-Schema {version} ist neuer als diese Jarvis-Version")
            if version == 0:
                apply_migration("""
                    CREATE TABLE chats (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE messages (
                        id TEXT PRIMARY KEY, chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                        role TEXT NOT NULL CHECK(role IN ('user','assistant')),
                        content TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        provider TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER,
                        cost_usd REAL, cost_kind TEXT
                    );
                    CREATE INDEX messages_chat_time ON messages(chat_id, created_at);
                    CREATE TABLE activity (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, detail TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    PRAGMA user_version=1;
                """)
            if version < 2:
                apply_migration("""
                    CREATE TABLE app_settings (
                        key TEXT PRIMARY KEY, value TEXT NOT NULL
                    );
                    PRAGMA user_version=2;
                """)
            if version < 3:
                apply_migration("""
                    CREATE TABLE runs (
                        id TEXT PRIMARY KEY, client_request_id TEXT UNIQUE,
                        tool_name TEXT NOT NULL, args_json TEXT NOT NULL, args_hash TEXT NOT NULL,
                        effect TEXT NOT NULL, status TEXT NOT NULL,
                        result_json TEXT, error TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        lease_owner TEXT, lease_until TEXT
                    );
                    CREATE TABLE approvals (
                        run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                        args_hash TEXT NOT NULL, decision TEXT NOT NULL,
                        decided_at TEXT NOT NULL
                    );
                    CREATE TABLE tool_ledger (
                        idempotency_key TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                        tool_name TEXT NOT NULL, args_hash TEXT NOT NULL,
                        effect TEXT NOT NULL, status TEXT NOT NULL,
                        result_json TEXT, error TEXT, started_at TEXT NOT NULL,
                        finished_at TEXT
                    );
                    CREATE INDEX runs_status_time ON runs(status,updated_at);
                    PRAGMA user_version=3;
                """)
            if version < 4:
                apply_migration("""
                    CREATE TABLE folders (
                        id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                        policy TEXT NOT NULL CHECK(policy IN ('local_only','cloud_allowed')),
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE documents (
                        id TEXT PRIMARY KEY, folder_id TEXT NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
                        path TEXT NOT NULL UNIQUE, sha256 TEXT, mtime_ns INTEGER, size INTEGER,
                        policy TEXT NOT NULL, status TEXT NOT NULL, excluded INTEGER NOT NULL DEFAULT 0,
                        indexed_at TEXT, error TEXT
                    );
                    CREATE TABLE passages (
                        id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        page INTEGER, ordinal INTEGER NOT NULL, text TEXT NOT NULL
                    );
                    CREATE VIRTUAL TABLE passages_fts USING fts5(text, content='passages', content_rowid='id');
                    CREATE TRIGGER passages_ai AFTER INSERT ON passages BEGIN
                        INSERT INTO passages_fts(rowid,text) VALUES(new.id,new.text);
                    END;
                    CREATE TRIGGER passages_ad AFTER DELETE ON passages BEGIN
                        INSERT INTO passages_fts(passages_fts,rowid,text) VALUES('delete',old.id,old.text);
                    END;
                    CREATE TRIGGER passages_au AFTER UPDATE ON passages BEGIN
                        INSERT INTO passages_fts(passages_fts,rowid,text) VALUES('delete',old.id,old.text);
                        INSERT INTO passages_fts(rowid,text) VALUES(new.id,new.text);
                    END;
                    CREATE TABLE memories (
                        id TEXT PRIMARY KEY, text TEXT NOT NULL, source_ref TEXT NOT NULL,
                        policy TEXT NOT NULL CHECK(policy IN ('local_only','cloud_allowed')),
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE VIRTUAL TABLE memories_fts USING fts5(id UNINDEXED,text);
                    PRAGMA user_version=4;
                """)
            if version < 5:
                apply_migration("""
                    ALTER TABLE messages ADD COLUMN citations_json TEXT;
                    CREATE TABLE model_usage (
                        id TEXT PRIMARY KEY, purpose TEXT NOT NULL, provider TEXT NOT NULL,
                        model TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                        cost_usd REAL NOT NULL, cost_kind TEXT NOT NULL, status TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    INSERT INTO model_usage(id,purpose,provider,model,input_tokens,output_tokens,
                        cost_usd,cost_kind,status,created_at,updated_at)
                    SELECT id,'chat',provider,model,input_tokens,output_tokens,
                        COALESCE(cost_usd,0),COALESCE(cost_kind,'unknown'),status,created_at,created_at
                    FROM messages WHERE role='assistant' AND provider IS NOT NULL;
                    PRAGMA user_version=5;
                """)
            if version < 6:
                apply_migration("""
                    CREATE TABLE style_samples (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                        policy TEXT NOT NULL CHECK(policy IN ('local_only','cloud_allowed')),
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE style_profile (
                        id INTEGER PRIMARY KEY CHECK(id=1), description TEXT NOT NULL,
                        policy TEXT NOT NULL CHECK(policy IN ('local_only','cloud_allowed')),
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE drafts (
                        id TEXT PRIMARY KEY, kind TEXT NOT NULL, input_text TEXT NOT NULL,
                        brief TEXT NOT NULL, content TEXT NOT NULL, sources_json TEXT NOT NULL,
                        uncertainty TEXT NOT NULL, style_snapshot TEXT NOT NULL,
                        status TEXT NOT NULL, version INTEGER NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE draft_exports (
                        draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
                        format TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL, PRIMARY KEY(draft_id,format)
                    );
                    PRAGMA user_version=6;
                """)
            if version < 7:
                apply_migration("""
                    CREATE TABLE tasks (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('open','done')),
                        due_utc TEXT, due_local TEXT, timezone TEXT,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE reminder_jobs (
                        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        due_utc TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending','fired','cancelled')),
                        created_at TEXT NOT NULL, fired_at TEXT
                    );
                    CREATE INDEX reminder_jobs_due ON reminder_jobs(status,due_utc);
                    CREATE TABLE notifications (
                        id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES reminder_jobs(id) ON DELETE CASCADE,
                        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        title TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT
                    );
                    PRAGMA user_version=7;
                """)
            if version < 8:
                apply_migration("""
                    CREATE TABLE worker_lease (
                        id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT NOT NULL,
                        expires_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    PRAGMA user_version=8;
                """)
            if version < 9:
                apply_migration("""
                    ALTER TABLE runs ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0;
                    PRAGMA user_version=9;
                """)
            if version < 10:
                apply_migration("""
                    CREATE TABLE plans (
                        id TEXT PRIMARY KEY, objective TEXT NOT NULL, steps_json TEXT NOT NULL,
                        status TEXT NOT NULL, current_index INTEGER NOT NULL DEFAULT 0,
                        current_run_id TEXT, error TEXT,
                        provider TEXT NOT NULL, model TEXT NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX plans_status_time ON plans(status,updated_at);
                    PRAGMA user_version=10;
                """)
            if version < 11:
                apply_migration("""
                    ALTER TABLE plans ADD COLUMN active_seconds REAL NOT NULL DEFAULT 0;
                    ALTER TABLE plans ADD COLUMN active_since TEXT;
                    UPDATE plans SET active_since=updated_at WHERE status='running';
                    PRAGMA user_version=11;
                """)
            if version < 12:
                apply_migration("""
                    CREATE TABLE markdown_revisions (
                        id TEXT PRIMARY KEY,
                        document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        before_sha256 TEXT NOT NULL,
                        after_sha256 TEXT NOT NULL,
                        before_bytes BLOB NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('prepared','applied','undone','aborted','unknown')),
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX markdown_revisions_document_time
                    ON markdown_revisions(document_id,created_at);
                    PRAGMA user_version=12;
                """)
            if version < 13:
                apply_migration("""
                    CREATE TABLE work_states (
                        id TEXT PRIMARY KEY, project TEXT NOT NULL,
                        confirmed TEXT NOT NULL, open_questions TEXT NOT NULL,
                        next_step TEXT NOT NULL, documents_json TEXT NOT NULL,
                        tasks_json TEXT NOT NULL, links_json TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX work_states_updated ON work_states(updated_at);
                    PRAGMA user_version=13;
                """)
            if version < 14:
                apply_migration("""
                    CREATE TABLE time_rules (
                        id TEXT PRIMARY KEY, title TEXT NOT NULL, kind TEXT NOT NULL,
                        weekdays_json TEXT NOT NULL, start_time TEXT NOT NULL,
                        end_time TEXT NOT NULL, timezone TEXT NOT NULL,
                        buffer_before INTEGER NOT NULL, buffer_after INTEGER NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE time_exceptions (
                        id TEXT PRIMARY KEY, date_local TEXT NOT NULL,
                        mode TEXT NOT NULL CHECK(mode IN ('skip','block')),
                        rule_id TEXT REFERENCES time_rules(id) ON DELETE CASCADE,
                        title TEXT, kind TEXT, start_time TEXT, end_time TEXT,
                        timezone TEXT, buffer_before INTEGER NOT NULL DEFAULT 0,
                        buffer_after INTEGER NOT NULL DEFAULT 0,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX time_exception_skip_unique
                    ON time_exceptions(rule_id,date_local) WHERE mode='skip';
                    CREATE INDEX time_exception_date ON time_exceptions(date_local);
                    PRAGMA user_version=14;
                """)
            if version < 15:
                apply_migration("""
                    CREATE TABLE evening_plans (
                        id TEXT PRIMARY KEY, input_json TEXT NOT NULL,
                        result_json TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX evening_plans_updated ON evening_plans(updated_at);
                    PRAGMA user_version=15;
                """)
            if version < 16:
                apply_migration("""
                    ALTER TABLE tasks ADD COLUMN priority INTEGER NOT NULL DEFAULT 3
                        CHECK(priority BETWEEN 1 AND 5);
                    ALTER TABLE notifications ADD COLUMN acknowledged_at TEXT;
                    ALTER TABLE notifications ADD COLUMN snoozed_until TEXT;
                    ALTER TABLE notifications ADD COLUMN followup_due_utc TEXT;
                    ALTER TABLE notifications ADD COLUMN followup_count INTEGER NOT NULL DEFAULT 0;
                    ALTER TABLE notifications ADD COLUMN last_delivered_at TEXT;
                    CREATE INDEX notifications_followup_due
                    ON notifications(followup_due_utc) WHERE acknowledged_at IS NULL;
                    CREATE INDEX notifications_snoozed_due
                    ON notifications(snoozed_until) WHERE acknowledged_at IS NULL;
                    PRAGMA user_version=16;
                """)
            if version < 17:
                apply_migration("""
                    ALTER TABLE tasks ADD COLUMN source_url TEXT;
                    CREATE TABLE extension_pairs (
                        extension_id TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE extension_captures (
                        extension_id TEXT NOT NULL, request_id TEXT NOT NULL,
                        content_sha256 TEXT NOT NULL, task_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(extension_id,request_id)
                    );
                    PRAGMA user_version=17;
                """)
            if version < 18:
                apply_migration("""
                    CREATE TABLE focus_settings (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        manual_enabled INTEGER NOT NULL DEFAULT 0 CHECK(manual_enabled IN (0,1)),
                        schedule_enabled INTEGER NOT NULL DEFAULT 0 CHECK(schedule_enabled IN (0,1)),
                        start_time TEXT NOT NULL DEFAULT '09:00',
                        end_time TEXT NOT NULL DEFAULT '17:00',
                        timezone TEXT NOT NULL DEFAULT 'Europe/Paris',
                        version INTEGER NOT NULL DEFAULT 1,
                        updated_at TEXT NOT NULL
                    );
                    INSERT INTO focus_settings(id,updated_at)
                    VALUES(1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
                    ALTER TABLE notifications ADD COLUMN focus_deferred_at TEXT;
                    CREATE INDEX notifications_focus_deferred
                    ON notifications(focus_deferred_at) WHERE read_at IS NULL;
                    PRAGMA user_version=18;
                """)
            if version < 19:
                apply_migration("""
                    CREATE TABLE research_reports (
                        id TEXT PRIMARY KEY, question TEXT NOT NULL, search_query TEXT NOT NULL,
                        search_provider TEXT NOT NULL,
                        sources_json TEXT NOT NULL, claims_json TEXT NOT NULL,
                        interpretation TEXT NOT NULL, uncertainty TEXT NOT NULL,
                        errors_json TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX research_reports_created ON research_reports(created_at);
                    PRAGMA user_version=19;
                """)
            if version < 20:
                apply_migration("""
                    CREATE TABLE project_actions (
                        state_id TEXT NOT NULL REFERENCES work_states(id) ON DELETE CASCADE,
                        step_hash TEXT NOT NULL, task_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(state_id,step_hash)
                    );
                    PRAGMA user_version=20;
                """)
            if version < 21:
                apply_migration("""
                    CREATE TABLE project_review_schedules (
                        state_id TEXT PRIMARY KEY REFERENCES work_states(id) ON DELETE CASCADE,
                        interval_days INTEGER NOT NULL CHECK(interval_days BETWEEN 1 AND 30),
                        local_time TEXT NOT NULL, timezone TEXT NOT NULL,
                        next_local_date TEXT NOT NULL,
                        paused INTEGER NOT NULL CHECK(paused IN (0,1)),
                        last_status TEXT, last_error TEXT, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE project_reviews (
                        id TEXT PRIMARY KEY,
                        state_id TEXT NOT NULL REFERENCES work_states(id) ON DELETE CASCADE,
                        local_date TEXT NOT NULL, kind TEXT NOT NULL
                            CHECK(kind IN ('scheduled','manual')),
                        status TEXT NOT NULL CHECK(status IN ('running','succeeded','failed')),
                        summary_json TEXT, fingerprint TEXT,
                        changed INTEGER, error TEXT, created_at TEXT NOT NULL
                    );
                    CREATE INDEX project_reviews_state_time
                        ON project_reviews(state_id,created_at);
                    CREATE UNIQUE INDEX project_reviews_scheduled_day
                        ON project_reviews(state_id,local_date) WHERE kind='scheduled';
                    PRAGMA user_version=21;
                """)
            if version < 22:
                apply_migration("""
                    CREATE TABLE run_evaluations (
                        run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                        verdict TEXT NOT NULL CHECK(verdict IN ('achieved','partial','failed','unclear')),
                        reason TEXT NOT NULL,
                        regression_case INTEGER NOT NULL CHECK(regression_case IN (0,1)),
                        expected_result TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX run_evaluations_cases
                        ON run_evaluations(regression_case,updated_at);
                    PRAGMA user_version=22;
                """)
            if version < 23:
                apply_migration("""
                    CREATE TABLE chat_rule_versions (
                        version INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE chat_rule_state (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        active_version INTEGER NOT NULL REFERENCES chat_rule_versions(version),
                        revision INTEGER NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE chat_rule_activations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        from_version INTEGER NOT NULL REFERENCES chat_rule_versions(version),
                        to_version INTEGER NOT NULL REFERENCES chat_rule_versions(version),
                        changed_at TEXT NOT NULL
                    );
                    INSERT INTO chat_rule_versions(version,content,created_at)
                    VALUES(1,'',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
                    INSERT INTO chat_rule_state(id,active_version,revision,updated_at)
                    VALUES(1,1,1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
                    ALTER TABLE messages ADD COLUMN rule_version INTEGER
                        REFERENCES chat_rule_versions(version);
                    PRAGMA user_version=23;
                """)
            if version < 24:
                apply_migration("""
                    ALTER TABLE messages ADD COLUMN usage_id TEXT REFERENCES model_usage(id);
                    CREATE UNIQUE INDEX messages_usage_id ON messages(usage_id)
                        WHERE usage_id IS NOT NULL;
                    CREATE TABLE chat_answer_evaluations (
                        message_id TEXT PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
                        verdict TEXT NOT NULL CHECK(verdict IN ('achieved','partial','failed','unclear')),
                        source_quality TEXT NOT NULL CHECK(source_quality IN
                            ('supported','unsupported','not_applicable','unclear')),
                        reason TEXT NOT NULL,
                        regression_case INTEGER NOT NULL CHECK(regression_case IN (0,1)),
                        expected_result TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX chat_answer_evaluations_cases
                        ON chat_answer_evaluations(regression_case,updated_at);
                    PRAGMA user_version=24;
                """)
            if version < 25:
                apply_migration("""
                    CREATE TABLE draft_rule_versions (
                        version INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE draft_rule_state (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        active_version INTEGER NOT NULL REFERENCES draft_rule_versions(version),
                        revision INTEGER NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE draft_rule_activations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        from_version INTEGER NOT NULL REFERENCES draft_rule_versions(version),
                        to_version INTEGER NOT NULL REFERENCES draft_rule_versions(version),
                        changed_at TEXT NOT NULL
                    );
                    INSERT INTO draft_rule_versions(version,content,created_at)
                    VALUES(1,'',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
                    INSERT INTO draft_rule_state(id,active_version,revision,updated_at)
                    VALUES(1,1,1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
                    ALTER TABLE drafts ADD COLUMN rule_version INTEGER
                        REFERENCES draft_rule_versions(version);
                    PRAGMA user_version=25;
                """)
            if version < 26:
                apply_migration("""
                    CREATE TABLE plan_rule_versions (
                        version INTEGER PRIMARY KEY AUTOINCREMENT,
                        content TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE plan_rule_state (
                        id INTEGER PRIMARY KEY CHECK(id=1),
                        active_version INTEGER NOT NULL REFERENCES plan_rule_versions(version),
                        revision INTEGER NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE plan_rule_activations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        from_version INTEGER NOT NULL REFERENCES plan_rule_versions(version),
                        to_version INTEGER NOT NULL REFERENCES plan_rule_versions(version),
                        changed_at TEXT NOT NULL
                    );
                    INSERT INTO plan_rule_versions(version,content,created_at)
                    VALUES(1,'',strftime('%Y-%m-%dT%H:%M:%SZ','now'));
                    INSERT INTO plan_rule_state(id,active_version,revision,updated_at)
                    VALUES(1,1,1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
                    ALTER TABLE plans ADD COLUMN rule_version INTEGER
                        REFERENCES plan_rule_versions(version);
                    PRAGMA user_version=26;
                """)
            if version < 27:
                apply_migration("""
                    ALTER TABLE plans ADD COLUMN usage_id TEXT REFERENCES model_usage(id);
                    CREATE UNIQUE INDEX plans_usage_id ON plans(usage_id)
                        WHERE usage_id IS NOT NULL;
                    CREATE TABLE plan_evaluations (
                        plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
                        verdict TEXT NOT NULL CHECK(verdict IN
                            ('achieved','partial','failed','unclear')),
                        source_quality TEXT NOT NULL CHECK(source_quality IN
                            ('supported','unsupported','not_applicable','unclear')),
                        reason TEXT NOT NULL,
                        regression_case INTEGER NOT NULL CHECK(regression_case IN (0,1)),
                        expected_result TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX plan_evaluations_cases
                        ON plan_evaluations(regression_case,updated_at);
                    PRAGMA user_version=27;
                """)
            if version < 28:
                apply_migration("""
                    CREATE TABLE skills (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
                        active_version INTEGER, revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE skill_versions (
                        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                        version INTEGER NOT NULL, content TEXT NOT NULL,
                        content_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                        PRIMARY KEY(skill_id,version)
                    );
                    CREATE TABLE skill_activations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
                        from_version INTEGER, to_version INTEGER,
                        changed_at TEXT NOT NULL
                    );
                    ALTER TABLE messages ADD COLUMN skill_id TEXT;
                    ALTER TABLE messages ADD COLUMN skill_version INTEGER;
                    ALTER TABLE messages ADD COLUMN skill_hash TEXT;
                    PRAGMA user_version=28;
                """)
            if version < 29:
                apply_migration("""
                    ALTER TABLE research_reports ADD COLUMN project_context_json TEXT;
                    PRAGMA user_version=29;
                """)
            if version < 30:
                apply_migration("""
                    CREATE TABLE research_evaluations (
                        report_id TEXT PRIMARY KEY REFERENCES research_reports(id) ON DELETE CASCADE,
                        verdict TEXT NOT NULL CHECK(verdict IN
                            ('achieved','partial','failed','unclear')),
                        source_quality TEXT NOT NULL CHECK(source_quality IN
                            ('supported','unsupported','not_applicable','unclear')),
                        reason TEXT NOT NULL,
                        regression_case INTEGER NOT NULL CHECK(regression_case IN (0,1)),
                        expected_result TEXT NOT NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE INDEX research_evaluations_cases
                        ON research_evaluations(regression_case,updated_at);
                    PRAGMA user_version=30;
                """)
            if version < 31:
                apply_migration("""
                    ALTER TABLE research_reports ADD COLUMN parent_report_id TEXT
                        REFERENCES research_reports(id);
                    ALTER TABLE research_reports ADD COLUMN open_point TEXT;
                    CREATE INDEX research_reports_parent ON research_reports(parent_report_id);
                    PRAGMA user_version=31;
                """)
            if version < 32:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS essay_metadata (
                        draft_id TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
                        word_target INTEGER NOT NULL CHECK(word_target BETWEEN 300 AND 900),
                        outline_json TEXT NOT NULL
                    );
                    PRAGMA user_version=32;
                """)
            if version < 33:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS essay_feedback_usage (
                        draft_id TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
                        snapshot_json TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS essay_style_feedback (
                        id TEXT PRIMARY KEY,
                        draft_id TEXT NOT NULL REFERENCES drafts(id) ON DELETE CASCADE,
                        draft_version INTEGER NOT NULL,
                        note TEXT NOT NULL,
                        normalized_note TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS essay_style_feedback_newest
                        ON essay_style_feedback(created_at DESC, id DESC);
                    PRAGMA user_version=33;
                """)
            if version < 34:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS style_sample_origins (
                        sample_id TEXT PRIMARY KEY REFERENCES style_samples(id) ON DELETE CASCADE,
                        document_id TEXT NOT NULL,
                        source_path TEXT NOT NULL,
                        source_sha256 TEXT NOT NULL,
                        imported_at TEXT NOT NULL,
                        UNIQUE(document_id,source_sha256)
                    );
                    PRAGMA user_version=34;
                """)
            if version < 35:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS typed_style_profiles (
                        kind TEXT PRIMARY KEY CHECK(kind IN ('email','text','answer','essay')),
                        description TEXT NOT NULL,
                        policy TEXT NOT NULL CHECK(policy IN ('local_only','cloud_allowed')),
                        updated_at TEXT NOT NULL
                    );
                    PRAGMA user_version=35;
                """)
            if version < 36:
                apply_migration("""
                    CREATE TABLE essay_metadata_v36 (
                        draft_id TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
                        word_target INTEGER NOT NULL CHECK(word_target BETWEEN 300 AND 1200),
                        outline_json TEXT NOT NULL
                    );
                    INSERT INTO essay_metadata_v36(draft_id,word_target,outline_json)
                        SELECT draft_id,word_target,outline_json FROM essay_metadata;
                    DROP TABLE essay_metadata;
                    ALTER TABLE essay_metadata_v36 RENAME TO essay_metadata;
                    PRAGMA user_version=36;
                """)
            if version < 37:
                apply_migration("""
                    CREATE TABLE style_profile_revisions (
                        scope TEXT NOT NULL CHECK(scope IN
                            ('general','email','text','answer','essay')),
                        revision INTEGER NOT NULL CHECK(revision >= 1),
                        description TEXT NOT NULL,
                        policy TEXT NOT NULL CHECK(policy IN ('local_only','cloud_allowed')),
                        restored_from INTEGER,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(scope,revision)
                    );
                    INSERT INTO style_profile_revisions
                        (scope,revision,description,policy,created_at)
                        SELECT 'general',1,description,policy,updated_at FROM style_profile;
                    INSERT INTO style_profile_revisions
                        (scope,revision,description,policy,created_at)
                        SELECT kind,1,description,policy,updated_at FROM typed_style_profiles;
                    PRAGMA user_version=37;
                """)
            if version < 38:
                apply_migration("""
                    ALTER TABLE chats ADD COLUMN archived_at TEXT;
                    CREATE INDEX chats_archive_updated ON chats(archived_at,updated_at DESC);
                    PRAGMA user_version=38;
                """)
            if version < 39:
                apply_migration("""
                    CREATE TABLE chat_compactions (
                        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                        version INTEGER NOT NULL CHECK(version >= 1),
                        covered_message_ids_json TEXT NOT NULL,
                        entries_json TEXT NOT NULL,
                        provider TEXT NOT NULL CHECK(provider='ollama'),
                        model TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(chat_id,version)
                    );
                    PRAGMA user_version=39;
                """)
            if version < 40:
                apply_migration("""
                    ALTER TABLE messages ADD COLUMN compaction_version INTEGER;
                    ALTER TABLE chat_compactions ADD COLUMN created_by TEXT NOT NULL
                        DEFAULT 'ollama' CHECK(created_by IN
                            ('ollama','user_remove','user_reset'));
                    PRAGMA user_version=40;
                """)
            if version < 41:
                apply_migration("""
                    ALTER TABLE messages ADD COLUMN context_estimated_tokens INTEGER;
                    ALTER TABLE messages ADD COLUMN context_token_budget INTEGER;
                    ALTER TABLE messages ADD COLUMN context_omitted_count INTEGER;
                    ALTER TABLE messages ADD COLUMN context_latest_omitted_id TEXT;
                    PRAGMA user_version=41;
                """)
            if version < 42:
                apply_migration("""
                    CREATE TABLE project_people (
                        id TEXT PRIMARY KEY,
                        state_id TEXT NOT NULL REFERENCES work_states(id) ON DELETE CASCADE,
                        name TEXT NOT NULL,
                        relation_type TEXT NOT NULL CHECK(relation_type IN
                            ('lead','contributor','contact','reviewer','other')),
                        source_note TEXT NOT NULL,
                        source_document_id TEXT,
                        source_path TEXT,
                        source_sha256 TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX project_people_state ON project_people(state_id,created_at,id);
                    PRAGMA user_version=42;
                """)
            if version < 43:
                apply_migration("""
                    CREATE TABLE project_person_tasks (
                        id TEXT PRIMARY KEY,
                        state_id TEXT NOT NULL REFERENCES work_states(id) ON DELETE CASCADE,
                        person_id TEXT NOT NULL REFERENCES project_people(id) ON DELETE CASCADE,
                        task_id TEXT NOT NULL,
                        task_title TEXT NOT NULL,
                        relation_type TEXT NOT NULL CHECK(relation_type IN
                            ('responsible','involved','reviewer')),
                        source_note TEXT NOT NULL,
                        source_document_id TEXT,
                        source_path TEXT,
                        source_sha256 TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(state_id,person_id,task_id,relation_type)
                    );
                    CREATE INDEX project_person_tasks_state
                        ON project_person_tasks(state_id,created_at,id);
                    PRAGMA user_version=43;
                """)
            if version < 44:
                apply_migration("""
                    ALTER TABLE project_people ADD COLUMN confidence TEXT NOT NULL
                        DEFAULT 'unverified' CHECK(confidence IN
                            ('unverified','plausible','confirmed'));
                    ALTER TABLE project_people ADD COLUMN valid_from TEXT;
                    ALTER TABLE project_people ADD COLUMN valid_until TEXT;
                    ALTER TABLE project_person_tasks ADD COLUMN confidence TEXT NOT NULL
                        DEFAULT 'unverified' CHECK(confidence IN
                            ('unverified','plausible','confirmed'));
                    ALTER TABLE project_person_tasks ADD COLUMN valid_from TEXT;
                    ALTER TABLE project_person_tasks ADD COLUMN valid_until TEXT;
                    PRAGMA user_version=44;
                """)
            if version < 45:
                apply_migration("""
                    CREATE TABLE project_person_aliases (
                        id TEXT PRIMARY KEY,
                        state_id TEXT NOT NULL REFERENCES work_states(id) ON DELETE CASCADE,
                        person_id TEXT NOT NULL REFERENCES project_people(id) ON DELETE CASCADE,
                        alias TEXT NOT NULL,
                        source_note TEXT NOT NULL,
                        source_document_id TEXT,
                        source_path TEXT,
                        source_sha256 TEXT,
                        confidence TEXT NOT NULL DEFAULT 'unverified' CHECK(confidence IN
                            ('unverified','plausible','confirmed')),
                        valid_from TEXT,
                        valid_until TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX project_person_aliases_state
                        ON project_person_aliases(state_id,created_at,id);
                    PRAGMA user_version=45;
                """)
            if version < 46:
                apply_migration("""
                    CREATE TABLE project_person_merges (
                        id TEXT PRIMARY KEY,
                        state_id TEXT NOT NULL REFERENCES work_states(id) ON DELETE CASCADE,
                        source_person_id TEXT NOT NULL,
                        target_person_id TEXT NOT NULL,
                        source_snapshot_json TEXT NOT NULL,
                        moved_aliases_json TEXT NOT NULL,
                        moved_links_json TEXT NOT NULL,
                        canonical_alias_id TEXT NOT NULL,
                        target_version_after INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        split_at TEXT
                    );
                    CREATE INDEX project_person_merges_state
                        ON project_person_merges(state_id,created_at,id);
                    PRAGMA user_version=46;
                """)
            if version < 47:
                apply_migration("""
                    CREATE TABLE project_memory_links (
                        id TEXT PRIMARY KEY,
                        state_id TEXT NOT NULL REFERENCES work_states(id) ON DELETE CASCADE,
                        memory_id TEXT NOT NULL,
                        memory_text TEXT NOT NULL,
                        memory_source_ref TEXT NOT NULL,
                        memory_policy TEXT NOT NULL CHECK(memory_policy IN
                            ('local_only','cloud_allowed')),
                        memory_sha256 TEXT NOT NULL,
                        source_note TEXT NOT NULL,
                        confidence TEXT NOT NULL DEFAULT 'unverified' CHECK(confidence IN
                            ('unverified','plausible','confirmed')),
                        valid_from TEXT,
                        valid_until TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(state_id,memory_id)
                    );
                    CREATE INDEX project_memory_links_state
                        ON project_memory_links(state_id,created_at,id);
                    PRAGMA user_version=47;
                """)
            if version < 48:
                apply_migration("""
                    CREATE TABLE named_style_profiles (
                        name TEXT PRIMARY KEY CHECK(name='Maxim'),
                        description TEXT NOT NULL,
                        policy TEXT NOT NULL CHECK(policy IN ('local_only','cloud_allowed')),
                        revision INTEGER NOT NULL CHECK(revision>=1),
                        updated_at TEXT NOT NULL
                    );
                    CREATE TABLE named_style_profile_revisions (
                        name TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        description TEXT NOT NULL,
                        policy TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(name,revision)
                    );
                    CREATE TABLE draft_named_style_usage (
                        draft_id TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
                        name TEXT NOT NULL,
                        revision INTEGER NOT NULL,
                        description TEXT NOT NULL
                    );
                    PRAGMA user_version=48;
                """)
            if version < 49:
                apply_migration("""
                    CREATE TABLE learning_sessions (
                        id TEXT PRIMARY KEY,
                        topic TEXT NOT NULL,
                        question TEXT NOT NULL,
                        reference_answer TEXT NOT NULL,
                        evidence_quote TEXT NOT NULL,
                        source_json TEXT NOT NULL,
                        user_answer TEXT,
                        evaluation TEXT CHECK(evaluation IN
                            ('correct','partial','incorrect','uncertain')),
                        feedback TEXT,
                        feedback_quote TEXT,
                        next_question TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL CHECK(version>=1)
                    );
                    CREATE INDEX learning_sessions_updated
                        ON learning_sessions(updated_at,id);
                    PRAGMA user_version=49;
                """)
            if version < 50:
                apply_migration("""
                    CREATE TABLE memory_proposals (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT NOT NULL,
                        chat_title TEXT NOT NULL,
                        snapshot_message_id TEXT NOT NULL,
                        message_id TEXT NOT NULL,
                        message_created_at TEXT NOT NULL,
                        source_quote TEXT NOT NULL,
                        candidate_text TEXT NOT NULL,
                        decided_text TEXT,
                        memory_id TEXT,
                        status TEXT NOT NULL CHECK(status IN
                            ('pending','rejected','accepted','deleted')),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        version INTEGER NOT NULL CHECK(version>=1),
                        UNIQUE(chat_id,snapshot_message_id,message_id,source_quote)
                    );
                    CREATE INDEX memory_proposals_chat_time
                        ON memory_proposals(chat_id,created_at);
                    CREATE TABLE memory_proposal_events (
                        id TEXT PRIMARY KEY,
                        proposal_id TEXT NOT NULL REFERENCES memory_proposals(id) ON DELETE CASCADE,
                        action TEXT NOT NULL CHECK(action IN
                            ('accepted','rejected','corrected','deleted')),
                        text TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX memory_proposal_events_proposal
                        ON memory_proposal_events(proposal_id,created_at);
                    PRAGMA user_version=50;
                """)
            if version < 51:
                apply_migration("""
                    CREATE TABLE memory_context_uses (
                        assistant_message_id TEXT NOT NULL
                            REFERENCES messages(id) ON DELETE CASCADE,
                        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                        proposal_id TEXT NOT NULL
                            REFERENCES memory_proposals(id) ON DELETE CASCADE,
                        memory_id TEXT NOT NULL,
                        text_snapshot TEXT NOT NULL,
                        source_quote TEXT NOT NULL,
                        used_at TEXT NOT NULL,
                        PRIMARY KEY(assistant_message_id,proposal_id)
                    );
                    CREATE INDEX memory_context_uses_chat ON memory_context_uses(chat_id);
                    CREATE INDEX memory_context_uses_proposal_time
                        ON memory_context_uses(proposal_id,used_at);
                    PRAGMA user_version=51;
                """)
            if version < 52:
                apply_migration("""
                    ALTER TABLE memory_proposals ADD COLUMN valid_from TEXT;
                    ALTER TABLE memory_proposals ADD COLUMN valid_until TEXT;
                    ALTER TABLE memory_proposal_events ADD COLUMN valid_from TEXT;
                    ALTER TABLE memory_proposal_events ADD COLUMN valid_until TEXT;
                    PRAGMA user_version=52;
                """)
            if version < 53:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS chat_attachments (
                        id TEXT PRIMARY KEY,
                        chat_id TEXT NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
                        message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
                        filename TEXT NOT NULL,
                        suffix TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS chat_attachments_chat_message
                        ON chat_attachments(chat_id,message_id);
                    PRAGMA user_version=53;
                """)
            if version < 54:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS source_preferences (
                        query_key TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        path TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (query_key, document_id)
                    );
                    PRAGMA user_version=54;
                """)
            if version < 55:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS contacts (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        name_key TEXT NOT NULL,
                        context TEXT NOT NULL,
                        context_key TEXT NOT NULL,
                        relationship TEXT NOT NULL DEFAULT '',
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE UNIQUE INDEX IF NOT EXISTS contacts_identity
                        ON contacts(name_key, context_key);
                    CREATE TABLE IF NOT EXISTS contact_notes (
                        id TEXT PRIMARY KEY,
                        contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                        content TEXT NOT NULL,
                        source_kind TEXT NOT NULL CHECK(source_kind='manual'),
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS contact_notes_contact
                        ON contact_notes(contact_id, created_at);
                    PRAGMA user_version=55;
                """)
            if version < 56:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS contact_plans (
                        id TEXT PRIMARY KEY,
                        contact_id TEXT NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
                        title TEXT NOT NULL,
                        details TEXT NOT NULL DEFAULT '',
                        source_kind TEXT NOT NULL DEFAULT 'manual'
                            CHECK(source_kind='manual'),
                        status TEXT NOT NULL DEFAULT 'open'
                            CHECK(status IN ('open','completed')),
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS contact_plans_contact
                        ON contact_plans(contact_id, created_at);
                    PRAGMA user_version=56;
                """)
            if version < 57:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS contact_plan_reminders (
                        plan_id TEXT PRIMARY KEY REFERENCES contact_plans(id) ON DELETE CASCADE,
                        task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE SET NULL,
                        task_title_snapshot TEXT NOT NULL,
                        due_utc_snapshot TEXT NOT NULL,
                        due_local_snapshot TEXT NOT NULL,
                        timezone_snapshot TEXT NOT NULL,
                        linked_at TEXT NOT NULL
                    );
                    PRAGMA user_version=57;
                """)
            if version < 58:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS draft_recipients (
                        draft_id TEXT PRIMARY KEY REFERENCES drafts(id) ON DELETE CASCADE,
                        snapshot_json TEXT NOT NULL
                    );
                    PRAGMA user_version=58;
                """)
            if version < 59:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS memory_events (
                        id TEXT PRIMARY KEY,
                        date_local TEXT NOT NULL,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        location TEXT NOT NULL DEFAULT '',
                        source_kind TEXT NOT NULL DEFAULT 'manual'
                            CHECK(source_kind='manual'),
                        version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS memory_events_date
                        ON memory_events(date_local DESC,id DESC);
                    PRAGMA user_version=59;
                """)
            if version < 60:
                apply_migration("""
                    CREATE TABLE IF NOT EXISTS photo_folders (
                        id TEXT PRIMARY KEY,
                        path TEXT NOT NULL UNIQUE,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS photo_assets (
                        id TEXT PRIMARY KEY,
                        folder_id TEXT NOT NULL REFERENCES photo_folders(id) ON DELETE CASCADE,
                        name TEXT NOT NULL,
                        sha256 TEXT NOT NULL,
                        size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        media_type TEXT NOT NULL,
                        event_id TEXT REFERENCES memory_events(id) ON DELETE SET NULL,
                        version INTEGER NOT NULL DEFAULT 1,
                        indexed_at TEXT NOT NULL,
                        UNIQUE(folder_id,name)
                    );
                    CREATE INDEX IF NOT EXISTS photo_assets_event ON photo_assets(event_id);
                    PRAGMA user_version=60;
                """)
            if version < 61:
                columns = {row[1] for row in db.execute("PRAGMA table_info(photo_folders)")}
                if "version" in columns:
                    apply_migration("PRAGMA user_version=61;")
                else:
                    apply_migration("""
                        ALTER TABLE photo_folders ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
                        PRAGMA user_version=61;
                    """)
            if version < 62:
                columns = {row[1] for row in db.execute("PRAGMA table_info(photo_assets)")}
                if "capture_local" in columns:
                    apply_migration("PRAGMA user_version=62;")
                else:
                    apply_migration("""
                        ALTER TABLE photo_assets ADD COLUMN capture_local TEXT;
                        PRAGMA user_version=62;
                    """)

    def claim_lease(self, owner: str, seconds: int = 12) -> bool:
        now = datetime.now(UTC)
        expires = (now + timedelta(seconds=seconds)).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT owner,expires_at FROM worker_lease WHERE id=1").fetchone()
            if prior and prior["owner"] != owner and prior["expires_at"] > now.isoformat():
                return False
            db.execute("""INSERT INTO worker_lease VALUES(1,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET owner=excluded.owner,
                       expires_at=excluded.expires_at,updated_at=excluded.updated_at""",
                       (owner, expires, now.isoformat()))
        return True

    def renew_lease(self, owner: str, seconds: int = 12) -> bool:
        now = datetime.now(UTC)
        with self.connect() as db:
            result = db.execute("UPDATE worker_lease SET expires_at=?,updated_at=? WHERE id=1 AND owner=?",
                                ((now + timedelta(seconds=seconds)).isoformat(), now.isoformat(), owner))
            return result.rowcount == 1

    def release_lease(self, owner: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM worker_lease WHERE id=1 AND owner=?", (owner,))

    def create_chat(self, title: str = "Neues Gespräch") -> dict:
        chat_id, now = str(uuid4()), utcnow()
        with self.connect() as db:
            db.execute(
                "INSERT INTO chats(id,title,created_at,updated_at) VALUES(?,?,?,?)",
                (chat_id, title, now, now),
            )
        return {"id": chat_id, "title": title, "created_at": now,
                "updated_at": now, "archived_at": None}

    def list_chats(self, query: str = "", archived: bool = False) -> list[dict]:
        term = query.strip().casefold()
        if len(query) > 200:
            raise ValueError("Suchtext darf höchstens 200 Zeichen haben")
        with self.connect() as db:
            where = "archived_at IS NOT NULL" if archived else "archived_at IS NULL"
            chats = [dict(row) for row in db.execute(
                f"SELECT * FROM chats WHERE {where} ORDER BY updated_at DESC")]
            if not term or not chats:
                return chats
            chat_ids = {row["id"] for row in chats}
            matches: dict[str, tuple[str, str]] = {}
            for row in db.execute("SELECT id,chat_id,content FROM messages ORDER BY created_at DESC,id DESC"):
                if (row["chat_id"] in chat_ids and row["chat_id"] not in matches
                        and term in row["content"].casefold()):
                    folded_position = row["content"].casefold().find(term)
                    position = folded_length = 0
                    for char in row["content"]:
                        width = len(char.casefold())
                        if folded_length + width > folded_position:
                            break
                        folded_length += width
                        position += 1
                    start = max(0, position - 50)
                    excerpt = row["content"][start:start + 200]
                    matches[row["chat_id"]] = (row["id"], ("…" if start else "") + excerpt)
            return [{**row, "match_message_id": matches[row["id"]][0] if row["id"] in matches else None,
                     "match_preview": matches[row["id"]][1] if row["id"] in matches else None}
                    for row in chats
                    if term in row["title"].casefold() or row["id"] in matches]

    def set_chat_archived(self, chat_id: str, archived: bool) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT id FROM chats WHERE id=?", (chat_id,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE chats SET archived_at=? WHERE id=?",
                       (utcnow() if archived else None, chat_id))
        self.log("chat_archived" if archived else "chat_restored", f"Chat {chat_id}")
        return self.get_chat(chat_id)

    def rename_chat(self, chat_id: str, title: str) -> dict | None:
        title = title.strip()
        if not title or len(title) > 80 or any(ord(char) < 32 or ord(char) == 127 for char in title):
            raise ValueError("Gesprächstext muss 1–80 Zeichen ohne Steuerzeichen enthalten")
        with self.connect() as db:
            changed = db.execute("UPDATE chats SET title=? WHERE id=?", (title, chat_id)).rowcount
        if not changed:
            return None
        self.log("chat_renamed", f"Chat {chat_id}")
        return self.get_chat(chat_id)

    def delete_chat(self, chat_id: str) -> bool:
        with self.connect() as db:
            removed = db.execute("DELETE FROM chats WHERE id=?", (chat_id,)).rowcount == 1
        if removed:
            self.log("chat_deleted", f"Chat {chat_id}")
        return removed

    def get_chat(self, chat_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            return dict(row) if row else None

    def list_messages(self, chat_id: str, *, include_attachment_text: bool = False) -> list[dict]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("""SELECT m.*,
                u.created_at AS usage_started_at,u.updated_at AS usage_finished_at,
                u.status AS usage_status,s.name AS skill_name,
                e.verdict AS evaluation_verdict,
                e.source_quality AS evaluation_source_quality,e.reason AS evaluation_reason,
                e.regression_case AS evaluation_regression_case,
                e.expected_result AS evaluation_expected_result,
                e.version AS evaluation_version,e.updated_at AS evaluation_updated_at
                FROM messages m LEFT JOIN model_usage u ON u.id=m.usage_id
                LEFT JOIN skills s ON s.id=m.skill_id
                LEFT JOIN chat_answer_evaluations e ON e.message_id=m.id
                WHERE m.chat_id=? ORDER BY m.created_at,m.id""", (chat_id,))]
            memory_uses: dict[str, list[dict]] = {}
            for used in db.execute("""SELECT assistant_message_id,proposal_id,memory_id,
                text_snapshot,source_quote,used_at FROM memory_context_uses
                WHERE chat_id=? ORDER BY used_at,proposal_id""", (chat_id,)):
                entry = dict(used)
                memory_uses.setdefault(entry.pop("assistant_message_id"), []).append(entry)
            attachments: dict[str, list[dict]] = {}
            for attached in db.execute("""SELECT id,message_id,filename,sha256,size,text
                FROM chat_attachments WHERE chat_id=? AND message_id IS NOT NULL
                ORDER BY rowid""", (chat_id,)):
                entry = dict(attached)
                if not include_attachment_text:
                    entry.pop("text")
                attachments.setdefault(entry.pop("message_id"), []).append(entry)
            for row in rows:
                row["memory_context"] = memory_uses.get(row["id"], [])
                row["attachments"] = attachments.get(row["id"], [])
                row["usage_duration_seconds"] = (
                    max(0.0, (datetime.fromisoformat(row["usage_finished_at"])
                              - datetime.fromisoformat(row["usage_started_at"])).total_seconds())
                    if row["usage_started_at"] and row["usage_finished_at"]
                    and row["usage_status"] != "running" else None
                )
                verdict = row.pop("evaluation_verdict")
                fields = ("source_quality", "reason", "regression_case", "expected_result",
                          "version", "updated_at")
                row["evaluation"] = ({"message_id": row["id"], "verdict": verdict,
                                      **{field: row.pop("evaluation_" + field)
                                         for field in fields}}
                                     if verdict is not None else None)
                if verdict is None:
                    for field in fields:
                        row.pop("evaluation_" + field)
            return rows

    def list_chat_compactions(self, chat_id: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute("""SELECT * FROM chat_compactions WHERE chat_id=?
                ORDER BY version DESC""", (chat_id,)).fetchall()
        return [self._decode_chat_compaction(row) for row in rows]

    @staticmethod
    def _decode_chat_compaction(row: sqlite3.Row) -> dict:
        item = dict(row)
        item["covered_message_ids"] = json.loads(item.pop("covered_message_ids_json"))
        item["entries"] = json.loads(item.pop("entries_json"))
        return item

    def latest_chat_compaction(self, chat_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("""SELECT * FROM chat_compactions WHERE chat_id=?
                ORDER BY version DESC LIMIT 1""", (chat_id,)).fetchone()
        return self._decode_chat_compaction(row) if row else None

    def save_chat_compaction(self, chat_id: str, expected_version: int,
                             expected_last_message_id: str, covered_ids: list[str],
                             entries: list[dict], model: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            chat = db.execute("SELECT archived_at FROM chats WHERE id=?", (chat_id,)).fetchone()
            if chat is None or chat["archived_at"] is not None:
                raise ValueError("Gespräch fehlt oder ist archiviert")
            last = db.execute("""SELECT id FROM messages WHERE chat_id=?
                ORDER BY created_at DESC,id DESC LIMIT 1""", (chat_id,)).fetchone()
            if last is None or last["id"] != expected_last_message_id:
                raise ValueError("Gespräch wurde während der Zusammenfassung geändert")
            prior = db.execute("""SELECT version,covered_message_ids_json FROM chat_compactions
                WHERE chat_id=? ORDER BY version DESC LIMIT 1""", (chat_id,)).fetchone()
            if (prior["version"] if prior else 0) != expected_version:
                raise ValueError("Gesprächszustand wurde zwischenzeitlich geändert")
            prior_ids = json.loads(prior["covered_message_ids_json"]) if prior else []
            if covered_ids[:len(prior_ids)] != prior_ids or len(covered_ids) <= len(prior_ids):
                raise ValueError("Zusammenfassungsbereich ist ungültig")
            complete = [row["id"] for row in db.execute("""SELECT id FROM messages
                WHERE chat_id=? AND status='complete' ORDER BY created_at,id""", (chat_id,))]
            if complete[:len(covered_ids)] != covered_ids or len(complete) - len(covered_ids) < 8:
                raise ValueError("Zusammenfassungsbereich passt nicht zur aktuellen Historie")
            version, now = expected_version + 1, utcnow()
            db.execute("""INSERT INTO chat_compactions
                (chat_id,version,covered_message_ids_json,entries_json,provider,model,created_at)
                VALUES(?,?,?,?,?,?,?)""",
                (chat_id, version, json.dumps(covered_ids),
                 json.dumps(entries, ensure_ascii=False), "ollama", model, now))
        return {"chat_id": chat_id, "version": version,
                "covered_message_ids": covered_ids, "entries": entries,
                "provider": "ollama", "model": model, "created_by": "ollama",
                "created_at": now}

    def revise_chat_compaction(self, chat_id: str, expected_version: int,
                               expected_last_message_id: str, entry_index: int | None) -> dict:
        """Entferne einen belegten Punkt oder setze den aktiven Zustand zurück."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            chat = db.execute("SELECT archived_at FROM chats WHERE id=?", (chat_id,)).fetchone()
            if chat is None or chat["archived_at"] is not None:
                raise ValueError("Gespräch fehlt oder ist archiviert")
            last = db.execute("""SELECT id FROM messages WHERE chat_id=?
                ORDER BY created_at DESC,id DESC LIMIT 1""", (chat_id,)).fetchone()
            if last is None or last["id"] != expected_last_message_id:
                raise ValueError("Gespräch wurde zwischenzeitlich geändert")
            prior = db.execute("""SELECT * FROM chat_compactions WHERE chat_id=?
                ORDER BY version DESC LIMIT 1""", (chat_id,)).fetchone()
            if prior is None or prior["version"] != expected_version:
                raise ValueError("Gesprächszustand wurde zwischenzeitlich geändert")
            previous = self._decode_chat_compaction(prior)
            if entry_index is None:
                if not previous["entries"] and not previous["covered_message_ids"]:
                    raise ValueError("Gesprächszustand ist bereits zurückgesetzt")
                covered_ids, entries = [], []
                created_by = "user_reset"
            else:
                if not 0 <= entry_index < len(previous["entries"]):
                    raise ValueError("Zustandspunkt nicht gefunden")
                if len(previous["entries"]) == 1:
                    raise ValueError("Letzten Punkt nur durch Zurücksetzen entfernen")
                covered_ids = previous["covered_message_ids"]
                entries = [item for index, item in enumerate(previous["entries"])
                           if index != entry_index]
                created_by = "user_remove"
            version, now = expected_version + 1, utcnow()
            db.execute("""INSERT INTO chat_compactions
                (chat_id,version,covered_message_ids_json,entries_json,provider,model,created_at,
                 created_by) VALUES(?,?,?,?,?,?,?,?)""",
                (chat_id, version, json.dumps(covered_ids),
                 json.dumps(entries, ensure_ascii=False), "ollama", previous["model"], now,
                 created_by))
        return {"chat_id": chat_id, "version": version,
                "covered_message_ids": covered_ids, "entries": entries,
                "provider": "ollama", "model": previous["model"],
                "created_by": created_by, "created_at": now}

    def add_message(self, chat_id: str, role: str, content: str, status: str = "complete",
                    memory_context: list[dict] | None = None,
                    attachment_ids: list[str] | None = None, **usage) -> dict:
        message_id, now = str(uuid4()), utcnow()
        if attachment_ids and (role != "user" or len(attachment_ids) > 3
                               or len(set(attachment_ids)) != len(attachment_ids)):
            raise ValueError("Höchstens drei verschiedene Dateien je Nutzernachricht")
        with self.connect() as db:
            db.execute(
                """INSERT INTO messages(id,chat_id,role,content,status,created_at,provider,model,
                   input_tokens,output_tokens,cost_usd,cost_kind,citations_json,rule_version,usage_id,
                   skill_id,skill_version,skill_hash,compaction_version,
                   context_estimated_tokens,context_token_budget,context_omitted_count,
                   context_latest_omitted_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (message_id, chat_id, role, content, status, now, usage.get("provider"),
                 usage.get("model"), usage.get("input_tokens"), usage.get("output_tokens"),
                 usage.get("cost_usd"), usage.get("cost_kind"), usage.get("citations_json"),
                 usage.get("rule_version"), usage.get("usage_id"), usage.get("skill_id"),
                 usage.get("skill_version"), usage.get("skill_hash"),
                 usage.get("compaction_version"), usage.get("context_estimated_tokens"),
                 usage.get("context_token_budget"), usage.get("context_omitted_count"),
                 usage.get("context_latest_omitted_id")),
            )
            db.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
            for attachment_id in attachment_ids or []:
                linked = db.execute("""UPDATE chat_attachments SET message_id=?
                    WHERE id=? AND chat_id=? AND message_id IS NULL""",
                    (message_id, attachment_id, chat_id)).rowcount
                if linked != 1:
                    raise ValueError("Chatanhang nicht gefunden oder bereits verwendet")
            if role == "assistant" and memory_context:
                for selected in memory_context:
                    db.execute("""INSERT INTO memory_context_uses
                        (assistant_message_id,chat_id,proposal_id,memory_id,
                         text_snapshot,source_quote,used_at)
                        VALUES(?,?,?,?,?,?,?)""",
                        (message_id, chat_id, selected["proposal_id"],
                         selected["memory_id"], selected["text"],
                         selected["source_quote"], now))
            if role == "user":
                db.execute(
                    "UPDATE chats SET title=? WHERE id=? AND title='Neues Gespräch'",
                    (content[:60].strip() or "Neues Gespräch", chat_id),
                )
        return {"id": message_id, "chat_id": chat_id, "role": role, "content": content,
                "status": status, "created_at": now, **usage,
                "memory_context": memory_context or []}

    def log(self, kind: str, detail: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO activity VALUES(?,?,?,?)", (str(uuid4()), kind, detail, utcnow()))
        if kind in SEARCH_SOURCE_CHANGED:
            PersistentEmbeddingCache(self.path.parent / "semantic-embeddings.sqlite").clear()

    def list_activity(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM activity ORDER BY created_at DESC LIMIT ?", (limit,)
            )]

    def cost_since(self, since: str) -> float:
        with self.connect() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(cost_usd),0) FROM model_usage WHERE provider='openrouter' AND created_at>=?",
                (since,),
            ).fetchone()
            return float(row[0])

    def list_usage(self, limit: int = 20) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT id,purpose,provider,model,input_tokens,output_tokens,cost_usd,
                   cost_kind,status,created_at FROM model_usage
                   ORDER BY created_at DESC,id DESC LIMIT ?""", (limit,)
            )]

    def reserve_usage(self, purpose: str, provider: str, model: str, reservation_usd: float,
                      daily_budget_usd: float, since: str) -> str:
        if (not math.isfinite(reservation_usd) or reservation_usd < 0
                or not math.isfinite(daily_budget_usd) or daily_budget_usd < 0):
            raise ValueError("Ungültige Kosten- oder Budgetgrenze")
        usage_id, now = str(uuid4()), utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            spent = db.execute("""SELECT COALESCE(SUM(cost_usd),0) FROM model_usage
                               WHERE provider='openrouter' AND created_at>=?""", (since,)).fetchone()[0]
            if provider == "openrouter" and spent + reservation_usd > daily_budget_usd:
                raise ValueError("Tagesbudget reicht für diese Anfrage nicht aus")
            db.execute("""INSERT INTO model_usage(id,purpose,provider,model,cost_usd,cost_kind,status,
                       created_at,updated_at) VALUES(?,?,?,?,?,'preflight_reservation','running',?,?)""",
                       (usage_id, purpose, provider, model, reservation_usd, now, now))
        return usage_id

    def finish_usage(self, usage_id: str, input_tokens: int | None, output_tokens: int | None,
                     cost_usd: float | None, cost_kind: str | None, status: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT cost_usd FROM model_usage WHERE id=?", (usage_id,)).fetchone()
            if row is None:
                return
            valid_cost = (type(cost_usd) in {int, float} and math.isfinite(cost_usd)
                          and cost_usd >= 0)
            actual = float(cost_usd) if valid_cost else row["cost_usd"]
            kind = (cost_kind or "reported") if valid_cost else "upper_bound_after_missing_usage"
            db.execute("""UPDATE model_usage SET input_tokens=?,output_tokens=?,cost_usd=?,
                       cost_kind=?,status=?,updated_at=? WHERE id=?""",
                       (input_tokens, output_tokens, actual, kind, status, utcnow(), usage_id))

    def get_settings(self) -> dict[str, str]:
        with self.connect() as db:
            return {row["key"]: row["value"] for row in db.execute("SELECT key,value FROM app_settings")}

    def set_settings(self, values: dict[str, str]) -> None:
        with self.connect() as db:
            db.executemany("INSERT INTO app_settings(key,value) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", values.items())

    def create_run(self, tool_name: str, args_json: str, args_hash: str, effect: str,
                   client_request_id: str | None = None) -> dict:
        run_id, now = str(uuid4()), utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if client_request_id:
                prior = db.execute("SELECT * FROM runs WHERE client_request_id=?",
                                   (client_request_id,)).fetchone()
                if prior:
                    if prior["args_hash"] != args_hash or prior["tool_name"] != tool_name:
                        raise ValueError("Idempotenz-ID wurde mit anderen Argumenten verwendet")
                    return dict(prior)
            db.execute(
                """INSERT INTO runs(id,client_request_id,tool_name,args_json,args_hash,effect,status,
                   created_at,updated_at) VALUES(?,?,?,?,?,?,'queued',?,?)""",
                (run_id, client_request_id, tool_name, args_json, args_hash, effect, now, now),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_runs(self, limit: int = 100) -> list[dict]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)
            )]
            for run in rows:
                ledger = db.execute("""SELECT status,started_at,finished_at FROM tool_ledger
                                    WHERE run_id=? ORDER BY started_at DESC LIMIT 1""",
                                    (run["id"],)).fetchone()
                run["ledger"] = dict(ledger) if ledger else None
                run["execution_seconds"] = (
                    max(0.0, (datetime.fromisoformat(ledger["finished_at"])
                              - datetime.fromisoformat(ledger["started_at"])).total_seconds())
                    if ledger and ledger["finished_at"] else None
                )
                evaluation = db.execute("SELECT * FROM run_evaluations WHERE run_id=?",
                                        (run["id"],)).fetchone()
                run["evaluation"] = dict(evaluation) if evaluation else None
                run["model_cost_usd"] = None  # Kein eindeutiger Modellkostenbeleg je Werkzeuglauf.
            return rows

    def evaluate_run(self, run_id: str, verdict: str, reason: str,
                     regression_case: bool, expected_result: str, version: int) -> dict:
        if verdict not in {"achieved", "partial", "failed", "unclear"}:
            raise ValueError("Ungültiges Prüfergebnis")
        reason, expected_result = reason.strip(), expected_result.strip()
        if not 1 <= len(reason) <= 2000:
            raise ValueError("Begründung muss 1–2.000 Zeichen enthalten")
        if regression_case and (verdict not in {"partial", "failed"}
                                or not 1 <= len(expected_result) <= 500):
            raise ValueError("Fehlerfall braucht Teilerfolg/Fehlschlag und ein erwartetes Ergebnis")
        if not regression_case:
            expected_result = ""
        now = utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise LookupError("Auftrag nicht gefunden")
            if run["status"] not in {"succeeded", "failed", "cancelled", "interrupted"}:
                raise ValueError("Nur abgeschlossene Aufträge können bewertet werden")
            prior = db.execute("SELECT version FROM run_evaluations WHERE run_id=?",
                               (run_id,)).fetchone()
            if prior is None:
                if version != 0:
                    raise RuntimeError("Bewertung wurde zwischenzeitlich geändert")
                db.execute("""INSERT INTO run_evaluations
                           (run_id,verdict,reason,regression_case,expected_result,
                            version,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)""",
                           (run_id, verdict, reason, int(regression_case), expected_result, now, now))
            else:
                if prior["version"] != version:
                    raise RuntimeError("Bewertung wurde zwischenzeitlich geändert")
                db.execute("""UPDATE run_evaluations SET verdict=?,reason=?,regression_case=?,
                           expected_result=?,version=version+1,updated_at=? WHERE run_id=?""",
                           (verdict, reason, int(regression_case), expected_result, now, run_id))
            return dict(db.execute("SELECT * FROM run_evaluations WHERE run_id=?",
                                   (run_id,)).fetchone())

    def list_evaluation_cases(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT e.*,r.tool_name,r.args_json,
                       r.args_hash,r.effect,r.status,r.result_json,r.error,
                       l.status AS ledger_status,l.started_at,l.finished_at
                       FROM run_evaluations e JOIN runs r ON r.id=e.run_id
                       LEFT JOIN tool_ledger l ON l.run_id=e.run_id
                       WHERE e.regression_case=1 ORDER BY e.updated_at DESC""")]

    def evaluate_plan(self, plan_id: str, verdict: str, source_quality: str,
                      reason: str, regression_case: bool, expected_result: str,
                      version: int) -> dict:
        if verdict not in {"achieved", "partial", "failed", "unclear"}:
            raise ValueError("Ungültiges Prüfergebnis")
        if source_quality not in {"supported", "unsupported", "not_applicable", "unclear"}:
            raise ValueError("Ungültige Quellenbewertung")
        reason, expected_result = reason.strip(), expected_result.strip()
        if not 1 <= len(reason) <= 2000:
            raise ValueError("Begründung muss 1–2.000 Zeichen enthalten")
        if regression_case and (verdict not in {"partial", "failed"}
                                or not 1 <= len(expected_result) <= 500):
            raise ValueError("Fehlerfall braucht Teilerfolg/Fehlschlag und ein erwartetes Ergebnis")
        if not regression_case:
            expected_result = ""
        now = utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            plan = db.execute("SELECT status FROM plans WHERE id=?", (plan_id,)).fetchone()
            if plan is None:
                raise LookupError("Plan nicht gefunden")
            if plan["status"] not in {"succeeded", "failed", "cancelled", "interrupted"}:
                raise ValueError("Nur abgeschlossene Pläne können bewertet werden")
            prior = db.execute("SELECT version FROM plan_evaluations WHERE plan_id=?",
                               (plan_id,)).fetchone()
            if prior is None:
                if version != 0:
                    raise RuntimeError("Planbewertung wurde zwischenzeitlich geändert")
                db.execute("""INSERT INTO plan_evaluations
                           (plan_id,verdict,source_quality,reason,regression_case,
                            expected_result,version,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,1,?,?)""",
                           (plan_id, verdict, source_quality, reason, int(regression_case),
                            expected_result, now, now))
            else:
                if prior["version"] != version:
                    raise RuntimeError("Planbewertung wurde zwischenzeitlich geändert")
                db.execute("""UPDATE plan_evaluations SET verdict=?,source_quality=?,
                           reason=?,regression_case=?,expected_result=?,version=version+1,
                           updated_at=? WHERE plan_id=?""",
                           (verdict, source_quality, reason, int(regression_case),
                            expected_result, now, plan_id))
            return dict(db.execute("SELECT * FROM plan_evaluations WHERE plan_id=?",
                                   (plan_id,)).fetchone())

    def list_plan_evaluation_cases(self) -> list[dict]:
        with self.connect() as db:
            cases = [dict(row) for row in db.execute("""SELECT e.*,p.objective,p.steps_json,
                       p.status,p.error,p.provider,p.model,p.rule_version,p.usage_id,
                       u.input_tokens,u.output_tokens,u.cost_usd,u.cost_kind,
                       u.created_at AS usage_started_at,u.updated_at AS usage_finished_at
                       FROM plan_evaluations e JOIN plans p ON p.id=e.plan_id
                       LEFT JOIN model_usage u ON u.id=p.usage_id
                       WHERE e.regression_case=1 ORDER BY e.updated_at DESC""")]
            for case in cases:
                case["run_results"] = []
                for step in json.loads(case["steps_json"]):
                    if run_id := step.get("run_id"):
                        run = db.execute("""SELECT id,tool_name,status,result_json,error
                                           FROM runs WHERE id=?""", (run_id,)).fetchone()
                        if run:
                            case["run_results"].append(dict(run))
            return cases

    def evaluate_chat_answer(self, chat_id: str, message_id: str, verdict: str,
                             source_quality: str, reason: str, regression_case: bool,
                             expected_result: str, version: int) -> dict:
        if verdict not in {"achieved", "partial", "failed", "unclear"}:
            raise ValueError("Ungültiges Prüfergebnis")
        if source_quality not in {"supported", "unsupported", "not_applicable", "unclear"}:
            raise ValueError("Ungültige Quellenbewertung")
        reason, expected_result = reason.strip(), expected_result.strip()
        if not 1 <= len(reason) <= 2000:
            raise ValueError("Begründung muss 1–2.000 Zeichen enthalten")
        if regression_case and (verdict not in {"partial", "failed"}
                                or not 1 <= len(expected_result) <= 500):
            raise ValueError("Fehlerfall braucht Teilerfolg/Fehlschlag und ein erwartetes Ergebnis")
        if not regression_case:
            expected_result = ""
        now = utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            message = db.execute("SELECT role FROM messages WHERE id=? AND chat_id=?",
                                 (message_id, chat_id)).fetchone()
            if message is None:
                raise LookupError("Antwort nicht gefunden")
            if message["role"] != "assistant":
                raise ValueError("Nur Assistentenantworten können bewertet werden")
            prior = db.execute("SELECT version FROM chat_answer_evaluations WHERE message_id=?",
                               (message_id,)).fetchone()
            if prior is None:
                if version != 0:
                    raise RuntimeError("Antwortbewertung wurde zwischenzeitlich geändert")
                db.execute("""INSERT INTO chat_answer_evaluations
                           (message_id,verdict,source_quality,reason,regression_case,
                            expected_result,version,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,1,?,?)""",
                           (message_id, verdict, source_quality, reason, int(regression_case),
                            expected_result, now, now))
            else:
                if prior["version"] != version:
                    raise RuntimeError("Antwortbewertung wurde zwischenzeitlich geändert")
                db.execute("""UPDATE chat_answer_evaluations SET verdict=?,source_quality=?,
                           reason=?,regression_case=?,expected_result=?,version=version+1,
                           updated_at=? WHERE message_id=?""",
                           (verdict, source_quality, reason, int(regression_case),
                            expected_result, now, message_id))
            return dict(db.execute("SELECT * FROM chat_answer_evaluations WHERE message_id=?",
                                   (message_id,)).fetchone())

    def list_chat_evaluation_cases(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT e.*,m.chat_id,m.content AS answer,
                       m.status,m.provider,m.model,m.input_tokens,m.output_tokens,m.cost_usd,
                       m.cost_kind,m.citations_json,m.rule_version,m.usage_id,
                       m.skill_id,m.skill_version,m.skill_hash,s.name AS skill_name,
                       (SELECT previous.content FROM messages previous
                        WHERE previous.chat_id=m.chat_id AND previous.role='user'
                          AND previous.created_at<=m.created_at
                        ORDER BY previous.created_at DESC,previous.id DESC LIMIT 1) AS question,
                       u.created_at AS usage_started_at,u.updated_at AS usage_finished_at
                       FROM chat_answer_evaluations e JOIN messages m ON m.id=e.message_id
                       LEFT JOIN skills s ON s.id=m.skill_id
                       LEFT JOIN model_usage u ON u.id=m.usage_id
                       WHERE e.regression_case=1 ORDER BY e.updated_at DESC""")]

    def set_run(self, run_id: str, status: str, result_json: str | None = None,
                error: str | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE runs SET status=?,result_json=?,error=?,updated_at=? WHERE id=?",
                       (status, result_json, error, utcnow(), run_id))

    def request_cancel(self, run_id: str) -> dict:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ValueError("Auftrag nicht gefunden")
            if row["status"] in {"queued", "waiting_approval"}:
                db.execute("UPDATE runs SET status='cancelled',updated_at=? WHERE id=?",
                           (utcnow(), run_id))
            elif row["status"] == "running":
                db.execute("UPDATE runs SET cancel_requested=1,updated_at=? WHERE id=?",
                           (utcnow(), run_id))
        return self.get_run(run_id)

    def set_approval(self, run_id: str, args_hash: str, decision: str) -> None:
        with self.connect() as db:
            row = db.execute("SELECT args_hash,status FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] != "waiting_approval" or row["args_hash"] != args_hash:
                raise ValueError("Freigabe passt nicht zum wartenden Auftrag")
            db.execute("INSERT INTO approvals(run_id,args_hash,decision,decided_at) VALUES(?,?,?,?) "
                       "ON CONFLICT(run_id) DO UPDATE SET decision=excluded.decision, "
                       "decided_at=excluded.decided_at",
                       (run_id, args_hash, decision, utcnow()))

    def get_approval(self, run_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM approvals WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def ledger_start(self, run_id: str, tool_name: str, args_hash: str, effect: str) -> dict:
        key, now = f"{run_id}:0", utcnow()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT status,cancel_requested FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] == "cancelled" or run["cancel_requested"]:
                return {"cancelled": True}
            prior = db.execute("SELECT * FROM tool_ledger WHERE idempotency_key=?", (key,)).fetchone()
            if prior:
                return dict(prior)
            db.execute("""INSERT INTO tool_ledger(idempotency_key,run_id,tool_name,args_hash,effect,
                       status,started_at) VALUES(?,?,?,?,?,'running',?)""",
                       (key, run_id, tool_name, args_hash, effect, now))
            db.execute("UPDATE runs SET status='running',updated_at=? WHERE id=?", (now, run_id))
        return {"idempotency_key": key, "status": "running", "new": True,
                "args_hash": args_hash}

    def ledger_finish(self, key: str, status: str, result_json: str | None,
                      error: str | None = None) -> None:
        with self.connect() as db:
            db.execute("UPDATE tool_ledger SET status=?,result_json=?,error=?,finished_at=? "
                       "WHERE idempotency_key=?", (status, result_json, error, utcnow(), key))

    def reconcile_runs(self) -> None:
        with self.connect() as db:
            db.execute("UPDATE tool_ledger SET status='unknown',finished_at=? "
                       "WHERE status='running' AND effect!='read'", (utcnow(),))
            db.execute("UPDATE tool_ledger SET status='interrupted',finished_at=? "
                       "WHERE status='running' AND effect='read'", (utcnow(),))
            db.execute("UPDATE runs SET status='interrupted',error='Prozess wurde unterbrochen',updated_at=? "
                       "WHERE status='running'", (utcnow(),))
            db.execute("UPDATE runs SET status='interrupted',error='Warteschlange durch Neustart unterbrochen',updated_at=? "
                       "WHERE status='queued'", (utcnow(),))
            db.execute("UPDATE model_usage SET status='interrupted',"
                       "cost_kind='upper_bound_after_interruption',updated_at=? "
                       "WHERE status='running'", (utcnow(),))
