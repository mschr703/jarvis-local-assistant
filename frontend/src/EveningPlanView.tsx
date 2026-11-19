import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Task = { id: string; title: string; status: 'open' | 'done'; priority: number; due_local: string | null };
type PlanItem = { task_id: string; estimate_minutes: number; priority: number };
type PlanInput = { date_local: string; timezone: string; start_time: string; sleep_time: string; work_minutes: number; break_minutes: number; items: PlanItem[] };
type Segment = { kind: 'work' | 'break'; task_id: string | null; title: string; start_local: string; end_local: string; minutes: number };
type Remainder = { task_id: string; title: string; remaining_minutes: number; reason: 'no_time' | 'deadline' | 'done' | 'missing' };
type Result = { window_start_local: string; sleep_local: string; window_minutes: number; available_minutes: number; planned_minutes: number; break_minutes: number; segments: Segment[]; remainder: Remainder[]; source_hash: string };
type Saved = { id: string; input: PlanInput; result: Result; version: number; updated_at: string; stale?: boolean };

const todayIn = (timezone: string) => {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const part = (type: string) => parts.find((item) => item.type === type)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
};
const localLabel = (value: string) => `${value.slice(0, 16).replace('T', ' ')} · UTC${value.slice(-6)}`;
const reasonLabel: Record<Remainder['reason'], string> = {
  no_time: 'Keine freie Zeit vor dem Schlafziel', deadline: 'Frist erreicht',
  done: 'Aufgabe bereits erledigt', missing: 'Aufgabe wurde gelöscht',
};

