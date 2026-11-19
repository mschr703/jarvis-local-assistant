"""Kleine, nachvollziehbare Webrecherche mit überprüfbaren Textbelegen."""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import parse_qs, quote_plus, urljoin, urlsplit
from uuid import uuid4

from jarvis.agent_reach import read_public_source
from jarvis.db import Database, utcnow
from jarvis.desktop import fetch_public_document

SEARCH_BASE = "https://html.duckduckgo.com/html/?q="
WIKIPEDIA_SEARCH = "https://en.wikipedia.org/w/index.php?search="
MAX_RESULTS = 5
MAX_SOURCES = 2
MAX_SOURCE_CHARS = 1500
PUBLICATION_FIELDS = {"article:published_time", "datepublished", "pubdate", "dc.date.issued"}


def plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class SearchResults(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "a" and "result__a" in (values.get("class") or "").split():
            self._href = values.get("href")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        raw = self._href
        self._href = None
        if raw.startswith("//duckduckgo.com/l/"):
            raw = parse_qs(urlsplit("https:" + raw).query).get("uddg", [""])[0]
        elif raw.startswith("https://duckduckgo.com/l/"):
            raw = parse_qs(urlsplit(raw).query).get("uddg", [""])[0]
        parsed = urlsplit(raw)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return
        if parsed.username or parsed.password or parsed.fragment:
            return
        title = plain_text("".join(self._parts))[:300]
        if title and raw not in {item["url"] for item in self.results}:
            self.results.append({"title": title, "url": raw})


class WikipediaResults(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._heading = 0
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "div" and "mw-search-result-heading" in (values.get("class") or "").split():
            self._heading += 1
        elif self._heading and tag == "a":
            self._href = values.get("href")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            url = urljoin("https://en.wikipedia.org", self._href)
            title = plain_text("".join(self._parts))[:300]
            if title and url.startswith("https://en.wikipedia.org/wiki/"):
                self.results.append({"title": title, "url": url})
            self._href = None
        elif tag == "div" and self._heading:
            self._heading -= 1


class ReadablePage(HTMLParser):
    SKIP: ClassVar = {"script", "style", "nav", "footer", "header", "aside", "form", "noscript", "svg"}
    BLOCK: ClassVar = {"p", "div", "article", "main", "section", "h1", "h2", "h3", "h4", "li", "br", "tr"}
    VOID: ClassVar = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._main_depth = 0
        self._title = False
        self.title_parts: list[str] = []
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self.published_candidates: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "meta":
            values = dict(attrs)
            field = (values.get("property") or values.get("name") or values.get("itemprop") or "").casefold()
            if (field in PUBLICATION_FIELDS and values.get("content")
                    and len(values["content"] or "") <= 40 and len(self.published_candidates) < 8):
                self.published_candidates.append(values["content"] or "")
        if tag == "main" or (self._main_depth and tag not in self.VOID):
            self._main_depth += 1
        if tag == "title":
            self._title = True
        if (self._skip_depth or tag in self.SKIP) and tag not in self.VOID:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if self._main_depth:
                self.main_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._title:
            self.title_parts.append(data)
        elif not self._skip_depth:
            self.parts.append(data)
            if self._main_depth:
                self.main_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._title = False
        if self._skip_depth and tag not in self.VOID:
            self._skip_depth -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")
            if self._main_depth:
                self.main_parts.append("\n")
        if self._main_depth and tag not in self.VOID:
            self._main_depth -= 1

    def readable(self) -> tuple[str, str]:
        chosen = self.main_parts if len("".join(self.main_parts)) >= 300 else self.parts
        lines = [plain_text(line) for line in "".join(chosen).splitlines()]
        text = "\n".join(line for line in lines if line)
        return plain_text("".join(self.title_parts))[:300], text[:MAX_SOURCE_CHARS]

    def published_at(self) -> str | None:
        """Nur explizite, plausible ISO-Veröffentlichungsdaten; Herkunft bleibt die Seite."""
        for raw in self.published_candidates:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}"
                                r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)?", raw):
                continue
            try:
                published = (datetime.fromisoformat(raw).date()
                             if "T" in raw else date.fromisoformat(raw))
            except ValueError:
                continue
            if date(1900, 1, 1) <= published <= datetime.now(UTC).date():
                return published.isoformat()
        return None


