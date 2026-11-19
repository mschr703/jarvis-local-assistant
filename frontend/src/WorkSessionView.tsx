import { useEffect, useState } from 'react';

import { api } from './api';

type State = { id: string; version: number; project: string; next_step: string;
  documents: { id: string; path: string; status: string }[];
  tasks: { id: string; title: string; current_title: string | null; status: string }[];
  links: string[] };
type Session = { id: string; state_version: number; project_snapshot: string;
  result: string; open_questions: string; next_step: string; created_at: string;
  state_changed: boolean;
  documents: { id: string; path: string; status: string; in_state: boolean }[];
  tasks: { id: string; title: string; current_title: string | null; status: string;
    saved_status: string;
    due_status: string; in_state: boolean }[];
  links: { url: string; in_state: boolean; status: 'nicht_geprueft' }[] };
type Form = { result: string; open_questions: string; next_step: string;
  document_ids: string[]; task_ids: string[]; links: string[] };
const emptyForm = (): Form => ({ result: '', open_questions: '',
  next_step: '', document_ids: [], task_ids: [], links: [] });
const fileStatus: Record<string, string> = { unveraendert: 'Unverändert', geaendert: 'Geändert',
  fehlt: 'Fehlt', ausgeschlossen: 'Ausgeschlossen', freigabe_entfernt: 'Freigabe entfernt',
  pfad_geaendert: 'Pfad geändert', index_ausstehend: 'Index ausstehend',
  nicht_verfuegbar: 'Nicht verfügbar' };
const taskStatus: Record<string, string> = { open: 'Offen', done: 'Erledigt',
  geloescht: 'Gelöscht' };

