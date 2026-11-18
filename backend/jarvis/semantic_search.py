"""Optional, bounded local semantic retrieval from freshly verified document passages."""

import math
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from jarvis.db import Database
from jarvis.documents import DocumentStore

MODEL = "embeddinggemma:300m"
MAX_PASSAGES = 100
MAX_DOCUMENTS = 30
MAX_RESULTS = 10


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


def search(db: Database, query: str, ollama_url: str, folder_id: str | None = None) -> dict:
    cleaned = " ".join(query.split())
    if not 2 <= len(cleaned) <= 160:
        raise ValueError("Suchfrage muss 2–160 Zeichen enthalten")
    with db.connect() as connection:
        if folder_id and not connection.execute(
                "SELECT 1 FROM folders WHERE id=?", (folder_id,)).fetchone():
            raise ValueError("Freigabeordner nicht gefunden")
        rows = [dict(row) for row in connection.execute("""
            WITH ranked AS (
                SELECT p.id AS passage_id,p.document_id,p.page,p.text,d.path,d.policy,
                       d.sha256,d.size,d.status,d.excluded,f.path AS folder_path,
                       ROW_NUMBER() OVER (PARTITION BY d.id ORDER BY p.ordinal,p.id) AS part
                FROM passages p JOIN documents d ON d.id=p.document_id
                JOIN folders f ON f.id=d.folder_id
                WHERE d.excluded=0 AND d.status='indexed'
                  AND (? IS NULL OR d.folder_id=?)
            )
            SELECT * FROM ranked WHERE part<=3 ORDER BY path,part,passage_id LIMIT ?
        """, (folder_id, folder_id, MAX_PASSAGES + 1))]
    limited = len(rows) > MAX_PASSAGES
    freshness: dict[str, bool] = {}
    fresh = []
    stale = []
    checked_passages = 0
    for row in rows[:MAX_PASSAGES]:
        document_id = row["document_id"]
        if document_id not in freshness:
            if len(freshness) == MAX_DOCUMENTS:
                limited = True
                break
            freshness[document_id] = DocumentStore.source_is_current(row)
            if not freshness[document_id]:
                if len(stale) < MAX_RESULTS:
                    stale.append({"document_id": document_id, "path": row["path"],
                                  "index_sha256": row["sha256"], "status": "needs_reindex"})
                else:
                    limited = True
        checked_passages += 1
        if freshness[document_id]:
            fresh.append(row)
    if not fresh:
        return {"query": cleaned, "results": [], "stale_sources": stale,
                "limited": limited, "model": MODEL, "checked_passages": checked_passages}
    vectors = embed_texts(ollama_url, [cleaned] + [row["text"][:1200] for row in fresh])
    question = vectors[0]
    ranked_results = []
    for row, vector in zip(fresh, vectors[1:], strict=True):
        dot = sum(a * b for a, b in zip(question, vector, strict=True))
        norm = math.sqrt(sum(x * x for x in question) * sum(x * x for x in vector))
        ranked_results.append((dot / norm, row))
    ranked_results.sort(key=lambda item: (-item[0], item[1]["path"], item[1]["passage_id"]))
    selected = []
    seen: set[str] = set()
    for score, row in ranked_results:
        if row["document_id"] in seen:
            continue
        seen.add(row["document_id"])
        if not DocumentStore.source_is_current(row):
            if not any(item["document_id"] == row["document_id"] for item in stale):
                if len(stale) < MAX_RESULTS:
                    stale.append({"document_id": row["document_id"], "path": row["path"],
                                  "index_sha256": row["sha256"], "status": "needs_reindex"})
                else:
                    limited = True
            continue
        selected.append({"kind": "document", "id": row["document_id"],
                         "document_id": row["document_id"], "title": Path(row["path"]).name,
                         "source": row["path"], "excerpt": row["text"][:500],
                         "passage_id": row["passage_id"], "page": row["page"],
                         "sha256": row["sha256"], "policy": row["policy"],
                         "source_status": "current", "similarity": round(score, 3)})
        if len(selected) == MAX_RESULTS:
            break
    return {"query": cleaned, "results": selected, "stale_sources": stale,
            "limited": limited or len({row["document_id"] for _, row in ranked_results}) > MAX_RESULTS,
            "model": MODEL, "checked_passages": checked_passages}
