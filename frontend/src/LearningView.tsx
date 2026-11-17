import { useEffect, useRef, useState } from 'react';
import { api } from './api';
import type { Source } from './DocumentView';

type Evaluation = 'correct' | 'partial' | 'incorrect' | 'uncertain' | null;
type LearningSession = {
  id: string; topic: string; question: string; reference_answer: string;
  evidence_quote: string; source: Source; user_answer: string | null;
  evaluation: Evaluation; feedback: string | null; feedback_quote: string | null;
  next_question: string | null; version: number; created_at: string; updated_at: string;
};

const evaluationLabel: Record<Exclude<Evaluation, null>, string> = {
  correct: 'Richtig', partial: 'Teilweise richtig', incorrect: 'Noch nicht richtig',
  uncertain: 'Nicht sicher bewertbar',
};

export default function LearningView({ visible, sources, configured, provider, onDocuments }: {
  visible: boolean; sources: Source[]; configured: boolean; provider: string;
  onDocuments: () => void;
}) {
  const [sessions, setSessions] = useState<LearningSession[]>([]);
  const [activeId, setActiveId] = useState('');
  const [topic, setTopic] = useState('');
  const [answer, setAnswer] = useState('');
  const [questionEdit, setQuestionEdit] = useState('');
  const [referenceEdit, setReferenceEdit] = useState('');
  const [quoteEdit, setQuoteEdit] = useState('');
  const [showCorrection, setShowCorrection] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const request = useRef(0);
  const active = sessions.find((item) => item.id === activeId) ?? null;
  const selected = sources.slice(0, 3);

  useEffect(() => {
    if (!visible) return;
    const current = ++request.current;
    void api<LearningSession[]>('/learning/sessions').then((items) => {
      if (current !== request.current) return;
      setSessions(items);
      setActiveId((previous) => items.some((item) => item.id === previous)
        ? previous : items[0]?.id ?? '');
    }).catch((error: Error) => { if (current === request.current) setNotice(error.message); });
    return () => { ++request.current; };
  }, [visible]);

  useEffect(() => {
    if (!active) return;
    setAnswer(active.user_answer ?? '');
    setQuestionEdit(active.question);
    setReferenceEdit(active.reference_answer);
    setQuoteEdit(active.evidence_quote);
    setShowCorrection(false);
  }, [active?.id, active?.version]);

  const replace = (item: LearningSession) => {
    setSessions((current) => [item, ...current.filter((old) => old.id !== item.id)]);
    setActiveId(item.id);
  };
  const create = async () => {
    if (busy || !selected.length || !topic.trim()) return;
    setBusy(true); setNotice('Ollama erstellt eine belegte Lernfrage…');
    try {
      const item = await api<LearningSession>('/learning/sessions', {
        method: 'POST', body: JSON.stringify({ topic: topic.trim(),
          passage_ids: selected.map((source) => source.passage_id) }),
      });
      replace(item); setNotice('Lernfrage erstellt. Prüfe Frage und Beleg vor dem Antworten.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };
  const correct = async () => {
    if (!active || busy || active.user_answer !== null) return;
    setBusy(true);
    try {
      const item = await api<LearningSession>(`/learning/sessions/${active.id}`, {
        method: 'PATCH', body: JSON.stringify({ expected_version: active.version,
          question: questionEdit, reference_answer: referenceEdit,
          evidence_quote: quoteEdit }),
      });
      replace(item); setNotice('Frage, Musterantwort und Beleg korrigiert.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };
  const submit = async () => {
    if (!active || busy || !answer.trim()) return;
    setBusy(true); setNotice('Ollama prüft deine Antwort anhand der Quelle…');
    try {
      const item = await api<LearningSession>(`/learning/sessions/${active.id}/answer`, {
        method: 'POST', body: JSON.stringify({ expected_version: active.version, answer }),
      });
      replace(item); setNotice('Antwort gespeichert. Die Modellbewertung bitte am Quellenbeleg prüfen.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };
  const remove = async () => {
    if (!active || busy || !window.confirm('Diese Lernrunde mit Antwort endgültig löschen?')) return;
    setBusy(true);
    try {
      await api(`/learning/sessions/${active.id}`, { method: 'DELETE' });
      const remaining = sessions.filter((item) => item.id !== active.id);
      setSessions(remaining); setActiveId(remaining[0]?.id ?? '');
      setNotice('Lernrunde gelöscht. Die Quelldatei bleibt erhalten.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };

  return <div className="stack">
    <p className="eyebrow">LOKAL LERNEN</p><h2>Lernmodus</h2>
    <p className="intro">Stelle dir Fragen zu ausgewählten Dokumentstellen. Jarvis speichert nur deine tatsächlichen Antworten und zeigt den Quellenbeleg zu jeder Rückmeldung.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Neue Lernrunde</h3>
      {selected.length ? <><p>Ausgewählte Fundstellen: {selected.map((item) => item.path).join(' · ')}</p>
        {sources.length > 3 && <p className="notice">Für den Lernmodus werden höchstens die ersten drei Fundstellen verwendet.</p>}</> :
        <p>Wähle unter „Dokumente“ eine bis drei Fundstellen und öffne von dort den Lernmodus.</p>}
      <div className="item-actions"><button onClick={onDocuments}>Dokumente öffnen</button></div>
      <label>Lernthema<input value={topic} onChange={(event) => setTopic(event.target.value)}
        maxLength={160} placeholder="Zum Beispiel: Grundlagen der Photosynthese" /></label>
      <button className="primary" onClick={() => void create()} disabled={busy || !configured || provider !== 'ollama' ||
        !selected.length || topic.trim().length < 3}>Belegte Frage erstellen</button>
      {provider !== 'ollama' && <small>Der Lernmodus verwendet derzeit nur lokales Ollama.</small>}
    </section>
    <section className="card"><h3>Gespeicherte Lernrunden</h3>
      {sessions.length === 0 ? <p>Noch keine Lernrunde gespeichert.</p> :
        <div className="item-list">{sessions.map((item) =>
          <button key={item.id} disabled={busy} onClick={() => setActiveId(item.id)}>
            {item.topic} · {item.user_answer === null ? 'Offene Frage' : evaluationLabel[item.evaluation ?? 'uncertain']}
          </button>)}</div>}
    </section>
    {active && <section className="card"><h3>Frage und Antwort</h3>
      <p><strong>{active.question}</strong></p>
      <p>Beleg: „{active.evidence_quote}“</p>
      <p className="muted">{active.source.path}{active.source.page ? ` · Seite ${active.source.page}` : ''} · Fundstelle {active.source.passage_id} · SHA-256 {active.source.sha256}</p>
      {active.user_answer === null && <div className="item-actions"><button onClick={() => setShowCorrection((value) => !value)} disabled={busy}>
        {showCorrection ? 'Korrektur schließen' : 'Frage/Musterantwort korrigieren'}</button></div>}
      {showCorrection && active.user_answer === null && <div className="memory-form">
        <p className="muted">Änderungen an Frage und Musterantwort selbst gegen den Beleg prüfen. Das Zitat muss wörtlich in der Fundstelle stehen.</p>
        <label>Frage<textarea aria-label="Lernfrage korrigieren" value={questionEdit} onChange={(event) => setQuestionEdit(event.target.value)} /></label>
        <label>Musterantwort<textarea aria-label="Musterantwort korrigieren" value={referenceEdit} onChange={(event) => setReferenceEdit(event.target.value)} /></label>
        <label>Quellenzitat<textarea aria-label="Quellenzitat korrigieren" value={quoteEdit} onChange={(event) => setQuoteEdit(event.target.value)} /></label>
        <button onClick={() => void correct()} disabled={busy || questionEdit.trim().length < 10 || !referenceEdit.trim() || quoteEdit.trim().length < 8}>Korrektur speichern</button>
      </div>}
      <label>Deine Antwort<textarea aria-label="Meine Lernantwort" value={answer}
        onChange={(event) => setAnswer(event.target.value)} maxLength={2000} disabled={busy} /></label>
      <button className="primary" onClick={() => void submit()} disabled={busy || provider !== 'ollama' || !answer.trim() ||
        answer === active.user_answer || (active.user_answer === null && showCorrection)}>
        {active.user_answer === null ? 'Antwort prüfen' : 'Korrigierte Antwort erneut prüfen'}</button>
      {active.user_answer !== null && <div className="memory-form"><h4>{evaluationLabel[active.evaluation ?? 'uncertain']}</h4>
        <p>{active.feedback}</p>
        {active.feedback_quote && <p>Rückmeldungsbeleg: „{active.feedback_quote}“</p>}
        <p><strong>Musterantwort:</strong> {active.reference_answer}</p>
        {active.next_question && <p><strong>Mögliche Folgefrage:</strong> {active.next_question}</p>}
        <small>Die Bewertung kommt von einem lokalen Modell. Prüfe sie selbst anhand der Fundstelle.</small>
      </div>}
      <div className="item-actions"><button onClick={() => void remove()} disabled={busy}>Lernrunde löschen</button></div>
    </section>}
  </div>;
}