export default function WorkSessionView({ state }: { state: State }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [form, setForm] = useState<Form>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [deleteCandidate, setDeleteCandidate] = useState<string | null>(null);
  const endpoint = `/work-states/${state.id}/sessions`;
  const load = async () => setSessions(await api<Session[]>(endpoint));
  useEffect(() => {
    let current = true;
    void api<Session[]>(endpoint).then((items) => { if (current) setSessions(items); })
      .catch((error: Error) => { if (current) setNotice(error.message); });
    return () => { current = false; };
  }, [endpoint, state.version]);
  const toggle = (field: 'document_ids' | 'task_ids' | 'links', id: string) =>
    setForm((previous) => ({ ...previous, [field]: previous[field].includes(id)
      ? previous[field].filter((item) => item !== id) : [...previous[field], id] }));
  const jumpTo = (kind: 'document' | 'task', id: string) =>
    window.document.getElementById(`project-${kind}-${id}`)?.scrollIntoView({ block: 'center' });
  const save = async () => {
    if (busy) return;
    setBusy(true); setNotice('');
    try {
      await api<Session>(endpoint, { method: 'POST', body: JSON.stringify({ ...form,
        expected_state_version: state.version }) });
      setForm(emptyForm());
      await load();
      setNotice('Sitzungsnotiz gespeichert. Nur manuell erfasste Ergebnisse wurden übernommen.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };
  const remove = async (id: string) => {
    if (busy) return;
    setBusy(true); setNotice('');
    try {
      await api(`${endpoint}/${id}`, { method: 'DELETE' });
      setDeleteCandidate(null); await load(); setNotice('Sitzungsnotiz entfernt.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };
  const refresh = async () => {
    setBusy(true); setNotice('');
    try { await load(); setNotice('Dateien und Aufgaben erneut geprüft. Externe Links wurden nicht aufgerufen.'); }
    catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };

  return <section className="card work-session-card" aria-label="Sitzungsnotizen zum Arbeitsstand">
    <div className="card-heading"><h3>Sitzungsnotizen & Wiedereinstieg · {state.project}</h3>
      <button type="button" disabled={busy} onClick={() => void refresh()}>Notizen neu prüfen</button></div>
    <p>Halte fest, was du tatsächlich getan hast. Wähle nur passende Belege aus diesem Arbeitsstand. Jarvis prüft beim Wiederöffnen deren aktuellen Status; Links bleiben ungeprüft. Die Notiz ändert keine Aufgabe automatisch.</p>
    {notice && <p role="status" className="notice">{notice}</p>}
    <div className="work-form">
      <label>Erreichtes Ergebnis<textarea aria-label="Sitzungsergebnis" maxLength={5000} value={form.result}
        onChange={(event) => setForm({ ...form, result: event.target.value })} /></label>
      <label>Offene Fragen<textarea aria-label="Offene Sitzungsfragen" maxLength={3000} value={form.open_questions}
        onChange={(event) => setForm({ ...form, open_questions: event.target.value })} /></label>
      <label>Nächster Schritt<textarea aria-label="Nächster Sitzungsschritt" maxLength={2000}
        placeholder={`Im Arbeitsstand: ${state.next_step}`} value={form.next_step}
        onChange={(event) => setForm({ ...form, next_step: event.target.value })} /></label>
      {state.documents.length > 0 && <fieldset><legend>Dateien als Beleg</legend>{state.documents.map((item) =>
        <label key={item.id}><input type="checkbox" checked={form.document_ids.includes(item.id)}
          disabled={busy || item.status !== 'unveraendert'}
          onChange={() => toggle('document_ids', item.id)} /> {item.path} · {fileStatus[item.status] || item.status}</label>)}</fieldset>}
      {state.tasks.length > 0 && <fieldset><legend>Aufgaben als Beleg</legend>{state.tasks.map((item) =>
        <label key={item.id}><input type="checkbox" checked={form.task_ids.includes(item.id)}
          disabled={busy || item.status === 'geloescht'}
          onChange={() => toggle('task_ids', item.id)} /> {item.current_title || item.title} · {taskStatus[item.status] || item.status}</label>)}</fieldset>}
      {state.links.length > 0 && <fieldset><legend>Links als Verweis (Inhalt ungeprüft)</legend>{state.links.map((link) =>
        <label key={link}><input type="checkbox" checked={form.links.includes(link)} disabled={busy}
          onChange={() => toggle('links', link)} /> {link}</label>)}</fieldset>}
      <button className="primary" type="button" disabled={busy || !form.result.trim() || !form.next_step.trim()}
        onClick={() => void save()}>Sitzungsnotiz sichern</button>
    </div>
    <h4>Gespeicherte Sitzungen</h4>
    {sessions.length === 0 ? <p>Noch keine Sitzungsnotiz.</p> : <div className="item-list">{sessions.map((item) =>
      <article className="item-row" key={item.id}><div>
        <strong>{new Date(item.created_at).toLocaleString('de-DE')} · {item.project_snapshot}</strong>
        <small>Arbeitsstand-Fassung {item.state_version}{item.state_changed ? ' · Arbeitsstand inzwischen geändert' : ''}</small>
        <p><strong>Erreicht:</strong> {item.result}</p>
        <p><strong>Offen:</strong> {item.open_questions || 'Keine Frage notiert'}</p>
        <p><strong>Weiter:</strong> {item.next_step}</p>
        {item.documents.length > 0 && <ul>{item.documents.map((file) =>
          <li key={file.id}>Datei: {file.path} · {fileStatus[file.status] || file.status}
            {!file.in_state ? ' · nicht mehr im Arbeitsstand' : <button type="button"
              onClick={() => jumpTo('document', file.id)}>Dateibeleg ansehen</button>}</li>)}</ul>}
        {item.tasks.length > 0 && <ul>{item.tasks.map((task) =>
          <li key={task.id}>Aufgabe: {task.title}{task.current_title && task.current_title !== task.title
            ? ` → ${task.current_title}` : ''} · {taskStatus[task.status] || task.status}
            {task.saved_status !== task.status ? ` (beim Sichern: ${taskStatus[task.saved_status] || task.saved_status})` : ''}
            {!task.in_state ? ' · nicht mehr im Arbeitsstand' : <button type="button"
              onClick={() => jumpTo('task', task.id)}>Aufgabe ansehen</button>}</li>)}</ul>}
        {item.links.length > 0 && <ul>{item.links.map((link) =>
          <li key={link.url}>Link: {link.url} · ungeprüft{!link.in_state && ' · nicht mehr im Arbeitsstand'}
            <a href={link.url} target="_blank" rel="noopener noreferrer">Link öffnen</a></li>)}</ul>}
      </div><div className="item-actions">{deleteCandidate === item.id ? <>
        <button type="button" disabled={busy} onClick={() => void remove(item.id)}>Wirklich entfernen</button>
        <button type="button" disabled={busy} onClick={() => setDeleteCandidate(null)}>Abbrechen</button>
      </> : <button type="button" disabled={busy} onClick={() => setDeleteCandidate(item.id)}>Entfernen…</button>}</div></article>)}</div>}
  </section>;
}
