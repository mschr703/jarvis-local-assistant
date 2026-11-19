"""Bounded read-only search across granted passages and confirmed local records."""

import re
import unicodedata
from datetime import date
from pathlib import Path

from jarvis.db import Database
from jarvis.documents import DocumentStore

MAX_CANDIDATES = 100
MAX_FRESHNESS_CHECKS = 30
MAX_PROJECT_ROWS = 2000
MAX_PROJECT_PERSON_ROWS = 2000
MAX_PROJECT_ALIAS_ROWS = 2000
MAX_PROJECT_TASK_ROWS = 2000
MAX_PROJECT_FILE_ROWS = 2000
MAX_EVENT_ROWS = 2000
MAX_CONTACT_ROWS = 2000
MAX_CONTACT_DETAILS = 5
MAX_PER_KIND = 10
SOURCE_KINDS = frozenset({"all", "document", "memory", "project", "life_event", "contact"})
STOPWORDS = frozenset({"was", "wer", "wie", "wo", "wann", "welche", "welcher", "welches",
                       "ist", "sind", "hat", "haben", "ich", "mir", "mein", "meine", "zu",
                       "zur", "zum", "von", "für", "mit", "über", "und", "oder", "der",
                       "die", "das", "den", "dem", "des", "ein", "eine", "einen", "im", "in",
                       "am", "an", "aus", "bei", "nach", "bekannt"})
PROJECT_RELATIONS = {"lead": "Leitung", "contributor": "Mitwirkung",
                     "contact": "Kontakt", "reviewer": "Prüfung", "other": "Andere Beziehung"}


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


def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value).casefold()
                   if not unicodedata.combining(char))


def validate_event_range(kind: str, from_date: str | None,
                         to_date: str | None) -> tuple[str | None, str | None]:
    if (from_date or to_date) and kind not in {"all", "life_event"}:
        raise ValueError("Datumsbereich gilt nur für Lebensereignisse")
    for value in (from_date, to_date):
        if value is None:
            continue
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Ereignisdatum braucht das Format JJJJ-MM-TT")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Ungültiges Ereignisdatum") from exc
    if from_date and to_date and from_date > to_date:
        raise ValueError("Von-Datum muss vor dem Bis-Datum liegen")
    return from_date, to_date


