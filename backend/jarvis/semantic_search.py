"""Optional, bounded local semantic retrieval from current personal sources."""

import math
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from jarvis.db import Database
from jarvis.documents import DocumentStore

MODEL = "embeddinggemma:300m"
MAX_PASSAGES = 100
MAX_DOCUMENTS = 30
MAX_MEMORIES = 30
MAX_PROJECTS = 20
MAX_RESULTS = 10
MAX_DEEP_RESULTS = 5


class SemanticSearchError(RuntimeError):
    """The local embedding service is unavailable or returned invalid vectors."""


def embed_texts(url: str, texts: list[str]) -> list[list[float]]:
    try:
        parsed = urlsplit(url)
        local = (parsed.scheme == "http" and parsed.hostname in
                 {"localhost", "127.0.0.1", "::1"} and parsed.port is not None
                 and not parsed.username and not parsed.password
                 and parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    except ValueError:
        local = False
    if not local:
        raise SemanticSearchError("Semantische Suche benötigt eine lokale Ollama-Adresse")
    try:
        with httpx.Client(timeout=120, trust_env=False) as client:
            response = client.post(f"{url.rstrip('/')}/api/embed",
                                   json={"model": MODEL, "input": texts})
            response.raise_for_status()
            vectors = response.json()["embeddings"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise SemanticSearchError(
            f"Lokale Embeddings nicht verfügbar. Ollama starten und 'ollama pull {MODEL}' "
            "ausführen."
        ) from exc
    if (not isinstance(vectors, list) or len(vectors) != len(texts)
            or not vectors or not isinstance(vectors[0], list)
            or not 1 <= len(vectors[0]) <= 4096):
        raise SemanticSearchError("Ollama lieferte keine gültigen Embeddings")
    dimension = len(vectors[0])
    if any(not isinstance(vector, list) or len(vector) != dimension
           or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                  for value in vector) or not 0 < sum(value * value for value in vector) < math.inf
           for vector in vectors):
        raise SemanticSearchError("Ollama lieferte ungültige Embeddings")
    return vectors


def _project_body(row: dict) -> str:
    return " · ".join(part for part in
                      (row["confirmed"], row["open_questions"], row["next_step"]) if part)


def _record_is_current(db: Database, kind: str, row: dict) -> bool:
    """Do not show a saved memory or work state changed during the Ollama call."""
    if kind == "memory":
        fields = ("id", "text", "source_ref", "policy", "updated_at")
        table = "memories"
    else:
        fields = ("id", "project", "confirmed", "open_questions", "next_step",
                  "version", "updated_at")
        table = "work_states"
    with db.connect() as connection:
        current = connection.execute(
            f"SELECT {','.join(fields)} FROM {table} WHERE id=?", (row["id"],)
        ).fetchone()
    return current is not None and all(current[field] == row[field] for field in fields)


def search(db: Database, query: str, ollama_url: str, folder_id: str | None = None,
           document_id: str | None = None) -> dict:
    cleaned = " ".join(query.split())
    if not 2 <= len(cleaned) <= 160:
        raise ValueError("Suchfrage muss 2–160 Zeichen enthalten")
    with db.connect() as connection:
        if folder_id and not connection.execute(
                "SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
            raise ValueError("Freigabeordner nicht gefunden")
        scope = None
        if document_id:
            document = connection.execute("""SELECT id,path,folder_id,status,excluded
                FROM documents WHERE id=?""", (document_id,)).fetchone()
            if document is None or document["excluded"] or document["status"] != "indexed":
                raise ValueError("Indexiertes Dokument nicht gefunden")
            if folder_id and document["folder_id"] != folder_id:
                raise ValueError("Dokument gehört nicht zum gewählten Ordner")
            scope = {"id": document["id"], "title": Path(document["path"]).name}
        rows = [dict(row) for row in connection.execute("""
            WITH ranked AS (
                SELECT p.id AS passage_id,p.document_id,p.page,p.text,d.path,d.policy,
                       d.sha256,d.size,d.status,d.excluded,f.path AS folder_path,
                       ROW_NUMBER() OVER (PARTITION BY d.id ORDER BY p.ordinal,p.id) AS part
                FROM passages p JOIN documents d ON d.id=p.document_id
                JOIN folders f ON f.id=d.folder_id
                WHERE d.excluded=0 AND d.status='indexed'
                  AND (? IS NULL OR d.folder_id=?)
                  AND (? IS NULL OR d.id=?)
            )
            SELECT * FROM ranked WHERE part<=? ORDER BY path,part,passage_id LIMIT ?
        """, (folder_id, folder_id, document_id, document_id,
              MAX_PASSAGES + 1 if document_id else 3, MAX_PASSAGES + 1))]
        memories = [] if document_id else [dict(row) for row in connection.execute("""
            SELECT id,text,source_ref,policy,updated_at FROM memories
            ORDER BY updated_at DESC,id DESC LIMIT ?
        """, (MAX_MEMORIES + 1,))]
        projects = [] if document_id else [dict(row) for row in connection.execute("""
            SELECT id,project,confirmed,open_questions,next_step,version,updated_at
            FROM work_states ORDER BY updated_at DESC,id DESC LIMIT ?
        """, (MAX_PROJECTS + 1,))]
    limited = (len(rows) > MAX_PASSAGES or len(memories) > MAX_MEMORIES
               or len(projects) > MAX_PROJECTS)
    freshness: dict[str, bool] = {}
    fresh = []
    stale = []
    checked_passages = 0
    for row in rows[:MAX_PASSAGES]:
        candidate_id = row["document_id"]
        if candidate_id not in freshness:
            if len(freshness) == MAX_DOCUMENTS:
                limited = True
                break
            freshness[candidate_id] = DocumentStore.source_is_current(row)
            if not freshness[candidate_id]:
                if len(stale) < MAX_RESULTS:
                    stale.append({"document_id": candidate_id, "path": row["path"],
                                  "index_sha256": row["sha256"], "status": "needs_reindex"})
                else:
                    limited = True
        checked_passages += 1
        if freshness[candidate_id]:
            fresh.append(row)
    memories = memories[:MAX_MEMORIES]
    projects = projects[:MAX_PROJECTS]
    corpus = ([('document', row, row["text"]) for row in fresh]
              + [('memory', row, f'{row["source_ref"]}\n{row["text"]}') for row in memories]
              + [('project', row, f'{row["project"]}\n{_project_body(row)}') for row in projects])
    if not corpus:
        return {"query": cleaned, "results": [], "stale_sources": stale,
                "changed_sources": [], "limited": limited, "model": MODEL,
                "checked_passages": checked_passages, "checked_memories": 0,
                "checked_projects": 0, "document_scope": scope,
                "counts": {kind: 0 for kind in ("document", "memory", "project")}}
    vectors = embed_texts(ollama_url, [cleaned] + [body[:1200] for _, _, body in corpus])
    question = vectors[0]
    ranked_results = []
    for (kind, row, body), vector in zip(corpus, vectors[1:], strict=True):
        dot = sum(a * b for a, b in zip(question, vector, strict=True))
        norm = math.sqrt(sum(x * x for x in question) * sum(x * x for x in vector))
        ranked_results.append((dot / norm, kind, row, body))
    ranked_results.sort(key=lambda item: (-item[0], item[1], item[2]["id"]
                       if item[1] != "document" else item[2]["path"]))
    selected = []
    changed_sources = []
    seen_documents: set[str] = set()
    counts = {kind: 0 for kind in ("document", "memory", "project")}
    for score, kind, row, body in ranked_results:
        result_limit = MAX_DEEP_RESULTS if document_id and kind == "document" else MAX_RESULTS
        if counts[kind] == result_limit:
            continue
        if kind == "document":
            if not document_id and row["document_id"] in seen_documents:
                continue
            seen_documents.add(row["document_id"])
            if not DocumentStore.source_is_current(row):
                if not any(item["document_id"] == row["document_id"] for item in stale):
                    if len(stale) < MAX_RESULTS:
                        stale.append({"document_id": row["document_id"], "path": row["path"],
                                      "index_sha256": row["sha256"], "status": "needs_reindex"})
                    else:
                        limited = True
                continue
            selected.append({"kind": kind, "id": row["document_id"],
                             "document_id": row["document_id"],
                             "title": Path(row["path"]).name, "source": row["path"],
                             "excerpt": body[:1200 if document_id else 500],
                             "passage_id": row["passage_id"],
                             "page": row["page"], "sha256": row["sha256"],
                             "policy": row["policy"], "source_status": "current",
                             "similarity": round(score, 3)})
        else:
            if not _record_is_current(db, kind, row):
                if len(changed_sources) < MAX_RESULTS:
                    changed_sources.append({"kind": kind, "id": row["id"]})
                else:
                    limited = True
                continue
            if kind == "memory":
                selected.append({"kind": kind, "id": row["id"], "title": "Erinnerung",
                                 "source": row["source_ref"], "excerpt": row["text"][:500],
                                 "policy": row["policy"], "similarity": round(score, 3)})
            else:
                selected.append({"kind": kind, "id": row["id"], "title": row["project"],
                                 "source": f'Arbeitsstand v{row["version"]}',
                                 "excerpt": _project_body(row)[:500],
                                 "version": row["version"], "similarity": round(score, 3)})
        counts[kind] += 1
    limited = (limited or (len(fresh) > MAX_DEEP_RESULTS if document_id else
                          len({row["document_id"] for _, kind, row, _ in ranked_results
                               if kind == "document"}) > MAX_RESULTS)
               or len(memories) > MAX_RESULTS or len(projects) > MAX_RESULTS)
    return {"query": cleaned, "results": selected, "stale_sources": stale,
            "changed_sources": changed_sources, "limited": limited,
            "model": MODEL, "checked_passages": checked_passages,
            "checked_memories": len(memories), "checked_projects": len(projects),
            "counts": counts, "document_scope": scope}
