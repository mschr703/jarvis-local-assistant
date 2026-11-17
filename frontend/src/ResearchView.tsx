import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import { downloadResearchMarkdown } from './researchExport';
import { downloadResearchCases, type ResearchCaseSnapshot } from './researchCaseExport';

type Source = { id: number; title: string; url: string; text: string; retrieved_at: string;
  published_at?: string | null; found_by_query?: string | null;
  found_by_provider?: string | null; media_type?: 'web' | 'pdf' | 'video' | 'github';
  caption_error?: string | null };
type Evidence = { source_id: number; quote: string };
type Claim = { statement: string; evidence: Evidence[] };
type WorkSummary = { id: string; project: string; version: number };
type ProjectContext = { id: string; project: string; version: number; updated_at: string;
  confirmed: string; open_questions: string; next_step: string; truncated: boolean };
type Comparison = { entries: { source_id: number; title: string; url: string; quote: string;
  retrieved_at: string; published_at?: string | null; found_by_query?: string | null;
  found_by_provider?: string | null; media_type: 'web' | 'pdf' | 'video' | 'github' }[];
  shared_words: string[]; first_only_words: string[]; second_only_words: string[];
  shared_numbers?: string[]; first_only_numbers?: string[]; second_only_numbers?: string[] };
type Evaluation = { report_id: string; verdict: string; source_quality: string; reason: string;
  regression_case: number; expected_result: string; version: number };
type EvaluationCase = Evaluation & { question: string; interpretation: string;
  provider: string; model: string; report_created_at: string };
type FollowUpSummary = { report_id: string; question: string; open_point: string;
  search_query: string; created_at: string; source_count: number;
  has_model_answer: boolean; has_errors: boolean };
type Report = {
  id: string; question: string; search_query: string; search_provider: string;
  sources: Source[]; claims: Claim[]; interpretation: string; uncertainty: string;
  errors: string[]; provider: string; model: string; created_at: string;
  project_context?: ProjectContext | null;
  parent_report_id?: string | null; open_point?: string | null;
  comparison?: Comparison | null;
  evaluation?: Evaluation | null;
};

const verdictLabels: Record<string, string> = { achieved: 'Ziel erreicht', partial: 'Teilweise erreicht',
  failed: 'Ziel verfehlt', unclear: 'Unklar' };
const sourceLabels: Record<string, string> = { supported: 'Quellen gestützt',
  unsupported: 'Quellen unzureichend', not_applicable: 'Keine Quelle nötig', unclear: 'Quellen unklar' };

function mediaName(value?: Source['media_type']): string {
  return value === 'pdf' ? 'Text-PDF' : value === 'video' ? 'YouTube-Video' :
    value === 'github' ? 'GitHub' : 'Webseite';
}