def search_public_web(query: str) -> tuple[list[dict[str, str]], str]:
    if not query.strip() or len(query) > 300:
        raise ValueError("Suchanfrage muss 1 bis 300 Zeichen enthalten")
    try:
        _, html, _, _ = fetch_public_document(SEARCH_BASE + quote_plus(query.strip()))
        parser = SearchResults()
        parser.feed(html)
        if parser.results:
            return parser.results[:MAX_RESULTS], "DuckDuckGo HTML"
    except ValueError:
        pass
    final_url, html, _, _ = fetch_public_document(WIKIPEDIA_SEARCH + quote_plus(query.strip()))
    if final_url.startswith("https://en.wikipedia.org/wiki/"):
        return [{"title": final_url.rsplit("/", 1)[-1].replace("_", " "), "url": final_url}], "Wikipedia-Suche"
    parser = WikipediaResults()
    parser.feed(html)
    return parser.results[:MAX_RESULTS], "Wikipedia-Suche"


def read_source(url: str, search_title: str) -> dict[str, str | None]:
    host = (urlsplit(url).hostname or "").lower()
    if host in {"youtube.com", "www.youtube.com", "youtu.be", "github.com"}:
        return read_public_source(url)
    final_url, html, _, content_type = fetch_public_document(url, allow_pdf=True)
    parser = ReadablePage()
    parser.feed(html)
    title, content = parser.readable()
    if len(content) < 150:
        raise ValueError("Quelle enthält zu wenig lesbaren Text")
    display_title = (f"{search_title} ({title})" if content_type == "application/pdf"
                     else title or search_title)
    return {"url": final_url, "title": display_title, "text": content,
            "media_type": "pdf" if content_type == "application/pdf" else "web",
            "retrieved_at": utcnow(),
            "published_at": parser.published_at() if content_type != "application/pdf" else None}


def normalize_quote(text: str) -> str:
    return plain_text(text).casefold()


def compare_verified_sources(sources: list[dict], claims: list[dict]) -> dict | None:
    """Stellt belegte Zitate lexikalisch gegenüber, ohne Tatsachenvergleich zu behaupten."""
    if len(sources) != 2:
        return None
    stopwords = {"aber", "auch", "beide", "beim", "dabei", "damit", "dann", "dass",
                 "diese", "dieser", "dieses", "einem", "einen", "einer", "eines", "oder",
                 "sowie", "unter", "wird", "werden", "with", "from", "that", "this",
                 "which", "their", "about", "nach", "noch", "nicht", "sind", "sein"}
    entries = []
    term_sets = []
    number_sets = []
    for source in sources:
        quote = next((item.get("quote", "") for claim in claims
                      for item in claim.get("evidence", [])
                      if item.get("source_id") == source.get("id")
                      and isinstance(item.get("quote"), str)
                      and len(item["quote"]) >= 20
                      and normalize_quote(item["quote"]) in normalize_quote(source.get("text", ""))), "")
        if not quote:
            return None
        entries.append({"source_id": source["id"], "title": source["title"],
                        "url": source["url"], "quote": quote,
                        "retrieved_at": source["retrieved_at"],
                        "published_at": source.get("published_at"),
                        "found_by_query": source.get("found_by_query"),
                        "found_by_provider": source.get("found_by_provider"),
                        "media_type": source.get("media_type", "web")})
        term_sets.append({word for word in re.findall(r"[^\W\d_]{4,}", quote.casefold())
                          if word not in stopwords})
        number_sets.append(set(re.findall(
            r"(?<![\w])\d+(?:[.,]\d+)?(?:\s?(?:%|km|kg|mm|cm|°C|m|g|K))?(?![\w])",
            quote)))
    return {"entries": entries,
            "shared_words": sorted(term_sets[0] & term_sets[1])[:8],
            "first_only_words": sorted(term_sets[0] - term_sets[1])[:8],
            "second_only_words": sorted(term_sets[1] - term_sets[0])[:8],
            "shared_numbers": sorted(number_sets[0] & number_sets[1])[:8],
            "first_only_numbers": sorted(number_sets[0] - number_sets[1])[:8],
            "second_only_numbers": sorted(number_sets[1] - number_sets[0])[:8]}


