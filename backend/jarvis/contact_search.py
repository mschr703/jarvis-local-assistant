"""Bounded, optional local semantic retrieval over manually entered contacts."""

import math

from jarvis.db import Database
from jarvis.semantic_cache import PersistentEmbeddingCache
from jarvis.semantic_search import MODEL, embed_texts

MAX_CONTACTS = 30
MAX_RESULTS = 10
MAX_NOTES = 3
MAX_PLANS = 3
MAX_EVENTS = 3


def _snapshot(db: Database, contact_id: str) -> dict | None:
    with db.connect() as connection:
        contact = connection.execute(
            "SELECT id,name,context,relationship,version,updated_at FROM contacts WHERE id=?",
            (contact_id,)).fetchone()
        if contact is None:
            return None
        notes = connection.execute(
            "SELECT id,content,version,updated_at FROM contact_notes WHERE contact_id=? "
            "ORDER BY updated_at DESC,id DESC LIMIT ?", (contact_id, MAX_NOTES)).fetchall()
        plans = connection.execute(
            "SELECT id,title,details,status,version,updated_at FROM contact_plans "
            "WHERE contact_id=? ORDER BY CASE status WHEN 'open' THEN 0 ELSE 1 END, "
            "updated_at DESC,id DESC LIMIT ?", (contact_id, MAX_PLANS)).fetchall()
        events = connection.execute(
            "SELECT e.id,e.date_local,e.title,e.location,e.description,e.version,"
            "e.updated_at,l.created_at AS linked_at FROM memory_event_contacts l "
            "JOIN memory_events e ON e.id=l.event_id WHERE l.contact_id=? "
            "ORDER BY e.updated_at DESC,e.id DESC LIMIT ?",
            (contact_id, MAX_EVENTS)).fetchall()
    details = [f"Kontakt: {contact['name']} ({contact['context']})"]
    if contact["relationship"]:
        details.append(f"Beziehung: {contact['relationship']}")
    details.extend(f"Notiz: {note['content']}"[:130] for note in notes)
    details.extend((f"Vorhaben ({'offen' if plan['status'] == 'open' else 'erledigt'}): "
                    f"{plan['title']} · {plan['details']}")[:130] for plan in plans)
    details.extend((f"Bestätigtes Ereignis {event['date_local']} in "
                    f"{event['location'] or 'unbekannt'}: {event['title']} · "
                    f"{event['description']}")[:160] for event in events)
    fingerprint = (tuple(contact), tuple(tuple(note) for note in notes),
                   tuple(tuple(plan) for plan in plans),
                   tuple(tuple(event) for event in events))
    return {"id": contact_id, "name": contact["name"], "context": contact["context"],
            "body": "\n".join(details)[:1700], "fingerprint": fingerprint}


def search(db: Database, query: str, ollama_url: str) -> dict:
    cleaned = " ".join(query.split())
    if not 2 <= len(cleaned) <= 160:
        raise ValueError("Suchfrage muss 2–160 Zeichen enthalten")
    with db.connect() as connection:
        identifiers = [row["id"] for row in connection.execute("""
            SELECT c.id,MAX(c.updated_at,
                COALESCE((SELECT MAX(n.updated_at) FROM contact_notes n
                          WHERE n.contact_id=c.id),''),
                COALESCE((SELECT MAX(p.updated_at) FROM contact_plans p
                          WHERE p.contact_id=c.id),''),
                COALESCE((SELECT MAX(l.created_at) FROM memory_event_contacts l
                          WHERE l.contact_id=c.id),''),
                COALESCE((SELECT MAX(e.updated_at) FROM memory_event_contacts l
                          JOIN memory_events e ON e.id=l.event_id
                          WHERE l.contact_id=c.id),'')) AS last_change
            FROM contacts c ORDER BY last_change DESC,c.id DESC LIMIT ?
        """, (MAX_CONTACTS + 1,))]
    limited = len(identifiers) > MAX_CONTACTS
    candidates = [item for identifier in identifiers[:MAX_CONTACTS]
                  if (item := _snapshot(db, identifier)) is not None]
    if not candidates:
        return {"query": cleaned, "model": MODEL, "checked_contacts": 0,
                "limited": limited, "changed_contacts": [], "results": []}
    vectors = embed_texts(ollama_url, [cleaned] + [item["body"] for item in candidates],
                          db.path.parent / "semantic-embeddings.sqlite")
    question = vectors[0]
    ranked = []
    for item, vector in zip(candidates, vectors[1:], strict=True):
        dot = sum(a * b for a, b in zip(question, vector, strict=True))
        norm = math.sqrt(sum(value * value for value in question)
                         * sum(value * value for value in vector))
        ranked.append((dot / norm, item))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]["name"].casefold(),
                                  pair[1]["context"].casefold(), pair[1]["id"]))
    selected = []
    changed = []
    for score, item in ranked:
        if len(selected) >= MAX_RESULTS:
            limited = True
            break
        current = _snapshot(db, item["id"])
        if current is None or current["fingerprint"] != item["fingerprint"]:
            changed.append(item["id"])
            continue
        selected.append({"id": item["id"], "name": item["name"],
                         "context": item["context"], "excerpt": item["body"][:500],
                         "source": "manuell bestätigt", "similarity": round(score, 3)})
    if changed:
        PersistentEmbeddingCache(db.path.parent / "semantic-embeddings.sqlite").clear()
    return {"query": cleaned, "model": MODEL, "checked_contacts": len(candidates),
            "limited": limited, "changed_contacts": changed, "results": selected}