function ResearchEvaluation({ report, onSaved }: { report: Report; onSaved: (value: Evaluation) => void }) {
  const prior = report.evaluation;
  const [verdict, setVerdict] = useState(prior?.verdict || 'unclear');
  const [sourceQuality, setSourceQuality] = useState(prior?.source_quality || 'unclear');
  const [reason, setReason] = useState(prior?.reason || '');
  const [regressionCase, setRegressionCase] = useState(Boolean(prior?.regression_case));
  const [expectedResult, setExpectedResult] = useState(prior?.expected_result || '');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [failed, setFailed] = useState(false);
  const save = async () => {
    if (busy) return;
    setBusy(true); setNotice(''); setFailed(false);
    try {
      const saved = await api<Evaluation>(`/research/${report.id}/evaluation`, {
        method: 'PUT', body: JSON.stringify({ verdict, source_quality: sourceQuality, reason,
          regression_case: regressionCase, expected_result: regressionCase ? expectedResult : '',
          version: report.evaluation?.version || 0 }),
      });
      onSaved(saved);
      setNotice('Berichtsbewertung gespeichert. Quellen und Modelltext bleiben unverändert.');
    } catch (cause) {
      setFailed(true); setNotice((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return <section className="research-evaluation" aria-label="Bericht bewerten">
    <h4>Bericht bewerten</h4>
    <p>Dein Urteil bleibt beim gespeicherten Bericht. Ein Fehlerfall wird als Testfall abgelegt, aber nicht automatisch erneut ausgeführt.</p>
    <label>Ergebnis<select value={verdict} onChange={(event) => setVerdict(event.target.value)}>{Object.entries(verdictLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label>Quellenbewertung<select value={sourceQuality} onChange={(event) => setSourceQuality(event.target.value)}>{Object.entries(sourceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
    <label>Begründung<textarea value={reason} maxLength={2000} onChange={(event) => setReason(event.target.value)} /></label>
    <label><input type="checkbox" checked={regressionCase} onChange={(event) => setRegressionCase(event.target.checked)} /> Als Fehler-Testfall sichern</label>
    {regressionCase && <label>Erwartetes Ergebnis<textarea value={expectedResult} maxLength={500} onChange={(event) => setExpectedResult(event.target.value)} /></label>}
    <button type="button" disabled={busy || !reason.trim() || (regressionCase &&
      (!['partial', 'failed'].includes(verdict) || !expectedResult.trim()))} onClick={() => void save()}>
      {busy ? 'Speichere Bewertung…' : 'Bewertung speichern'}
    </button>
    {notice && <p className={failed ? 'notice error' : 'notice'} role={failed ? 'alert' : 'status'}>{notice}</p>}
  </section>;
}

function published(value?: string | null): string {
  return value ? ` · Veröffentlichung laut Quelle (ungeprüft): ${value.slice(0, 10).split('-').reverse().join('.')}` : '';
}

function ResearchComparison({ comparison }: { comparison: Comparison }) {
  const hasNumbers = (comparison.shared_numbers?.length || 0) +
    (comparison.first_only_numbers?.length || 0) + (comparison.second_only_numbers?.length || 0) > 0;
  return <>
    <h4>Quellen nebeneinander</h4>
    <p>Diese Ansicht vergleicht nur den Wortlaut der zwei geprüften Fundstellen. Gleiche Wörter beweisen keine Übereinstimmung; fehlende Wörter beweisen keinen Widerspruch.</p>
    <div className="comparison-grid">{comparison.entries.map((entry) => <div key={entry.source_id} className="comparison-entry">
      <a href={entry.url} target="_blank" rel="noopener noreferrer">[{entry.source_id}] {entry.title}</a>
      <small>{mediaName(entry.media_type)} · Abruf: {new Date(entry.retrieved_at).toLocaleString('de-DE')}{published(entry.published_at)}</small>
      {entry.found_by_query && <small>Gefunden über {entry.found_by_provider}: „{entry.found_by_query}“</small>}
      <p>„{entry.quote}“</p>
    </div>)}</div>
    <p><strong>Wörter in beiden Zitaten:</strong> {comparison.shared_words.join(', ') || 'keine'}</p>
    <p><strong>Nur im ersten Zitat:</strong> {comparison.first_only_words.join(', ') || 'keine'}</p>
    <p><strong>Nur im zweiten Zitat:</strong> {comparison.second_only_words.join(', ') || 'keine'}</p>
    {hasNumbers && <><h5>Zahlen als Prüfhinweis</h5>
      <p>Die Zahlen stammen nur aus den zwei Zitaten. Verschiedene Zahlen können unterschiedliche Messgrößen oder Zeitpunkte betreffen und sind kein automatisch festgestellter Widerspruch.</p>
      <p><strong>Zahlen in beiden Zitaten:</strong> {comparison.shared_numbers?.join(', ') || 'keine'}</p>
      <p><strong>Nur im ersten Zitat:</strong> {comparison.first_only_numbers?.join(', ') || 'keine'}</p>
      <p><strong>Nur im zweiten Zitat:</strong> {comparison.second_only_numbers?.join(', ') || 'keine'}</p></>}
  </>;
}

function ResearchFollowUp({ report, configured, provider, onCreated }: {
  report: Report; configured: boolean; provider: string; onCreated: (value: Report) => void;
}) {
  const [openPoint, setOpenPoint] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submit = async () => {
    if (busy || openPoint.trim().length < 5 || searchQuery.trim().length < 5) return;
    setBusy(true); setError('');
    try {
      const result = await api<Report>(`/research/${encodeURIComponent(report.id)}/follow-up`, {
        method: 'POST', body: JSON.stringify({ open_point: openPoint.trim(), search_query: searchQuery.trim() }),
      });
      onCreated(result);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return <section className="research-follow-up" aria-label="Begrenzte Nachrecherche">
    <h4>Offenen Punkt nachrecherchieren</h4>
    <p>Formuliere einen konkreten offenen Punkt und prüfe die Suchfrage. Erst dein Klick startet die öffentliche Suche. Jarvis speichert einen getrennten Bericht und ändert diesen hier nicht.</p>
    <label>Offener Punkt<textarea value={openPoint} maxLength={1000} onChange={(event) => setOpenPoint(event.target.value)} placeholder="Welche konkrete Frage ist noch offen?" /></label>
    <label>Suchfrage bestätigen oder bearbeiten<input value={searchQuery} maxLength={300} onChange={(event) => setSearchQuery(event.target.value)} placeholder="Gezielte Stichwörter für die öffentliche Suche" /></label>
    <p className="muted">Höchstens drei Treffer werden versucht, eine neue lesbare Quelle und ein lokaler Ollama-Aufruf. Ein Fehler bleibt als eigener Bericht sichtbar. Keine automatische weitere Suche.</p>
    <button type="button" disabled={!configured || provider !== 'ollama' || busy || openPoint.trim().length < 5 || searchQuery.trim().length < 5} onClick={() => void submit()}>{busy ? 'Nachrecherche läuft…' : 'Nachrecherche starten'}</button>
    {provider !== 'ollama' && <p className="muted">Nachrecherche ist nur mit lokalem Ollama verfügbar.</p>}
    {error && <p className="notice error" role="alert">{error}</p>}
  </section>;
}

function ResearchFollowUpList({ reportId, onOpen }: {
  reportId: string; onOpen: (reportId: string) => void;
}) {
  const [items, setItems] = useState<FollowUpSummary[]>([]);
  const [error, setError] = useState('');
  useEffect(() => {
    void api<FollowUpSummary[]>(`/research/${encodeURIComponent(reportId)}/follow-ups`)
      .then(setItems).catch((cause: Error) => setError(cause.message));
  }, [reportId]);
  return <section className="research-follow-ups" aria-label="Nachrechercheverlauf">
    <h4>Nachrechercheverlauf</h4>
    {error && <p className="notice error" role="alert">{error}</p>}
    {items.length ? <div className="item-list">{items.map((item) =>
      <div className="item-row" key={item.report_id}><div><strong>{item.open_point || item.question}</strong>
        <small>Suchfrage: {item.search_query} · {new Date(item.created_at).toLocaleString('de-DE')}</small>
        <small>{item.source_count} neue Quelle{item.source_count === 1 ? '' : 'n'} · {item.has_model_answer ? 'mit Modellantwort' : 'ohne Modellantwort'}{item.has_errors ? ' · mit Fehlerhinweis' : ''}</small>
      </div><button type="button" onClick={() => onOpen(item.report_id)}>Nachlauf öffnen</button></div>)}</div>
      : !error && <p>Noch kein Nachlauf zu diesem Bericht gespeichert.</p>}
    {items.length === 50 && <p className="muted">Es werden höchstens die 50 neuesten direkten Nachläufe angezeigt.</p>}
  </section>;
}

export default function ResearchView({ visible, configured, provider, requestedReportId, onReportLoaded,
  suggestedQuestion, onSuggestionTaken }: {
  visible: boolean; configured: boolean; provider: string;
  requestedReportId: string; onReportLoaded: () => void;
  suggestedQuestion: string; onSuggestionTaken: () => void;
}) {
  const [question, setQuestion] = useState('');
  const [pendingSuggestion, setPendingSuggestion] = useState('');
  const [suggestionNotice, setSuggestionNotice] = useState('');
  const [query, setQuery] = useState('');
  const [secondQuery, setSecondQuery] = useState('');
  const [sourceLinks, setSourceLinks] = useState('');
  const [workStates, setWorkStates] = useState<WorkSummary[]>([]);
  const [projectId, setProjectId] = useState('');
  const [allowCloud, setAllowCloud] = useState(false);
  const [reports, setReports] = useState<Report[]>([]);
  const [cases, setCases] = useState<EvaluationCase[]>([]);
  const [active, setActive] = useState<Report | null>(null);
  const reportRef = useRef<HTMLElement | null>(null);
  const [jumpToReportId, setJumpToReportId] = useState('');
  const [busy, setBusy] = useState(false);
  const [suggestBusy, setSuggestBusy] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);
  const [exportError, setExportError] = useState('');
  const [error, setError] = useState('');
  const sourceUrls = sourceLinks.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);

  useEffect(() => {
    if (!visible || !suggestedQuestion) return;
    if (question.trim() || query.trim() || secondQuery.trim() || sourceLinks.trim()) {
      setPendingSuggestion(suggestedQuestion);
    } else {
      setQuestion(suggestedQuestion);
      setSuggestionNotice('Vorschlag ins Rechercheformular übernommen. Erst „Recherche starten“ liest öffentliche Quellen.');
    }
    onSuggestionTaken();
  }, [visible, suggestedQuestion]);

  useEffect(() => {
    if (!visible) return;
    void api<Report[]>('/research').then((items) => {
      setReports(items);
      setActive((current) => requestedReportId ? current : current || items[0] || null);
    }).catch((cause: Error) => setError(cause.message));
    void api<WorkSummary[]>('/work-states').then(setWorkStates)
      .catch((cause: Error) => setError(cause.message));
    void api<EvaluationCase[]>('/research/evaluation-cases').then(setCases)
      .catch((cause: Error) => setError(cause.message));
  }, [visible]);

  useEffect(() => {
    if (!visible || !requestedReportId) return;
    void api<Report>(`/research/${encodeURIComponent(requestedReportId)}`)
      .then((report) => { setActive(report); setJumpToReportId(report.id); onReportLoaded(); })
      .catch((cause: Error) => { setError(cause.message); onReportLoaded(); });
  }, [visible, requestedReportId]);

  useEffect(() => {
    if (!visible || !jumpToReportId || active?.id !== jumpToReportId) return;
    reportRef.current?.scrollIntoView({ block: 'start' });
    setJumpToReportId('');
  }, [visible, active?.id, jumpToReportId]);

  const evaluationSaved = (value: Evaluation) => {
    setActive((current) => current?.id === value.report_id ? { ...current, evaluation: value } : current);
    setReports((items) => items.map((item) => item.id === value.report_id ?
      { ...item, evaluation: value } : item));
    void api<EvaluationCase[]>('/research/evaluation-cases').then(setCases)
      .catch((cause: Error) => setError(cause.message));
  };

  const followUpCreated = (report: Report) => {
    setReports((items) => [report, ...items]);
    setActive(report);
    setJumpToReportId(report.id);
  };

  const openReport = async (reportId: string) => {
    setError('');
    try {
      const report = await api<Report>(`/research/${encodeURIComponent(reportId)}`);
      setActive(report);
      setJumpToReportId(report.id);
    } catch (cause) {
      setError((cause as Error).message);
    }
  };

  const submit = async () => {
    if (!question.trim() || busy || suggestBusy) return;
    setBusy(true);
    setError('');
    try {
      const report = await api<Report>('/research', {
        method: 'POST',
        body: JSON.stringify({ question: question.trim(), search_query: query.trim(), source_urls: sourceUrls,
          second_search_query: secondQuery.trim(), project_id: projectId || null, allow_cloud: allowCloud }),
      });
      setReports((items) => [report, ...items]);
      setActive(report);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const suggestQueries = async () => {
    if (question.trim().length < 5 || busy || suggestBusy || provider !== 'ollama' || sourceUrls.length) return;
    setSuggestBusy(true);
    setError('');
    try {
      const suggestion = await api<{ first: string; second: string }>('/research/suggest-queries', {
        method: 'POST', body: JSON.stringify({ question: question.trim() }),
      });
      setQuery(suggestion.first);
      setSecondQuery(suggestion.second);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setSuggestBusy(false);
    }
  };

  const exportCases = async () => {
    if (exportBusy || !cases.length) return;
    setExportBusy(true);
    setExportError('');
    try {
      const snapshots = await api<ResearchCaseSnapshot[]>('/research/evaluation-cases/export');
      if (!snapshots.length) throw new Error('Keine Recherche-Testfälle zum Export vorhanden.');
      downloadResearchCases(snapshots);
    } catch (cause) {
      setExportError((cause as Error).message);
    } finally {
      setExportBusy(false);
    }
  };

  return <>
    <p className="eyebrow">RECHERCHE</p>
    <h2>Öffentliche Quellen prüfen</h2>
    <p className="intro">Jarvis liest öffentliche Webseiten, kurze Text-PDFs sowie ausdrücklich angegebene YouTube-Videos und GitHub-Seiten. Belegte Aussagen und Schlussfolgerungen bleiben getrennt.</p>
    {suggestionNotice && <p className="notice" role="status">{suggestionNotice}</p>}
    {pendingSuggestion && <section className="card research-card"><h3>Vorgeschlagene Recherchefrage</h3><p>{pendingSuggestion}</p><p>Im Formular steht bereits eine Eingabe. Übernimm den Vorschlag nur, wenn sie ersetzt werden soll.</p><div className="item-actions"><button onClick={() => setPendingSuggestion('')}>Bestehende Eingabe behalten</button><button onClick={() => { setQuestion(pendingSuggestion); setQuery(''); setSecondQuery(''); setSourceLinks(''); setProjectId(''); setAllowCloud(false); setPendingSuggestion(''); setSuggestionNotice('Vorschlag übernommen. Erst „Recherche starten“ liest öffentliche Quellen.'); }}>Vorschlag ins Formular übernehmen</button></div></section>}
    <section className="card research-card">
      <h3>Neuer Auftrag</h3>
      <label>Frage<textarea value={question} maxLength={1000} disabled={suggestBusy} onChange={(event) => setQuestion(event.target.value)} placeholder="Was möchte ich für mein Projekt wissen?" /></label>
      <label>Suchanfrage (optional)<input value={query} maxLength={300} disabled={sourceUrls.length > 0 || suggestBusy} onChange={(event) => setQuery(event.target.value)} placeholder="Leer = Frage verwenden" /></label>
      <label>Zweite Suchfrage (optional)<input value={secondQuery} maxLength={300} disabled={sourceUrls.length > 0 || suggestBusy} onChange={(event) => setSecondQuery(event.target.value)} placeholder="Zweite gezielte Teilfrage für eine weitere Quelle" /></label>
      <label>Eigene öffentliche Quellenlinks (optional, eine URL pro Zeile, höchstens zwei)<textarea value={sourceLinks} maxLength={4100} disabled={suggestBusy} onChange={(event) => { setSourceLinks(event.target.value); if (event.target.value.trim()) setSecondQuery(''); }} placeholder="https://example.com/bericht.pdf" /></label>
      {provider === 'ollama' && <><button type="button" disabled={!configured || busy || suggestBusy || question.trim().length < 5 || sourceUrls.length > 0} onClick={() => void suggestQueries()}>{suggestBusy ? 'Ollama schlägt Teilfragen vor…' : 'Teilfragen mit Ollama vorschlagen'}</button><p className="muted">Der lokale Vorschlag startet noch keine Websuche. Prüfe und bearbeite beide Suchfragen vor „Recherche starten“.</p></>}
      <label>Lokaler Arbeitsstand (optional)<select value={projectId} onChange={(event) => setProjectId(event.target.value)}><option value="">Ohne Projektkontext</option>{workStates.map((item) => <option key={item.id} value={item.id} disabled={provider !== 'ollama'}>{item.project} · Version {item.version}</option>)}</select></label>
      {provider !== 'ollama' && <p className="muted">Projektkontext ist nur für lokales Ollama freigegeben. Für Cloud-Recherche „Ohne Projektkontext“ wählen.</p>}
      {sourceUrls.length > 0 && <p className="muted">Mit eigenen Links wird keine Suchanfrage an DuckDuckGo oder Wikipedia gesendet. YouTube liefert Metadaten und verfügbare Untertitel, GitHub öffentliche Repositories, Issues oder PRs. Jarvis liest nur die angegebenen Quellen; es nutzt weder Login noch kostenpflichtige Transkription.</p>}
      {secondQuery.trim() && <p className="muted">Jarvis sucht zu jeder Teilfrage höchstens eine lesbare Quelle und nennt die Suchherkunft im Bericht. Insgesamt höchstens zwei Quellen und ein Modellaufruf. Direkte Links sind dann nicht möglich.</p>}
      {sourceUrls.length > 2 && <p className="notice error" role="alert">Höchstens zwei Quellenlinks eingeben.</p>}
      {provider === 'openrouter' && <label><input type="checkbox" checked={allowCloud} onChange={(event) => setAllowCloud(event.target.checked)} /> Frage und gelesene öffentliche Quellen für diesen Auftrag an OpenRouter senden</label>}
      <p className="muted">Suche: DuckDuckGo HTML; bei fehlenden Treffern Wikipedia-Suche. Höchstens 5 Treffer je Suchfrage, normal 3 Leseversuche; mit zweiter Teilfrage höchstens 2 je Suche. Insgesamt 2 gelesene Quellen und 1500 Textzeichen je Quelle. Webseiten haben 20 Sekunden Netzwerklimit; Text-PDFs sind auf 2 MB, 50 Seiten, 12 gelesene Seiten und 8 Sekunden Extraktion begrenzt. Öffentliche YouTube-/GitHub-Links haben 60 Sekunden CLI- und 80 Sekunden Prozesslimit. Das Modell hat 90 Sekunden. Kein kostenpflichtiger Suchdienst.</p>
      <button disabled={!configured || busy || suggestBusy || question.trim().length < 5 || sourceUrls.length > 2 || (provider !== 'ollama' && !!projectId) || (provider === 'openrouter' && !allowCloud)} onClick={() => void submit()}>{busy ? 'Suche, lese und prüfe…' : 'Recherche starten'}</button>
      {!configured && <p>Starte zuerst das eingestellte Modell.</p>}
      {error && <p className="notice error" role="alert">{error}</p>}
    </section>
    {reports.length > 0 && <section className="card research-card"><h3>Gespeicherte Recherchen</h3><div className="item-actions">{reports.map((report) => <button key={report.id} className={active?.id === report.id ? 'selected' : ''} onClick={() => setActive(report)}>{report.question.slice(0, 60)} · {new Date(report.created_at).toLocaleDateString('de-DE')}</button>)}</div></section>}
    {active && <section ref={reportRef} className="card research-card" aria-label="Recherchebericht">
      <h3>{active.question}</h3>
      {active.parent_report_id && <p><strong>Getrennter Nachrecherchebericht.</strong> Offener Punkt: {active.open_point || active.question}. <button type="button" onClick={() => void openReport(active.parent_report_id!)}>Ursprungsbericht öffnen</button></p>}
      <button type="button" onClick={() => downloadResearchMarkdown(active)}>Bericht als Markdown herunterladen</button>
      <p>{active.search_provider === 'Direkte URLs' ? 'Direkt angegebene Quellen' : <>Suche: {active.search_provider} · „{active.search_query}“</>} · Modell: {active.provider}/{active.model} · Erstellt: {new Date(active.created_at).toLocaleString('de-DE')}</p>
      {active.project_context && <><h4>Lokaler Projektkontext</h4><p>„{active.project_context.project}“ · Arbeitsstand-Version {active.project_context.version} vom {new Date(active.project_context.updated_at).toLocaleString('de-DE')}. Dieser manuell bestätigte Stand ist Kontext, kein öffentlicher Quellenbeleg.</p><p><strong>Bestätigt:</strong> {active.project_context.confirmed || 'Keine Angabe'}</p><p><strong>Offene Fragen:</strong> {active.project_context.open_questions || 'Keine Angabe'}</p><p><strong>Nächster Schritt:</strong> {active.project_context.next_step}</p>{active.project_context.truncated && <p className="muted">Langer Arbeitsstand für diesen Bericht gekürzt.</p>}</>}
      <h4>Wörtliche Fundstellen</h4>
      <p>Diese Aussagen stammen aus den verlinkten Seiten. Ihr Wahrheitsgehalt wurde nicht unabhängig geprüft.</p>
      {active.claims.length ? <ol>{active.claims.map((claim, index) => <li key={index}><p>{claim.statement}</p><ul>{claim.evidence.map((item, evidenceIndex) => <li key={evidenceIndex}>[{item.source_id}] „{item.quote}“</li>)}</ul></li>)}</ol> : <p>Keine automatisch wortgetreu geprüfte Aussage. Lies die Fundstellen unten selbst.</p>}
      {active.comparison && <ResearchComparison comparison={active.comparison} />}
      {active.interpretation && <><h4>Schlussfolgerung des Modells</h4><p>{active.interpretation}</p></>}
      <h4>Unsicherheit und Grenzen</h4><p>{active.uncertainty || 'Keine weitere Unsicherheit angegeben; Quellen bitte selbst prüfen.'}</p>
      {active.errors.length > 0 && <ul>{active.errors.map((message, index) => <li key={index}>{message}</li>)}</ul>}
      <h4>Gelesene Quellen</h4>
      <ol>{active.sources.map((source) => <li key={source.id}><a href={source.url} target="_blank" rel="noopener noreferrer">[{source.id}] {source.title}</a><br />{mediaName(source.media_type)} · Abruf: {new Date(source.retrieved_at).toLocaleString('de-DE')}{published(source.published_at)} · {source.text.length} Textzeichen{source.caption_error && <><br /><strong>Untertitel nicht verfügbar:</strong> {source.caption_error}</>}{source.found_by_query && <><br />Gefunden über {source.found_by_provider}: „{source.found_by_query}“</>}<details><summary>Gelesenen Ausschnitt ansehen</summary><p style={{ whiteSpace: 'pre-wrap' }}>{source.text}</p></details></li>)}</ol>
      <ResearchFollowUpList key={`children-${active.id}`} reportId={active.id} onOpen={(reportId) => void openReport(reportId)} />
      <ResearchFollowUp key={`follow-up-${active.id}`} report={active} configured={configured} provider={provider} onCreated={followUpCreated} />
      <ResearchEvaluation key={`evaluation-${active.id}`} report={active} onSaved={evaluationSaved} />
    </section>}
    <section className="card research-card"><h3>Gesicherte Recherche-Testfälle</h3><p>Bewusste Fehlerbewertungen mit erwartetem Ergebnis. Sie werden nicht automatisch erneut ausgeführt.</p>
      <button type="button" disabled={!cases.length || exportBusy} onClick={() => void exportCases()}>{exportBusy ? 'Exportiere…' : 'Testfälle als JSONL herunterladen'}</button>
      {exportError && <p className="notice error" role="alert">{exportError}</p>}
      <p className="muted">Die lokale Datei enthält Bewertungen, Fragen, Modelltext und gelesene öffentliche Quellenausschnitte. Der gesonderte Projektarbeitsstand und App-Zugangsdaten werden nicht angehängt. Prüfe vor dem Weitergeben, ob eigener Text private Angaben enthält.</p>
      <div className="item-list">{cases.length ? cases.map((item) => <div className="item-row" key={item.report_id}><div><strong>{item.question}</strong><small>{item.provider}/{item.model} · {verdictLabels[item.verdict]} · {sourceLabels[item.source_quality]}</small><p>Beobachtet: {item.interpretation || '(keine Modell-Einordnung)'}</p><p>Begründung: {item.reason}</p><p>Erwartet: {item.expected_result}</p></div><button type="button" onClick={() => void api<Report>(`/research/${item.report_id}`).then(setActive).catch((cause: Error) => setError(cause.message))}>Bericht öffnen</button></div>) : <p>Noch keine Recherche-Testfälle gespeichert.</p>}</div>
    </section>
  </>;
}