def extract_source_claims(sources: list[dict], question: str) -> list[dict]:
    """Wählt kurze tatsächliche Fundstellen; die Modellantwort kann sie nicht ersetzen."""
    stop = {"was", "ist", "das", "die", "der", "und", "ein", "eine", "für", "mit",
            "what", "the", "and", "for", "mission", "nasa"}
    terms = {term for term in re.findall(r"[\wÀ-ÿ]{4,}", question.casefold()) if term not in stop}
    claims = []
    for source in sources:
        candidates = [plain_text(line) for line in source["text"].splitlines()]
        candidates = [line for line in candidates if len(line) >= 45]
        if not candidates:
            continue
        quote = max(candidates, key=lambda line: (
            sum(term in line.casefold() for term in terms), min(len(line), 300), -candidates.index(line)))
        quote = quote[:300].rstrip()
        if len(quote) >= 20:
            claims.append({"statement": quote,
                           "evidence": [{"source_id": source["id"], "quote": quote}]})
    return claims


def validate_claims(payload: object, sources: list[dict]) -> tuple[list[dict], str, str]:
    if not isinstance(payload, dict):
        raise TypeError("Modell lieferte keinen strukturierten Bericht")
    source_by_id = {source["id"]: source for source in sources}
    validated: list[dict] = []
    raw_claims = payload.get("claims", [])
    if not isinstance(raw_claims, list):
        raw_claims = []
    for claim in raw_claims[:8]:
        if not isinstance(claim, dict) or not isinstance(claim.get("statement"), str):
            continue
        statement = plain_text(claim["statement"])[:600]
        evidence = []
        raw_evidence = claim.get("evidence", [])
        if not isinstance(raw_evidence, list):
            raw_evidence = []
        for item in raw_evidence[:3]:
            if not isinstance(item, dict) or not isinstance(item.get("source_id"), int):
                continue
            source = source_by_id.get(item["source_id"])
            quote = plain_text(str(item.get("quote", "")))[:500]
            if source and len(quote) >= 20 and normalize_quote(quote) in normalize_quote(source["text"]):
                evidence.append({"source_id": item["source_id"], "quote": quote})
        if statement and evidence:
            validated.append({"statement": statement, "evidence": evidence})
    interpretation = plain_text(str(payload.get("interpretation", "")))[:1500]
    uncertainty = plain_text(str(payload.get("uncertainty", "")))[:1000]
    return validated, interpretation, uncertainty


def parse_model_json(text: str) -> object:
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.IGNORECASE)
    return json.loads(value)


