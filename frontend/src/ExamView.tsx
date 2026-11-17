import { useEffect, useState } from 'react';
import { examResults, remainingSeconds, selectExamCards,
  type ExamCard, type ExamGrade } from './exam';
import { examCsv } from './examCsv';

export default function ExamView({ sessions, onExit }: {
  sessions: ExamCard[]; onExit: () => void;
}) {
  const [phase, setPhase] = useState<'setup' | 'running' | 'submitted'>('setup');
  const [requested, setRequested] = useState(5);
  const [minutes, setMinutes] = useState(10);
  const [cards, setCards] = useState<ExamCard[]>([]);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [grades, setGrades] = useState<Record<string, ExamGrade | undefined>>({});
  const [deadline, setDeadline] = useState(0);
  const [remaining, setRemaining] = useState(0);
  const [timedOut, setTimedOut] = useState(false);
  const available = selectExamCards(sessions, 10).length;
  const results = examResults(cards, answers, grades);

  useEffect(() => {
    if (phase !== 'running') return;
    const tick = () => {
      const left = remainingSeconds(deadline, Date.now());
      setRemaining(left);
      if (left === 0) { setTimedOut(true); setPhase('submitted'); }
    };
    tick();
    const timer = window.setInterval(tick, 1000);
    document.addEventListener('visibilitychange', tick);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [phase, deadline]);

  const start = () => {
    const chosen = selectExamCards(sessions, requested);
    if (!chosen.length) return;
    setCards(chosen);
    setAnswers({}); setGrades({}); setTimedOut(false);
    setDeadline(Date.now() + minutes * 60_000);
    setRemaining(minutes * 60);
    setPhase('running');
  };

  const downloadReport = () => {
    if (phase !== 'submitted' || cards.length === 0) return;
    const blob = new Blob([examCsv(cards, answers, grades)],
      { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `jarvis-pruefung-${new Date().toISOString().slice(0, 10)}.csv`;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return <div className="stack exam-view">
    <p className="eyebrow">LOKALE ÜBUNGSPRÜFUNG</p><h2>Prüfungsmodus</h2>
    {phase === 'setup' ? <section className="card">
      <p>Wähle Fragen aus deinen beantworteten, belegten Lernrunden. Während der Prüfung bleiben Lösungen und Quellen verborgen. Nach Abgabe prüfst du deine Antworten selbst anhand der Belege.</p>
      <p>{available} unterschiedliche Frage(n) verfügbar.</p>
      <label>Anzahl Fragen<select value={requested}
        onChange={(event) => setRequested(Number(event.target.value))}>
        {[1, 3, 5, 10].map((count) => <option key={count} value={count}>{count}</option>)}
      </select></label>
      <label>Zeitlimit<select value={minutes}
        onChange={(event) => setMinutes(Number(event.target.value))}>
        {[5, 10, 20].map((count) => <option key={count} value={count}>{count} Minuten</option>)}
      </select></label>
      <div className="item-actions"><button className="primary" onClick={start}
        disabled={available === 0}>Prüfung starten</button>
        <button onClick={onExit}>Zurück zum Lernmodus</button></div>
      <small>Die Prüfung läuft nur in diesem Tab. Antworten und Ergebnis werden nicht gespeichert.</small>
    </section> : <>
      <section className="card">
        {phase === 'running' ? <>
          <p role="timer">Verbleibende Zeit: {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')}</p>
          <p>Antworten bleiben bis zur Abgabe bearbeitbar.</p>
          <button className="primary" onClick={() => setPhase('submitted')}>Prüfung abgeben</button>
        </> : <>
          <p role="status">{timedOut ? 'Zeit abgelaufen. Prüfung automatisch abgegeben.'
            : 'Prüfung abgegeben. Vergleiche deine Antworten mit den Quellen.'}</p>
          <p>Richtig: {results.correct} · Teilweise: {results.partial} · Falsch/leer: {results.incorrect} · Noch nicht bewertet: {results.unrated}</p>
        </>}
      </section>
      {cards.map((card, index) => <section className="card" key={card.id}>
        <h3>Frage {index + 1}</h3><p><strong>{card.question}</strong></p>
        <label>Deine Prüfungsantwort<textarea aria-label={`Prüfungsantwort ${index + 1}`}
          value={answers[card.id] ?? ''} maxLength={2000}
          onChange={(event) => setAnswers((current) => ({ ...current,
            [card.id]: event.target.value }))} disabled={phase !== 'running'} /></label>
        {phase === 'submitted' && <div className="memory-form">
          <p><strong>Musterantwort:</strong> {card.reference_answer}</p>
          <p><strong>Quellenzitat:</strong> „{card.evidence_quote}“</p>
          <p className="muted">{card.source.path}{card.source.page
            ? ` · Seite ${card.source.page}` : ''} · Fundstelle {card.source.passage_id}
            · SHA-256 {card.source.sha256}</p>
          {answers[card.id]?.trim() ? <label>Selbsteinschätzung {index + 1}
            <select value={grades[card.id] ?? ''}
              onChange={(event) => setGrades((current) => ({ ...current,
                [card.id]: event.target.value as ExamGrade || undefined }))}>
              <option value="">Bitte wählen</option>
              <option value="correct">Richtig</option>
              <option value="partial">Teilweise richtig</option>
              <option value="incorrect">Falsch</option>
            </select></label> : <p>Leere Antwort: als falsch gewertet.</p>}
        </div>}
      </section>)}
      {phase === 'submitted' && <section className="card"><h3>Wiederholen</h3>
        {results.repeat.length ? <ul>{results.repeat.map((question, index) =>
          <li key={`${question}-${index}`}>{question}</li>)}</ul> :
          <p>{results.unrated ? 'Bewerte deine Antworten, um Wiederholungsziele zu sehen.'
            : 'Keine Wiederholungsfragen aus dieser Runde.'}</p>}
        <p>Das Protokoll enthält {cards.length} Frage(n), deine Antworten, die Selbsteinschätzung und die Quellen. Prüfe persönliche Dateipfade vor dem Teilen.</p>
        <div className="item-actions"><button onClick={downloadReport}>Prüfungsprotokoll als CSV herunterladen</button>
          <button onClick={() => setPhase('setup')}>Neue Prüfung</button>
          <button onClick={onExit}>Zurück zum Lernmodus</button></div>
      </section>}
    </>}
  </div>;
}
