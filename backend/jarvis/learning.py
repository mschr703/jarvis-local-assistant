"""One-source, reviewable learning rounds backed by local document passages."""

import json
from uuid import uuid4

from jarvis.db import Database, utcnow
from jarvis.research import parse_model_json


class LearningConflict(ValueError):
    """The learning round changed or cannot be edited in this state."""


def _clean(value: object, minimum: int, maximum: int, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} fehlt")
    text = " ".join(value.split())
    if not minimum <= len(text) <= maximum:
        raise ValueError(f"{label} braucht {minimum}–{maximum} Zeichen")
    return text


def _in_source(quote: str, source_text: str) -> bool:
    return " ".join(quote.split()) in " ".join(source_text.split())


def parse_question(text: str, sources: list[dict]) -> dict:
    """Accept only a question with a literal quote from one selected passage."""
    payload = parse_model_json(text)
    if not isinstance(payload, dict):
        raise TypeError("Lernfrage ist kein JSON-Objekt")
    index = payload.get("source_index")
    if type(index) is not int or not 1 <= index <= len(sources):
        raise ValueError("Lernfrage verweist nicht auf eine ausgewählte Fundstelle")
    question = _clean(payload.get("question"), 10, 300, "Frage")
    answer = _clean(payload.get("answer"), 1, 500, "Musterantwort")
    quote = _clean(payload.get("evidence_quote"), 8, 300, "Quellenzitat")
    source = sources[index - 1]
    if not _in_source(quote, source["text"]):
        raise ValueError("Quellenzitat steht nicht in der ausgewählten Fundstelle")
    return {"question": question, "reference_answer": answer,
            "evidence_quote": quote, "source": source}


def parse_feedback(text: str, source_text: str) -> dict:
    payload = parse_model_json(text)
    if not isinstance(payload, dict):
        raise TypeError("Lernrückmeldung ist kein JSON-Objekt")
    evaluation = payload.get("evaluation")
    if not isinstance(evaluation, str) or evaluation not in {
            "correct", "partial", "incorrect", "uncertain"}:
        raise ValueError("Ungültige Lernbewertung")
    feedback = _clean(payload.get("feedback"), 10, 600, "Rückmeldung")
    next_question = _clean(payload.get("next_question", ""), 0, 300, "Folgefrage")
    quote = _clean(payload.get("evidence_quote"), 8, 300, "Quellenzitat")
    if not _in_source(quote, source_text):
        raise ValueError("Rückmeldung hat keinen gültigen Quellenbeleg")
    return {"evaluation": evaluation, "feedback": feedback,
            "next_question": next_question, "feedback_quote": quote}


class LearningStore:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _row(row) -> dict:
        item = dict(row)
        item["source"] = json.loads(item.pop("source_json"))
        return item

    def get(self, session_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM learning_sessions WHERE id=?", (session_id,)).fetchone()
            return self._row(row) if row else None

    def list(self, limit: int = 50) -> list[dict]:
        with self.db.connect() as db:
            rows = db.execute("""SELECT * FROM learning_sessions
                               ORDER BY updated_at DESC,id DESC LIMIT ?""", (limit,))
            return [self._row(row) for row in rows]

    def create(self, topic: str, question: dict) -> dict:
        source = question["source"]
        snapshot = {key: source[key] for key in
                    ("passage_id", "document_id", "path", "page", "sha256", "policy")}
        snapshot["text"] = source["text"][:1200]
        now, session_id = utcnow(), str(uuid4())
        with self.db.connect() as db:
            db.execute("""INSERT INTO learning_sessions
                       (id,topic,question,reference_answer,evidence_quote,source_json,
                        created_at,updated_at,version)
                       VALUES(?,?,?,?,?,?,?,?,1)""",
                       (session_id, topic, question["question"],
                        question["reference_answer"], question["evidence_quote"],
                        json.dumps(snapshot, ensure_ascii=False), now, now))
        self.db.log("learning_created", f"Lernrunde {session_id}")
        return self.get(session_id)

    def correct(self, session_id: str, expected_version: int, question: str,
                reference_answer: str, evidence_quote: str) -> dict:
        current = self.get(session_id)
        if current is None:
            raise KeyError(session_id)
        if current["version"] != expected_version or current["user_answer"] is not None:
            raise LearningConflict("Lernrunde wurde geändert oder bereits beantwortet")
        clean_question = _clean(question, 10, 300, "Frage")
        clean_answer = _clean(reference_answer, 1, 500, "Musterantwort")
        clean_quote = _clean(evidence_quote, 8, 300, "Quellenzitat")
        if not _in_source(clean_quote, current["source"]["text"]):
            raise ValueError("Quellenzitat steht nicht in der ausgewählten Fundstelle")
        with self.db.connect() as db:
            result = db.execute("""UPDATE learning_sessions SET question=?,reference_answer=?,
                                evidence_quote=?,version=version+1,updated_at=?
                                WHERE id=? AND version=? AND user_answer IS NULL""",
                                (clean_question, clean_answer, clean_quote, utcnow(),
                                 session_id, expected_version))
            if result.rowcount != 1:
                raise LearningConflict("Lernrunde wurde inzwischen geändert")
        self.db.log("learning_corrected", f"Lernrunde {session_id}")
        return self.get(session_id)

    def answer(self, session_id: str, expected_version: int, user_answer: str,
               feedback: dict) -> dict:
        if not isinstance(user_answer, str):
            raise TypeError("Eigene Antwort fehlt")
        clean_answer = user_answer.strip()
        if not 1 <= len(clean_answer) <= 2000:
            raise ValueError("Eigene Antwort braucht 1–2000 Zeichen")
        with self.db.connect() as db:
            result = db.execute("""UPDATE learning_sessions SET user_answer=?,evaluation=?,
                                feedback=?,feedback_quote=?,next_question=?,
                                version=version+1,updated_at=? WHERE id=? AND version=?""",
                                (clean_answer, feedback["evaluation"], feedback["feedback"],
                                 feedback.get("feedback_quote", ""), feedback["next_question"],
                                 utcnow(), session_id, expected_version))
            if result.rowcount != 1:
                raise LearningConflict("Lernrunde wurde inzwischen geändert")
        self.db.log("learning_answered", f"Lernrunde {session_id}")
        return self.get(session_id)

    def delete(self, session_id: str) -> None:
        with self.db.connect() as db:
            result = db.execute("DELETE FROM learning_sessions WHERE id=?", (session_id,))
            if result.rowcount != 1:
                raise KeyError(session_id)
        self.db.log("learning_deleted", f"Lernrunde {session_id}")