class ResearchStore:
    def __init__(self, db: Database):
        self.db = db

    def save(self, question: str, query: str, search_provider: str, sources: list[dict], claims: list[dict],
             interpretation: str, uncertainty: str, errors: list[str], provider: str,
             model: str, project_context: dict | None = None,
             parent_report_id: str | None = None, open_point: str | None = None) -> dict:
        report_id, now = str(uuid4()), utcnow()
        with self.db.connect() as db:
            db.execute("""INSERT INTO research_reports
                (id,question,search_query,search_provider,sources_json,claims_json,interpretation,uncertainty,
                 errors_json,provider,model,created_at,project_context_json,parent_report_id,open_point)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (report_id, question, query, search_provider, json.dumps(sources, ensure_ascii=False),
                 json.dumps(claims, ensure_ascii=False), interpretation, uncertainty,
                 json.dumps(errors, ensure_ascii=False), provider, model, now,
                 json.dumps(project_context, ensure_ascii=False) if project_context else None,
                 parent_report_id, open_point))
        self.db.log("research_created", f"Recherche {report_id}")
        return self.get(report_id)

    def get(self, report_id: str) -> dict | None:
        with self.db.connect() as db:
            row = db.execute("SELECT * FROM research_reports WHERE id=?", (report_id,)).fetchone()
        if row is None:
            return None
        report = dict(row)
        for field in ("sources", "claims", "errors"):
            report[field] = json.loads(report.pop(field + "_json"))
        raw_context = report.pop("project_context_json")
        report["project_context"] = json.loads(raw_context) if raw_context else None
        report["comparison"] = compare_verified_sources(report["sources"], report["claims"])
        with self.db.connect() as db:
            evaluation = db.execute("SELECT * FROM research_evaluations WHERE report_id=?",
                                    (report_id,)).fetchone()
        report["evaluation"] = dict(evaluation) if evaluation else None
        return report

    def list(self) -> list[dict]:
        with self.db.connect() as db:
            ids = [row["id"] for row in db.execute(
                "SELECT id FROM research_reports ORDER BY created_at DESC LIMIT 100")]
        return [self.get(report_id) for report_id in ids]

    def follow_ups(self, report_id: str) -> list[dict]:
        """List only direct descendants; full reports stay available by their ID."""
        with self.db.connect() as db:
            rows = db.execute("""SELECT id,question,open_point,search_query,sources_json,
                interpretation,errors_json,created_at FROM research_reports
                WHERE parent_report_id=? ORDER BY created_at DESC,id DESC LIMIT 50""",
                (report_id,)).fetchall()
        results = []
        for row in rows:
            source_count = len(json.loads(row["sources_json"]))
            results.append({"report_id": row["id"], "question": row["question"],
                            "open_point": row["open_point"], "search_query": row["search_query"],
                            "created_at": row["created_at"], "source_count": source_count,
                            "has_model_answer": bool(row["interpretation"]),
                            "has_errors": bool(json.loads(row["errors_json"]))})
        return results

    def for_project(self, project_id: str) -> list[dict]:
        """Summarize reports that explicitly captured this work state's ID."""
        with self.db.connect() as db:
            rows = db.execute("""SELECT id,question,interpretation,provider,model,created_at,
                sources_json,project_context_json FROM research_reports
                WHERE json_extract(project_context_json,'$.id')=?
                ORDER BY created_at DESC,id DESC LIMIT 50""", (project_id,)).fetchall()
        return [{"report_id": row["id"], "question": row["question"],
                 "interpretation": row["interpretation"], "provider": row["provider"],
                 "model": row["model"], "created_at": row["created_at"],
                 "source_count": len(json.loads(row["sources_json"])),
                 "project_version": json.loads(row["project_context_json"])["version"]}
                for row in rows]

    def evaluate(self, report_id: str, verdict: str, source_quality: str,
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
        with self.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT id FROM research_reports WHERE id=?", (report_id,)).fetchone() is None:
                raise LookupError("Recherchebericht nicht gefunden")
            prior = db.execute("SELECT version FROM research_evaluations WHERE report_id=?",
                               (report_id,)).fetchone()
            if prior is None:
                if version != 0:
                    raise RuntimeError("Berichtsbewertung wurde zwischenzeitlich geändert")
                db.execute("""INSERT INTO research_evaluations
                           (report_id,verdict,source_quality,reason,regression_case,
                            expected_result,version,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,1,?,?)""",
                           (report_id, verdict, source_quality, reason, int(regression_case),
                            expected_result, now, now))
            else:
                if prior["version"] != version:
                    raise RuntimeError("Berichtsbewertung wurde zwischenzeitlich geändert")
                db.execute("""UPDATE research_evaluations SET verdict=?,source_quality=?,
                           reason=?,regression_case=?,expected_result=?,version=version+1,
                           updated_at=? WHERE report_id=?""",
                           (verdict, source_quality, reason, int(regression_case),
                            expected_result, now, report_id))
            return dict(db.execute("SELECT * FROM research_evaluations WHERE report_id=?",
                                   (report_id,)).fetchone())

    def evaluation_cases(self) -> list[dict]:
        with self.db.connect() as db:
            return [dict(row) for row in db.execute("""SELECT e.*,r.question,r.interpretation,
                r.provider,r.model,r.created_at AS report_created_at
                FROM research_evaluations e JOIN research_reports r ON r.id=e.report_id
                WHERE e.regression_case=1 ORDER BY e.updated_at DESC LIMIT 100""")]

    def export_evaluation_cases(self) -> list[dict]:
        """Return bounded, consistent snapshots for an explicit local JSONL download."""
        with self.db.connect() as db:
            db.execute("BEGIN")
            evaluations = db.execute("""SELECT e.* FROM research_evaluations e
                JOIN research_reports r ON r.id=e.report_id
                WHERE e.regression_case=1 ORDER BY e.updated_at DESC, e.report_id LIMIT 100""").fetchall()
            snapshots = []
            for evaluation in evaluations:
                report = dict(db.execute("SELECT * FROM research_reports WHERE id=?",
                                         (evaluation["report_id"],)).fetchone())
                for field in ("sources", "claims", "errors"):
                    report[field] = json.loads(report.pop(field + "_json"))
                # Project notes are deliberately omitted from a shareable evaluation file.
                report.pop("project_context_json")
                snapshots.append({
                    "format_version": 1,
                    "evaluation": dict(evaluation),
                    "report": report,
                })
            return snapshots