def search(db: Database, query: str, kind: str = "all",
           from_date: str | None = None, to_date: str | None = None) -> dict:
    from jarvis.source_preferences import SourcePreferences

    if kind not in SOURCE_KINDS:
        raise ValueError("Unbekannter Such-Quelltyp")
    from_date, to_date = validate_event_range(kind, from_date, to_date)
    cleaned, terms = query_terms(query)
    preferred = (SourcePreferences(db).list(cleaned)["preferences"]
                 if kind in {"all", "document"} else [])
    match = " OR ".join(f'"{term}"*' for term in terms)
    with db.connect() as connection:
        connection.create_function("unicode_casefold", 1, str.casefold, deterministic=True)
        passages = [dict(row) for row in connection.execute("""
            SELECT p.id AS passage_id,p.document_id,p.page,p.text,d.path,d.policy,d.sha256,
                   d.size,d.status,d.excluded,f.path AS folder_path
            FROM passages_fts JOIN passages p ON p.id=passages_fts.rowid
            JOIN documents d ON d.id=p.document_id JOIN folders f ON f.id=d.folder_id
            WHERE passages_fts MATCH ? AND d.excluded=0 AND d.status='indexed'
            ORDER BY bm25(passages_fts),p.id LIMIT ?
        """, (match, MAX_CANDIDATES + 1))] if kind in {"all", "document"} else []
        path_conditions = " OR ".join("unicode_casefold(d.path) LIKE ? ESCAPE '\\'"
                                      for _ in terms)
        paths = [dict(row) for row in connection.execute(f"""
            SELECT p.id AS passage_id,d.id AS document_id,p.page,p.text,
                   d.path,d.policy,d.sha256,d.size,d.status,d.excluded,
                   f.path AS folder_path
            FROM documents d JOIN folders f ON f.id=d.folder_id
            LEFT JOIN passages p ON p.id=(SELECT id FROM passages
                WHERE document_id=d.id ORDER BY ordinal,id LIMIT 1)
            WHERE d.excluded=0 AND d.status='indexed' AND ({path_conditions})
            ORDER BY d.path,d.id LIMIT ?
        """, (*[f"%{term.replace('_', r'\_')}%" for term in terms], MAX_CANDIDATES + 1))] if kind in {
            "all", "document"} else []
        memories = [dict(row) for row in connection.execute("""
            SELECT m.id,m.text,m.source_ref,m.policy,m.updated_at
            FROM memories_fts JOIN memories m ON m.id=memories_fts.id
            WHERE memories_fts MATCH ?
            ORDER BY bm25(memories_fts),m.id LIMIT ?
        """, (match, MAX_CANDIDATES + 1))] if kind in {"all", "memory"} else []
        projects = [dict(row) for row in connection.execute("""
            SELECT id,project,confirmed,open_questions,next_step,version,updated_at
            FROM work_states ORDER BY updated_at DESC,id DESC LIMIT ?
        """, (MAX_PROJECT_ROWS + 1,))] if kind in {"all", "project"} else []
        project_people = [dict(row) for row in connection.execute("""
            SELECT p.id,p.state_id,p.name,p.relation_type,p.source_note,
                   p.source_document_id
            FROM project_people p JOIN
                (SELECT id FROM work_states ORDER BY updated_at DESC,id DESC LIMIT ?) s
                ON s.id=p.state_id
            ORDER BY p.updated_at DESC,p.id DESC LIMIT ?
        """, (MAX_PROJECT_ROWS, MAX_PROJECT_PERSON_ROWS + 1))] if kind in {
            "all", "project"} else []
        project_aliases = [dict(row) for row in connection.execute("""
            SELECT a.id,a.state_id,a.alias,a.source_note,a.source_document_id,
                   p.name AS person_name
            FROM project_person_aliases a
            JOIN project_people p ON p.id=a.person_id AND p.state_id=a.state_id
            JOIN (SELECT id FROM work_states ORDER BY updated_at DESC,id DESC LIMIT ?) s
                ON s.id=a.state_id
            ORDER BY a.updated_at DESC,a.id DESC LIMIT ?
        """, (MAX_PROJECT_ROWS, MAX_PROJECT_ALIAS_ROWS + 1))] if kind in {
            "all", "project"} else []
        project_tasks = [dict(row) for row in connection.execute("""
            SELECT s.id AS state_id,json_extract(link.value,'$.id') AS task_id,
                   json_extract(link.value,'$.title') AS saved_title,
                   t.title AS current_title,t.status AS current_status
            FROM (SELECT id,tasks_json,updated_at FROM work_states
                  ORDER BY updated_at DESC,id DESC LIMIT ?) s
            JOIN json_each(s.tasks_json) link
            LEFT JOIN tasks t ON t.id=json_extract(link.value,'$.id')
            ORDER BY s.updated_at DESC,s.id DESC,CAST(link.key AS INTEGER) LIMIT ?
        """, (MAX_PROJECT_ROWS, MAX_PROJECT_TASK_ROWS + 1))] if kind in {
            "all", "project"} else []
        project_files = [dict(row) for row in connection.execute("""
            SELECT s.id AS state_id,json_extract(link.value,'$.path') AS saved_path
            FROM (SELECT id,documents_json,updated_at FROM work_states
                  ORDER BY updated_at DESC,id DESC LIMIT ?) s
            JOIN json_each(s.documents_json) link
            ORDER BY s.updated_at DESC,s.id DESC,CAST(link.key AS INTEGER) LIMIT ?
        """, (MAX_PROJECT_ROWS, MAX_PROJECT_FILE_ROWS + 1))] if kind in {
            "all", "project"} else []
        events = [dict(row) for row in connection.execute("""
            SELECT e.id,e.date_local,e.title,e.description,e.location,e.version,
                   (SELECT GROUP_CONCAT(c.name||' ('||c.context||')',' | ')
                    FROM memory_event_contacts l JOIN contacts c ON c.id=l.contact_id
                    WHERE l.event_id=e.id) AS contact_names,
                   GROUP_CONCAT(a.name,' | ') AS photo_names
            FROM memory_events e LEFT JOIN photo_assets a ON a.event_id=e.id
            WHERE (? IS NULL OR e.date_local>=?) AND (? IS NULL OR e.date_local<=?)
            GROUP BY e.id ORDER BY e.date_local DESC,e.id DESC LIMIT ?
        """, (from_date, from_date, to_date, to_date,
              MAX_EVENT_ROWS + 1))] if kind in {"all", "life_event"} else []
        contacts = [dict(row) for row in connection.execute("""
            SELECT id,name,context,relationship,version FROM contacts
            ORDER BY updated_at DESC,id DESC LIMIT ?
        """, (MAX_CONTACT_ROWS + 1,))] if kind in {"all", "contact"} else []
        contact_details: dict[str, list[tuple[str, str]]] = {}
        contact_details_limited = False
        if contacts:
            sources = (
                ("Notiz", ("SELECT contact_id,content AS detail,updated_at,id "
                           "FROM contact_notes")),
                ("Vorhaben", ("SELECT contact_id,title||' · '||details AS detail,"
                              "updated_at,id FROM contact_plans")),
                ("Ereignis", ("SELECT l.contact_id,e.date_local||' · '||e.title||' · '||"
                              "e.location AS detail,e.updated_at,l.event_id AS id "
                              "FROM memory_event_contacts l JOIN memory_events e "
                              "ON e.id=l.event_id")),
            )
            for label, query_sql in sources:
                rows = connection.execute(f"""
                    WITH candidates AS (SELECT id FROM contacts
                        ORDER BY updated_at DESC,id DESC LIMIT ?),
                    ranked AS (SELECT detail,source.contact_id,
                        ROW_NUMBER() OVER (PARTITION BY source.contact_id
                            ORDER BY source.updated_at DESC,source.id DESC) AS rank
                        FROM ({query_sql}) source
                        JOIN candidates c ON c.id=source.contact_id)
                    SELECT contact_id,detail,rank FROM ranked WHERE rank<=?
                """, (MAX_CONTACT_ROWS, MAX_CONTACT_DETAILS + 1))
                for row in rows:
                    if row["rank"] > MAX_CONTACT_DETAILS:
                        contact_details_limited = True
                        continue
                    contact_details.setdefault(row["contact_id"], []).append(
                        (label, row["detail"]))

    limited = (len(passages) > MAX_CANDIDATES or len(paths) > MAX_CANDIDATES
               or len(memories) > MAX_CANDIDATES or len(projects) > MAX_PROJECT_ROWS
               or len(project_people) > MAX_PROJECT_PERSON_ROWS
               or len(project_aliases) > MAX_PROJECT_ALIAS_ROWS
               or len(project_tasks) > MAX_PROJECT_TASK_ROWS
               or len(project_files) > MAX_PROJECT_FILE_ROWS
               or len(events) > MAX_EVENT_ROWS or len(contacts) > MAX_CONTACT_ROWS
               or contact_details_limited)
    by_document: dict[str, dict] = {}
    for row in passages[:MAX_CANDIDATES] + paths[:MAX_CANDIDATES]:
        title = Path(row["path"]).name
        body = row["text"] or ""
        item = {"kind": "document", "id": row["document_id"],
                "title": title, "excerpt": body[:500], "source": row["path"],
                "document_id": row["document_id"], "passage_id": row["passage_id"],
                "page": row["page"], "sha256": row["sha256"],
                "policy": row["policy"],
                "path": row["path"], "size": row["size"], "status": row["status"],
                "excluded": row["excluded"], "folder_path": row["folder_path"],
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
            prior["_verified_preference"] = True
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
            "_verified_preference": True,
        }

    ranked_documents = sorted(by_document.values(),
                              key=lambda item: (-item["score"], item["title"].casefold(), item["id"]))
    limited = limited or len(ranked_documents) > MAX_FRESHNESS_CHECKS
    current_documents = []
    stale_sources = []
    stale_count = 0
    for item in ranked_documents[:MAX_FRESHNESS_CHECKS]:
        current = item.pop("_verified_preference", False) or DocumentStore.source_is_current(item)
        if current:
            item.setdefault("source_status", "current")
            for internal in ("path", "size", "status", "excluded", "folder_path"):
                item.pop(internal, None)
            current_documents.append(item)
        else:
            stale_count += 1
            if len(stale_sources) < MAX_PER_KIND:
                stale_sources.append({"document_id": item["document_id"],
                                      "path": item["source"], "index_sha256": item["sha256"],
                                      "status": "needs_reindex"})
    limited = limited or stale_count > MAX_PER_KIND

    memory_items = [{"kind": "memory", "id": row["id"], "title": "Erinnerung",
                     "excerpt": row["text"][:500], "source": row["source_ref"],
                     "policy": row["policy"],
                     "score": _score(cleaned, terms, row["source_ref"], row["text"])}
                    for row in memories[:MAX_CANDIDATES]]
    folded_query = _fold(cleaned)
    folded_terms = [_fold(term) for term in terms]
    project_items = []
    people_by_project: dict[str, list[dict]] = {}
    for person in project_people[:MAX_PROJECT_PERSON_ROWS]:
        people_by_project.setdefault(person["state_id"], []).append(person)
    aliases_by_project: dict[str, list[dict]] = {}
    for alias in project_aliases[:MAX_PROJECT_ALIAS_ROWS]:
        aliases_by_project.setdefault(alias["state_id"], []).append(alias)
    tasks_by_project: dict[str, list[dict]] = {}
    for task in project_tasks[:MAX_PROJECT_TASK_ROWS]:
        tasks_by_project.setdefault(task["state_id"], []).append(task)
    files_by_project: dict[str, list[str]] = {}
    for file in project_files[:MAX_PROJECT_FILE_ROWS]:
        files_by_project.setdefault(file["state_id"], []).append(
            Path(file["saved_path"]).name)
    for row in projects[:MAX_PROJECT_ROWS]:
        body = " · ".join(part for part in
                          (row["confirmed"], row["open_questions"], row["next_step"]) if part)
        score = _score(folded_query, folded_terms, _fold(row["project"]), _fold(body))
        matching_people = []
        for person in people_by_project.get(row["id"], []):
            role = PROJECT_RELATIONS[person["relation_type"]]
            person_body = f"{role} · {person['source_note']}"
            person_score = _score(folded_query, folded_terms,
                                  _fold(person["name"]), _fold(person_body))
            if person_score:
                matching_people.append((person_score, person, role))
        matching_people.sort(key=lambda item: (-item[0], item[1]["name"].casefold(), item[1]["id"]))
        if matching_people:
            score += matching_people[0][0]
        matching_aliases = []
        for alias in aliases_by_project.get(row["id"], []):
            alias_score = _score(folded_query, folded_terms,
                                 _fold(alias["alias"]), _fold(alias["source_note"]))
            if alias_score:
                matching_aliases.append((alias_score, alias))
        matching_aliases.sort(key=lambda item: (-item[0], item[1]["alias"].casefold(),
                                               item[1]["id"]))
        if matching_aliases:
            score += matching_aliases[0][0]
        matching_tasks = []
        for task in tasks_by_project.get(row["id"], []):
            task_title = task["current_title"] or task["saved_title"] or ""
            task_score = _score(folded_query, folded_terms, _fold(task_title),
                                _fold(task["saved_title"] or ""))
            if task_score:
                matching_tasks.append((task_score, task))
        matching_tasks.sort(key=lambda item: (-item[0],
                                              (item[1]["current_title"] or
                                               item[1]["saved_title"] or "").casefold(),
                                              item[1]["task_id"]))
        if matching_tasks:
            score += matching_tasks[0][0]
        matching_files = []
        for filename in files_by_project.get(row["id"], []):
            file_score = _score(folded_query, folded_terms, _fold(filename), "")
            if file_score:
                matching_files.append((file_score, filename))
        matching_files.sort(key=lambda item: (-item[0], item[1].casefold()))
        if matching_files:
            score += matching_files[0][0]
        if score:
            file_excerpts = [f"Projektdatei: {filename} · Original und Freigabe"
                             " im Arbeitsstand prüfen"
                             for _, filename in matching_files[:2]]
            task_excerpts = []
            for _, task in matching_tasks[:2]:
                if task["current_title"] is None:
                    task_excerpts.append(f"Projektaufgabe: {task['saved_title']} · gelöscht")
                elif task["current_title"] != task["saved_title"]:
                    task_excerpts.append(f"Projektaufgabe: {task['current_title']}"
                                         f" (damals: {task['saved_title']})"
                                         f" · {'erledigt' if task['current_status'] == 'done' else 'offen'}")
                else:
                    task_excerpts.append(f"Projektaufgabe: {task['current_title']}"
                                         f" · {'erledigt' if task['current_status'] == 'done' else 'offen'}")
            alias_excerpts = [f"Personenalias: {alias['alias']} → {alias['person_name']}"
                              f" · {alias['source_note']}"
                              + (" · Dateibeleg im Arbeitsstand prüfen"
                                 if alias["source_document_id"] else " · eigene Angabe")
                              for _, alias in matching_aliases[:2]]
            person_excerpts = [f"Projektperson: {person['name']} · {role} · {person['source_note']}"
                               + (" · Dateibeleg im Arbeitsstand prüfen"
                                  if person["source_document_id"] else " · eigene Angabe")
                               for _, person, role in matching_people[:2]]
            project_items.append({"kind": "project", "id": row["id"],
                                  "title": row["project"],
                                  "excerpt": " · ".join([*file_excerpts, *task_excerpts,
                                                         *alias_excerpts,
                                                         *person_excerpts, body])[:500],
                                  "source": f"Arbeitsstand v{row['version']}",
                                  "version": row["version"], "score": score})

    event_items = []
    for row in events[:MAX_EVENT_ROWS]:
        photo_names = (row["photo_names"] or "").split(" | ") if row["photo_names"] else []
        body = " · ".join(part for part in (row["date_local"], row["location"],
                                                row["description"], row["photo_names"],
                                                row["contact_names"]) if part)
        score = _score(folded_query, folded_terms, _fold(row["title"]), _fold(body))
        if score:
            matching_photos = [name for name in photo_names if any(term in _fold(name)
                                                                    for term in folded_terms)][:3]
            details = [row["description"][:350]] if row["description"] else []
            if row["location"]:
                details.append(f"Ort: {row['location']}")
            if matching_photos:
                details.append(f"Foto: {', '.join(matching_photos)}")
            if row["contact_names"]:
                matching_contacts = [person for person in row["contact_names"].split(" | ")
                                     if any(term in _fold(person) for term in folded_terms)][:3]
                if matching_contacts:
                    details.append(f"Kontakt: {', '.join(matching_contacts)}")
            event_items.append({"kind": "life_event", "id": row["id"],
                                "title": row["title"], "excerpt": " · ".join(details)[:500],
                                "source": f"{row['date_local']} · manuell bestätigt",
                                "date_local": row["date_local"], "version": row["version"],
                                "score": score})

    contact_items = []
    for row in contacts[:MAX_CONTACT_ROWS]:
        details = contact_details.get(row["id"], [])
        title = f"{row['name']} · {row['context']}"
        body = " · ".join([row["relationship"], *(detail for _, detail in details)])
        score = _score(folded_query, folded_terms, _fold(title), _fold(body))
        if score:
            matching = [f"{label}: {detail}" for label, detail in details
                        if any(term in _fold(detail) for term in folded_terms)][:2]
            excerpt = " · ".join(part for part in [row["relationship"], *matching] if part)
            contact_items.append({"kind": "contact", "id": row["id"],
                                  "title": title, "excerpt": excerpt[:500],
                                  "source": "Kontakt · manuell bestätigt",
                                  "version": row["version"], "score": score})

    groups = [current_documents, memory_items, project_items, event_items, contact_items]
    selected = []
    for group in groups:
        group.sort(key=lambda item: (-item["score"], item["title"].casefold(), item["id"]))
        limited = limited or len(group) > MAX_PER_KIND
        selected.extend(group[:MAX_PER_KIND])
    selected.sort(key=lambda item: (-item["score"], item["kind"], item["title"].casefold(),
                                    item["id"]))
    return {"query": cleaned, "event_from_date": from_date,
            "event_to_date": to_date, "results": selected, "limited": limited,
            "stale_sources": stale_sources,
            "preferences": preferred,
            "counts": {kind: sum(item["kind"] == kind for item in selected)
                       for kind in ("document", "memory", "project", "life_event", "contact")}}
