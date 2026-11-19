import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Note = { id: string; contact_id: string; content: string; source_kind: 'manual';
  version: number; created_at: string; updated_at: string };
type Plan = { id: string; contact_id: string; title: string; details: string;
  status: 'open' | 'completed'; version: number; created_at: string; updated_at: string;
  reminder: ReminderLink | null };
type ReminderLink = { task_id: string | null; task_title_snapshot: string;
  due_local_snapshot: string; timezone_snapshot: string; current_title: string | null;
  current_status: string | null; current_due_local: string | null;
  current_timezone: string | null;
  status: 'active' | 'fired' | 'cancelled' | 'task_changed' | 'task_completed' | 'task_deleted' | 'due_removed' };
type TaskOption = { id: string; title: string; status: 'open' | 'done';
  due_utc: string | null; due_local: string | null; timezone: string | null };
type LinkedEvent = { id: string; date_local: string; title: string; location: string;
  version: number; source_kind: 'manual'; linked_at: string };
type Contact = { id: string; name: string; context: string; relationship: string;
  version: number; created_at: string; updated_at: string; note_count?: number;
  plan_count?: number; event_count?: number; notes?: Note[]; plans?: Plan[];
  events?: LinkedEvent[] };
type ContactForm = { name: string; context: string; relationship: string };
const emptyForm = (): ContactForm => ({ name: '', context: '', relationship: '' });