export default function EveningPlanView({ visible }: { visible: boolean }) {
  const [draft, setDraft] = useState<PlanInput>(() => ({ date_local: todayIn('Europe/Paris'), timezone: 'Europe/Paris', start_time: '18:00', sleep_time: '23:00', work_minutes: 45, break_minutes: 10, items: [] }));
  const [tasks, setTasks] = useState<Task[]>([]);
  const [saved, setSaved] = useState<Saved[]>([]);
  const [active, setActive] = useState<Saved | null>(null);
  const [preview, setPreview] = useState<{ key: string; result: Result } | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const operation = useRef(false);
  const loaded = useRef(false);
  const dateTouched = useRef(false);
  const timezoneTouched = useRef(false);
  const request = useRef(0);
  const draftKey = JSON.stringify(draft);

  const refresh = async () => {
    const epoch = ++request.current;
    const [nextTasks, nextSaved] = await Promise.all([api<Task[]>('/tasks'), api<Saved[]>('/evening-plans')]);
    if (epoch === request.current) { setTasks(nextTasks); setSaved(nextSaved); }
  };

  useEffect(() => {
    if (!visible) return;
    let alive = true;
    if (!loaded.current) {
      loaded.current = true;
      void api<{ timezone: string }>('/settings').then((settings) => {
        if (!alive) return;
        setDraft((current) => ({ ...current,
          timezone: timezoneTouched.current ? current.timezone : settings.timezone,
          date_local: dateTouched.current ? current.date_local : todayIn(settings.timezone) }));
      }).catch((error: Error) => { if (alive) setNotice(error.message); });
    }
    void refresh().catch((error: Error) => { if (alive) setNotice(error.message); });
    return () => { alive = false; ++request.current; };
  }, [visible]);

  const update = (change: Partial<PlanInput>) => { setDraft((current) => ({ ...current, ...change })); setNotice(''); };
  const toggleTask = (task: Task) => {
    update({ items: draft.items.some((item) => item.task_id === task.id)
      ? draft.items.filter((item) => item.task_id !== task.id)
      : [...draft.items, { task_id: task.id, estimate_minutes: 45, priority: task.priority }] });
  };
  const changeItem = (taskId: string, change: Partial<PlanItem>) => update({ items: draft.items.map((item) => item.task_id === taskId ? { ...item, ...change } : item) });
  const run = async (work: () => Promise<void>) => {
    if (operation.current) return;
    operation.current = true; setBusy(true); setNotice('');
    try { await work(); } catch (error) { setNotice((error as Error).message); }
    finally { operation.current = false; setBusy(false); }
  };
  const showPreview = () => run(async () => {
    const key = JSON.stringify(draft);
    const result = await api<Result>('/evening-plans/preview', { method: 'POST', body: key });
    setPreview({ key, result });
    setNotice('Vorschau berechnet. Noch nicht gespeichert.');
  });
  const save = () => run(async () => {
    if (!preview || preview.key !== JSON.stringify(draft)) throw new Error('Zuerst die aktuellen Eingaben berechnen.');
    const item = active
      ? await api<Saved>(`/evening-plans/${active.id}`, { method: 'PUT', body: JSON.stringify({ ...draft, version: active.version }) })
      : await api<Saved>('/evening-plans', { method: 'POST', body: JSON.stringify(draft) });
    setActive(item); setPreview({ key: JSON.stringify(draft), result: item.result });
    await refresh(); setNotice(active ? 'Abendplan neu berechnet und gespeichert.' : 'Abendplan gespeichert.');
  });
  const load = (id: string) => run(async () => {
    const item = await api<Saved>(`/evening-plans/${id}`);
    setActive(item); setDraft(item.input); setPreview({ key: JSON.stringify(item.input), result: item.result });
    setConfirmDelete(false);
    setNotice(item.stale ? 'Aufgaben oder Zeitblöcke wurden geändert. Bitte neu berechnen und speichern.' : 'Gespeicherten Plan geladen.');
  });
  const remove = () => run(async () => {
    if (!active || !confirmDelete) return;
    await api(`/evening-plans/${active.id}?version=${active.version}`, { method: 'DELETE' });
    setActive(null); setPreview(null); setConfirmDelete(false); await refresh();
    setNotice('Abendplan gelöscht.');
  });
  const result = preview?.key === draftKey ? preview.result : null;

  return <>
    <p className="eyebrow">ABENDPLANUNG</p>
    <h2>Freie Zeit bewusst planen</h2>
    <p className="intro">Wähle offene Aufgaben und eine realistische Schätzung. Der Plan nutzt die freien Zeitfenster aus dem Zeitmodell und endet spätestens zum Schlafziel. Alles bleibt lokal.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card">
      <div className="card-heading"><h3>Abend und Pausen</h3>{active && <span className="badge pending">Gespeichert · Version {active.version}{active.stale ? ' · veraltet' : ''}</span>}</div>
      <div className="form-grid">
        <label>Datum<input type="date" value={draft.date_local} onChange={(event) => { dateTouched.current = true; update({ date_local: event.target.value }); }} /></label>
        <label>Zeitzone<input value={draft.timezone} onChange={(event) => { timezoneTouched.current = true; update({ timezone: event.target.value }); }} /></label>
        <label>Abendbeginn<input type="time" value={draft.start_time} onChange={(event) => update({ start_time: event.target.value })} /></label>
        <label>Schlafziel<input type="time" value={draft.sleep_time} onChange={(event) => update({ sleep_time: event.target.value })} /></label>
        <label>Arbeitsphase (Minuten)<input type="number" min="10" max="120" value={draft.work_minutes} onChange={(event) => update({ work_minutes: Number(event.target.value) })} /></label>
        <label>Pause (Minuten)<input type="number" min="5" max="60" value={draft.break_minutes} onChange={(event) => update({ break_minutes: Number(event.target.value) })} /></label>
      </div>
      <p>Liegt das Schlafziel vor dem Beginn, gilt es für den nächsten Kalendertag. Feste Termine und Puffer pflegst du im Bereich „Zeitmodell“.</p>
    </section>
    <section className="card">
      <div className="card-heading"><h3>Aufgaben</h3><button disabled={busy} onClick={() => void run(async () => { await refresh(); setNotice('Aufgaben und Pläne aktualisiert.'); })}>Aktualisieren</button></div>
      {tasks.filter((task) => task.status === 'open').length === 0 && <p>Keine offenen Aufgaben. Lege zuerst im Bereich „Aufgaben“ eine Aufgabe an.</p>}
      {tasks.filter((task) => task.status === 'open').map((task) => <label key={task.id} className="plan-task">
        <input type="checkbox" checked={draft.items.some((item) => item.task_id === task.id)} disabled={busy || (draft.items.length >= 20 && !draft.items.some((item) => item.task_id === task.id))} onChange={() => toggleTask(task)} />
        <span>{task.title}{task.due_local ? ` · fällig ${task.due_local.replace('T', ' ')}` : ''}</span>
      </label>)}
      {draft.items.map((entry) => {
        const task = tasks.find((candidate) => candidate.id === entry.task_id);
        return <div className="plan-item" key={entry.task_id}>
          <strong>{task?.title || 'Gelöschte Aufgabe'}{task?.status === 'done' ? ' · erledigt' : ''}</strong>
          <label>Schätzung (Minuten)<input type="number" min="5" max="480" value={entry.estimate_minutes} onChange={(event) => changeItem(entry.task_id, { estimate_minutes: Number(event.target.value) })} /></label>
          <label>Priorität (1–5)<input type="number" min="1" max="5" value={entry.priority} onChange={(event) => changeItem(entry.task_id, { priority: Number(event.target.value) })} /></label>
          <button disabled={busy} onClick={() => update({ items: draft.items.filter((item) => item.task_id !== entry.task_id) })}>Entfernen</button>
        </div>;
      })}
      <div className="item-actions">
        <button disabled={busy || !draft.items.length} onClick={() => void showPreview()}>Plan berechnen</button>
        <button disabled={busy || !result} onClick={() => void save()}>{active ? 'Neu berechnen und speichern' : 'Plan speichern'}</button>
        {active && <button disabled={busy} onClick={() => { setActive(null); setPreview(null); setConfirmDelete(false); setNotice('Neuer Plan begonnen.'); }}>Neuer Plan</button>}
      </div>
    </section>
    {result && <section className="card" aria-label="Planvorschau">
      <h3>{active ? 'Plan und Vorschau' : 'Vorschau'}</h3>
      <p>Fenster: {localLabel(result.window_start_local)} bis {localLabel(result.sleep_local)}. Frei: {result.available_minutes} Min. Eingeplant: {result.planned_minutes} Min. Pausen: {result.break_minutes} Min.</p>
      {active?.stale && <p className="notice">Der gespeicherte Stand ist veraltet. „Plan berechnen“ berücksichtigt aktuelle Aufgaben und Zeitblöcke; speichere danach erneut.</p>}
      {result.segments.length === 0 && <p>In diesem Zeitfenster ist keine Arbeitszeit frei.</p>}
      <ol className="plan-timeline">{result.segments.map((part, index) => <li key={`${part.start_local}-${index}`}>
        <strong>{part.kind === 'break' ? 'Pause' : part.title}</strong> · {localLabel(part.start_local)} bis {localLabel(part.end_local)} · {part.minutes} Min.
      </li>)}</ol>
      <h4>Nicht eingeplant</h4>
      {result.remainder.length === 0 ? <p>Alle ausgewählten Aufgaben passen in den Abend.</p> :
        <ul>{result.remainder.map((rest) => <li key={rest.task_id}>{rest.title}: {rest.remaining_minutes} Min. offen · {reasonLabel[rest.reason]}</li>)}</ul>}
    </section>}
    <section className="card">
      <h3>Gespeicherte Abendpläne</h3>
      {saved.length === 0 ? <p>Noch kein Abendplan gespeichert.</p> : <ul>{saved.map((item) => <li key={item.id}>
        <button disabled={busy} onClick={() => void load(item.id)}>{item.input.date_local} · {item.input.start_time}–{item.input.sleep_time} · Version {item.version}</button>
      </li>)}</ul>}
      {active && <div className="item-actions">
        {!confirmDelete ? <button disabled={busy} onClick={() => setConfirmDelete(true)}>Diesen Plan löschen</button> : <>
          <button disabled={busy} onClick={() => void remove()}>Löschen bestätigen</button>
          <button disabled={busy} onClick={() => setConfirmDelete(false)}>Abbrechen</button>
        </>}
      </div>}
    </section>
  </>;
}
