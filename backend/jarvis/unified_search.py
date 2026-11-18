"""Bounded read-only search across granted document passages, memories and work states."""

import re
from pathlib import Path

from jarvis.db import Database

MAX_CANDIDATES = 100
MAX_PROJECT_ROWS = 2000
MAX_PER_KIND = 10
STOPWORDS = frozenset({"was", "wer", "wie", "wo", "wann", "welche", "welcher", "welches",
                       "ist", "sind", "hat", "haben", "ich", "mir", "mein", "meine", "zu",
                       "zur", "zum", "von", "für", "mit", "über", "und", "oder", "der",
                       "die", "das", "den", "dem", "des", "ein", "eine", "einen", "im", "in",
                       "am", "an", "aus", "bei", "nach", "bekannt"})


def query_terms(query: str) -> tuple[str, list[str]]:
    cleaned = " ".join(query.split())
    if not 2 <= len(cleaned) <= 160:
        raise ValueError("Suchfrage muss 2–160 Zeichen enthalten")
    terms = list(dict.fromkeys(word.casefold() for word in re.findall(r"\w+", cleaned)
                               if len(word) >= 2 and word.casefold() not in STOPWORDS))[:8]
    if not terms:
        raise ValueError("Suchfrage enthält kein suchbares Stichwort")
    return cleaned, terms


def _score(query: str, terms: list[str], title: str, body: str) -> int:
    title_fold, body_fold = title.casefold(), body.casefold()
    query_fold = query.casefold()
    return (20 * (query_fold in title_fold) + 8 * (query_fold in body_fold)
            + sum(4 * (term in title_fold) + 2 * (term in body_fold) for term in terms))


def search(db: Database, query: str) -> dict:
    from jarvis.source_preferences import SourcePreferences

    cleaned, terms = query_terms(query)
    preferred = SourcePreferences(db).list(cleaned)["preferences"]
    match = " OR ".join(f'"{term}"*' for term in terms)
    with db.connect() as connection:
        connection.create_function("unicode_casefold", 1, str.casefold, deterministic=True)
        passages = [dict(row) for row in connection.execute("""
            SELECT p.id AS passage_id,p.document_id,p.page,p.text,d.path,d.policy,d.sha256
            FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid
            JOIN documents d ON d.id=p.document_id JOIN folders f ON f.id=d.folder_id
            WHERE passages_fts MATCH ? AND d.excluded=0 AND d.status='indexed'
            ORDER BY bm25(passages_fts),p.id LIMIT ?
        """, (match, MAX_CANDIDATES + 1))]
        path_conditions = " OR ".join("unicode_casefold(d.path) LIKE ? ESCAPE '\\'"
                                      for _ in terms)
        paths = [dict(row) for row in connection.execute(f"""
            SELECT p.id AS passage_id,d.id AS document_id,p.page,p.text,
                   d.path,d.policy,d.sha256
            FROM documents d JOIN folders f ON f.id=d.folder_id
            LEFT JOIN passages p ON p.id=(SELECT id FROM passages
                WHERE document_id=d.id ORDER BY ordinal,id LIMIT 1)
            WHERE d.excluded=0 AND d.status='indexed' AND ({path_conditions})
            ORDER BY d.path,d.id LIMIT ?
        """, (*[f"%{term.replace('_', r'\_')}%" for term in terms], MAX_CANDIDATES + 1))]
        memories = [dict(row) for row in connection.execute("""
            SELECT m.id,m.text,m.source_ref,m.policy,m.updated_at
            FROM memories_fts JOIN memories m ON m.id=memories_fts.id
            WHERE memories_fts MATCH ?
            ORDER BY bm25(memories_fts),m.id LIMIT ?
        """, (match, MAX_CANDIDATES + 1))]
        projects = [dict(row) for row in connection.execute("""
            SELECT id,project,confirmed,open_questions,next_step,version,updated_at
            FROM work_states ORDER BY updated_at DESC,id DESC LIMIT ?
        """, (MAX_PROJECT_ROWS + 1,))]

    limited = (len(passages) > MAX_CANDIDATES or len(paths) > MAX_CANDIDATES
               or len(memories) > MAX_CANDIDATES or len(projects) > MAX_PROJECT_ROWS)
    by_document: dict[str, dict] = {}
    for row in passages[:MAX_CANDIDATES] + paths[:MAX_CANDIDATES]:
        title = Path(row["path"]).name
        body = row["text"] or ""
        item = {"kind": "document", "id": row["document_id"],
                "title": title, "excerpt": body[:500], "source": row["path"],
                "document_id": row["document_id"], "passage_id": row["passage_id"],
                "page": row["page"], "sha256": row["sha256"],
                "policy": row["policy"],
                "score": _score(cleaned, terms, title, body)}
        prior = by_document.get(row["document_id"])
        if prior is None or item["score"] > prior["score"]:
            by_document[row["document_id"]] = item

    for preference in preferred:
        if preference["status"] not in {"current", "updated"}:
            by_document.pop(preference["document_id"], None)
            continue
        document_id = preference["document_id"]
        prior = by_document.get(document_id)
        if prior is not None:
            prior["preferred"] = True
            prior["source_status"] = preference["status"]
            prior["score"] += 1000
            continue
        by_document[document_id] = {
            "kind": "document", "id": document_id,
            "title": Path(preference["path"]).name,
            "excerpt": preference.get("excerpt", ""), "source": preference["path"],
            "document_id": document_id, "passage_id": preference.get("passage_id"),
            "page": preference.get("page"), "sha256": preference["current_sha256"],
            "policy": preference["policy"], "score": 1000,
            "preferred": True, "source_status": preference["status"],
        }

    memory_items = [{"kind": "memory", "id": row["id"], "title": "Erinnerung",
                     "excerpt": row["text"][:500], "source": row["source_ref"],
                     "policy": row["policy"],
                     "score": _score(cleaned, terms, row["source_ref"], row["text"])}
                    for row in memories[:MAX_CANDIDATES]]
    project_items = []
    for row in projects[:MAX_PROJECT_ROWS]:
        body = " · ".join(part for part in
                          (row["confirmed"], row["open_questions"], row["next_step"]) if part)
        score = _score(cleaned, terms, row["project"], body)
        if score:
            project_items.append({"kind": "project", "id": row["id"],
                                  "title": row["project"], "excerpt": body[:500],
                                  "source": f"Arbeitsstand v{row['version']}",
                                  "version": row["version"], "score": score})

    groups = [list(by_document.values()), memory_items, project_items]
    selected = []
    for group in groups:
        group.sort(key=lambda item: (-item["score"], item["title"].casefold(), item["id"]))
        limited = limited or len(group) > MAX_PER_KIND
        selected.extend(group[:MAX_PER_KIND])
    selected.sort(key=lambda item: (-item["score"], item["kind"], item["title"].casefold(),
                                    item["id"]))
    return {"query": cleaned, "results": selected, "limited": limited,
            "preferences": preferred,
            "counts": {kind: sum(item["kind"] == kind for item in selected)
                       for kind in ("document", "memory", "project")}}