export default function ContactView({ visible, onPrepareTask, onOpenEvent }: {
  visible: boolean; onPrepareTask: (title: string) => void;
  onOpenEvent: (eventId: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [tasks, setTasks] = useState<TaskOption[]>([]);
  const [chosenTasks, setChosenTasks] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<Contact | null>(null);
  const [form, setForm] = useState<ContactForm>(emptyForm);
  const [editing, setEditing] = useState(false);
  const [noteDraft, setNoteDraft] = useState('');
  const [editingNote, setEditingNote] = useState<string | null>(null);
  const [planTitle, setPlanTitle] = useState('');
  const [planDetails, setPlanDetails] = useState('');
  const [editingPlan, setEditingPlan] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const refreshRequest = useRef(0);
  const openRequest = useRef(0);

  const reload = async (term = query, id = selected?.id) => {
    const request = ++refreshRequest.current;
    const selection = openRequest.current;
    const [list, availableTasks] = await Promise.all([
      api<Contact[]>(`/contacts?q=${encodeURIComponent(term.trim())}`),
      api<TaskOption[]>('/tasks'),
    ]);
    if (request !== refreshRequest.current) return;
    setContacts(list);
    setTasks(availableTasks);
    if (id) {
      const person = await api<Contact>(`/contacts/${encodeURIComponent(id)}`).catch(() => null);
      if (request === refreshRequest.current && selection === openRequest.current)
        setSelected(person);
    }
  };
  useEffect(() => { if (visible) void reload().catch((error: Error) => setNotice(error.message)); }, [visible]);

  const change = async (operation: () => Promise<unknown>, success: string, id = selected?.id) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setNotice('');
    try {
      await operation();
      await reload(query, id);
      setNotice(success);
      return true;
    } catch (error) {
      setNotice((error as Error).message);
      await reload(query, id).catch(() => undefined);
      return false;
    } finally { inFlight.current = false; setBusy(false); }
  };
  const open = async (id: string) => {
    ++refreshRequest.current;
    const request = ++openRequest.current;
    setNotice(''); setEditing(false); setEditingNote(null); setNoteDraft('');
    setEditingPlan(null); setPlanTitle(''); setPlanDetails('');
    try {
      const person = await api<Contact>(`/contacts/${encodeURIComponent(id)}`);
      if (request !== openRequest.current) return;
      setSelected(person);
      setForm(emptyForm());
    } catch (error) { setNotice((error as Error).message); }
  };
  const saveContact = async () => {
    if (!editing || !selected) {
      const created = await api<Contact>('/contacts', { method: 'POST', body: JSON.stringify(form) });
      setForm(emptyForm());
      return created.id;
    }
    await api<Contact>(`/contacts/${encodeURIComponent(selected.id)}`, { method: 'PUT',
      body: JSON.stringify({ ...form, expected_version: selected.version }) });
    return selected.id;
  };
  const eligibleTasks = tasks.filter((task) => task.status === 'open' && task.due_utc
    && new Date(task.due_utc).getTime() > Date.now());

  return <div className="section-stack"><h2>Kontakte</h2>
    <p className="intro">Personen und bestätigte Notizen bleiben lokal. Nutze einen eindeutigen Kontext, wenn zwei Menschen denselben Namen haben. Notizen werden nur gespeichert, wenn du sie selbst einträgst.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Kontakt suchen</h3>
      <div className="memory-form"><input aria-label="Kontakte durchsuchen" maxLength={200} value={query}
        onChange={(event) => setQuery(event.target.value)} placeholder="Name, Kontext, Beziehung oder Notiz" />
      <button disabled={busy} onClick={() => void reload().catch((error: Error) => setNotice(error.message))}>Suchen</button></div>
      <div className="item-list">{contacts.map((person) => <div className="item-row" key={person.id}>
        <div><strong>{person.name}</strong><small>{person.context}{person.relationship ? ` · ${person.relationship}` : ''} · {person.note_count} Notizen · {person.plan_count} Vorhaben · {person.event_count} Lebensereignisse</small></div>
        <button onClick={() => void open(person.id)}>Öffnen</button></div>)}</div>
      {!contacts.length && <p>Keine passenden Kontakte.</p>}
    </section>
    <section className="card"><h3>{editing && selected ? 'Kontakt bearbeiten' : 'Kontakt anlegen'}</h3>
      <div className="memory-form">
        <input aria-label="Kontaktname" maxLength={120} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Name" />
        <input aria-label="Unterscheidender Kontext" maxLength={120} value={form.context} onChange={(event) => setForm({ ...form, context: event.target.value })} placeholder="z. B. Freund aus München" />
        <input aria-label="Beziehung" maxLength={120} value={form.relationship} onChange={(event) => setForm({ ...form, relationship: event.target.value })} placeholder="z. B. Freund" />
        <div className="item-actions"><button className="primary" disabled={busy || !form.name.trim() || !form.context.trim()}
          onClick={() => void (async () => {
            let id: string | undefined;
            const saved = await change(async () => { id = await saveContact(); }, editing ? 'Kontakt aktualisiert.' : 'Kontakt angelegt.', editing ? selected?.id : undefined);
            if (saved) { setEditing(false); if (id) await open(id); }
          })()}>{editing ? 'Änderungen speichern' : 'Kontakt anlegen'}</button>
        {editing && <button disabled={busy} onClick={() => { setEditing(false); setForm(emptyForm()); }}>Abbrechen</button>}</div>
      </div>
    </section>
    {selected && <section className="card"><h3>{selected.name} · {selected.context}</h3>
      <p>{selected.relationship || 'Keine Beziehung eingetragen'}</p>
      <div className="item-actions"><button disabled={busy} onClick={() => { setEditing(true); setForm({ name: selected.name, context: selected.context, relationship: selected.relationship }); }}>Kontakt bearbeiten</button>
        <button disabled={busy} onClick={() => void (async () => {
          if (!window.confirm(`Kontakt „${selected.name}“ mit allen Notizen löschen?`)) return;
          const done = await change(() => api(`/contacts/${encodeURIComponent(selected.id)}?expected_version=${selected.version}`, { method: 'DELETE' }), 'Kontakt gelöscht.', undefined);
          if (done) { setSelected(null); setForm(emptyForm()); await reload(query, undefined); }
        })()}>Kontakt löschen</button></div>
      <h4>Gemeinsame Lebensereignisse ({selected.event_count || 0})</h4>
      <p>Diese Zuordnungen wurden von dir bewusst bestätigt. Das Öffnen ändert weder Ereignis noch Kontakt.</p>
      <div className="item-list">{selected.events?.map((event) => <div className="item-row" key={event.id}>
        <div><strong>{event.date_local} · {event.title}</strong>
          <small>{event.location || 'Ohne Ort'} · manuell bestätigt · Version {event.version}</small></div>
        <button onClick={() => onOpenEvent(event.id)}>Ereignis öffnen</button></div>)}</div>
      {!selected.events?.length && <p>Keine Lebensereignisse zugeordnet.</p>}
      {(selected.event_count || 0) > (selected.events?.length || 0) &&
        <small>Die 200 neuesten verknüpften Ereignisse werden angezeigt.</small>}
      <h4>Gemeinsame Vorhaben</h4>
      <p>Diese Pläne werden von dir eingetragen. „Erledigt“ ist eine bewusste Bestätigung; es wird keine Nachricht gesendet.</p>
      <div className="memory-form"><input aria-label={editingPlan ? 'Vorhaben-Titel bearbeiten' : 'Neues Vorhaben'} maxLength={160}
        value={planTitle} onChange={(event) => setPlanTitle(event.target.value)} placeholder="z. B. Museum besuchen" />
        <textarea aria-label="Vorhaben-Details" maxLength={4000} value={planDetails}
          onChange={(event) => setPlanDetails(event.target.value)} placeholder="Absprachen und Kontext" />
        <div className="item-actions"><button className="primary" disabled={busy || !planTitle.trim()} onClick={() => void (async () => {
          const plan = selected.plans?.find((item) => item.id === editingPlan);
          const done = await change(() => api(plan ? `/contacts/${encodeURIComponent(selected.id)}/plans/${encodeURIComponent(plan.id)}` : `/contacts/${encodeURIComponent(selected.id)}/plans`,
            { method: plan ? 'PUT' : 'POST', body: JSON.stringify(plan ? { title: planTitle, details: planDetails, status: plan.status, expected_version: plan.version } : { title: planTitle, details: planDetails }) }),
          plan ? 'Vorhaben aktualisiert.' : 'Vorhaben gespeichert.');
          if (done) { setEditingPlan(null); setPlanTitle(''); setPlanDetails(''); }
        })()}>{editingPlan ? 'Vorhaben speichern' : 'Vorhaben hinzufügen'}</button>
        {editingPlan && <button disabled={busy} onClick={() => { setEditingPlan(null); setPlanTitle(''); setPlanDetails(''); }}>Abbrechen</button>}</div></div>
      <div className="item-list">{selected.plans?.map((plan) => <div className="item-row" key={plan.id}><div>
        <strong>{plan.title}</strong><small>{plan.status === 'open' ? 'Offen' : 'Erledigt'} · Manuell bestätigt · {new Date(plan.updated_at).toLocaleString('de-DE')}</small>
        {plan.details && <p>{plan.details}</p>}
      </div><div className="item-actions"><button disabled={busy} onClick={() => { setEditingPlan(plan.id); setPlanTitle(plan.title); setPlanDetails(plan.details); }}>Bearbeiten</button>
        <button disabled={busy} onClick={() => void change(() => api(`/contacts/${encodeURIComponent(selected.id)}/plans/${encodeURIComponent(plan.id)}`,
          { method: 'PUT', body: JSON.stringify({ title: plan.title, details: plan.details, status: plan.status === 'open' ? 'completed' : 'open', expected_version: plan.version }) }),
        plan.status === 'open' ? plan.reminder ? 'Vorhaben erledigt. Die verknüpfte Aufgabe bleibt separat offen.' : 'Vorhaben als erledigt markiert.' : 'Vorhaben wieder geöffnet.')}>{plan.status === 'open' ? 'Erledigt' : 'Wieder öffnen'}</button>
        <button disabled={busy} onClick={() => void (async () => {
          if (!window.confirm(`Vorhaben „${plan.title}“ löschen?`)) return;
          const done = await change(() => api(`/contacts/${encodeURIComponent(selected.id)}/plans/${encodeURIComponent(plan.id)}?expected_version=${plan.version}`, { method: 'DELETE' }), 'Vorhaben gelöscht.');
          if (done && editingPlan === plan.id) { setEditingPlan(null); setPlanTitle(''); setPlanDetails(''); }
        })()}>Löschen</button></div>
        {plan.reminder && <p role="status">Erinnerungsaufgabe: {plan.reminder.current_title || plan.reminder.task_title_snapshot} · {plan.reminder.current_due_local || plan.reminder.due_local_snapshot} {plan.reminder.current_timezone || plan.reminder.timezone_snapshot} · {{ active: 'ausstehend', fired: 'ausgelöst', cancelled: 'abgebrochen', task_changed: 'Aufgabe geändert – bitte prüfen', task_completed: 'Aufgabe erledigt', task_deleted: 'Aufgabe gelöscht – Verknüpfung prüfen', due_removed: 'Fälligkeit entfernt – keine Erinnerung mehr' }[plan.reminder.status]}</p>}
        {plan.reminder && ['task_changed', 'due_removed', 'task_deleted'].includes(plan.reminder.status) && <small>Beim Verknüpfen: {plan.reminder.task_title_snapshot} · {plan.reminder.due_local_snapshot} {plan.reminder.timezone_snapshot}</small>}
        {plan.status === 'open' && <div className="memory-form"><button disabled={busy} onClick={() => onPrepareTask(`Mit ${selected.name} (${selected.context}): ${plan.title}`)}>Erinnerungsaufgabe vorbereiten</button>
          <select aria-label={`Erinnerungsaufgabe für ${plan.title}`} value={chosenTasks[plan.id] || ''}
            onChange={(event) => setChosenTasks((current) => ({ ...current, [plan.id]: event.target.value }))}>
            <option value="">Aufgabe mit zukünftiger Erinnerung wählen</option>
            {eligibleTasks.map((task) => <option key={task.id} value={task.id}>{task.title} · {task.due_local} {task.timezone}</option>)}
          </select>
          <button disabled={busy || !chosenTasks[plan.id]} onClick={() => void change(() => api(`/contacts/${encodeURIComponent(selected.id)}/plans/${encodeURIComponent(plan.id)}/reminder`,
            { method: 'PUT', body: JSON.stringify({ task_id: chosenTasks[plan.id], expected_plan_version: plan.version }) }), 'Erinnerungsaufgabe verbunden. Die Aufgabe bleibt unter „Aufgaben“ bearbeitbar.')}>Aufgabe verbinden</button>
        </div>}
        {plan.reminder && <button disabled={busy} onClick={() => void change(() => api(`/contacts/${encodeURIComponent(selected.id)}/plans/${encodeURIComponent(plan.id)}/reminder?expected_plan_version=${plan.version}`, { method: 'DELETE' }), 'Erinnerungsverknüpfung entfernt. Die Aufgabe bleibt bestehen.')}>Verknüpfung lösen</button>}
      </div>)}</div>
      <h4>Bestätigte Notizen</h4><p>Quelle: manuell von dir eingetragen. Änderungen bleiben mit Zeitstempel nachvollziehbar.</p>
      <div className="memory-form"><textarea aria-label={editingNote ? 'Notiz bearbeiten' : 'Neue Kontaktnotiz'} maxLength={4000}
        value={noteDraft} onChange={(event) => setNoteDraft(event.target.value)} placeholder="Was soll Jarvis über diese Person wissen?" />
      <div className="item-actions"><button className="primary" disabled={busy || !noteDraft.trim()} onClick={() => void (async () => {
        const note = selected.notes?.find((item) => item.id === editingNote);
        const done = await change(() => api(note ? `/contacts/${encodeURIComponent(selected.id)}/notes/${encodeURIComponent(note.id)}` : `/contacts/${encodeURIComponent(selected.id)}/notes`,
          { method: note ? 'PUT' : 'POST', body: JSON.stringify(note ? { content: noteDraft, expected_version: note.version } : { content: noteDraft }) }),
        note ? 'Notiz aktualisiert.' : 'Notiz gespeichert.');
        if (done) { setNoteDraft(''); setEditingNote(null); }
      })()}>{editingNote ? 'Notiz speichern' : 'Notiz hinzufügen'}</button>
      {editingNote && <button disabled={busy} onClick={() => { setEditingNote(null); setNoteDraft(''); }}>Abbrechen</button>}</div></div>
      <div className="item-list">{selected.notes?.map((note) => <div className="item-row" key={note.id}><div>
        <p>{note.content}</p><small>Manuell bestätigt · {new Date(note.updated_at).toLocaleString('de-DE')}{note.version > 1 ? ` · Version ${note.version}` : ''}</small>
      </div><div className="item-actions"><button disabled={busy} onClick={() => { setEditingNote(note.id); setNoteDraft(note.content); }}>Bearbeiten</button>
        <button disabled={busy} onClick={() => void (async () => {
          if (!window.confirm('Diese Notiz löschen?')) return;
          const done = await change(() => api(`/contacts/${encodeURIComponent(selected.id)}/notes/${encodeURIComponent(note.id)}?expected_version=${note.version}`, { method: 'DELETE' }), 'Notiz gelöscht.');
          if (done && editingNote === note.id) { setEditingNote(null); setNoteDraft(''); }
        })()}>Löschen</button></div></div>)}</div>
    </section>}
  </div>;
}
