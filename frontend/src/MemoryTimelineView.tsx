import { useEffect, useRef, useState } from 'react';
import { api, downloadMemoryTimeline } from './api';
import PhotoLibraryView, { type TimelineEventOption } from './PhotoLibraryView';

type Event = { id: string; date_local: string; title: string; description: string;
  location: string; source_kind: 'manual'; version: number; created_at: string;
  updated_at: string };
type Form = Pick<Event, 'date_local' | 'title' | 'description' | 'location'>;
const emptyForm = (): Form => ({ date_local: '', title: '', description: '', location: '' });

export default function MemoryTimelineView() {
  const [items, setItems] = useState<Event[]>([]);
  const [eventOptions, setEventOptions] = useState<TimelineEventOption[]>([]);
  const [query, setQuery] = useState('');
  const [fromDate, setFromDate] = useState('');
  const [toDate, setToDate] = useState('');
  const [form, setForm] = useState<Form>(emptyForm);
  const [editing, setEditing] = useState<Event | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const inFlight = useRef(false);
  const searchRequest = useRef(0);

  const refresh = async () => {
    const request = ++searchRequest.current;
    const params = new URLSearchParams();
    if (query.trim()) params.set('q', query.trim());
    if (fromDate) params.set('from_date', fromDate);
    if (toDate) params.set('to_date', toDate);
    const [results, options] = await Promise.all([
      api<Event[]>(`/memory-events?${params}`), api<TimelineEventOption[]>('/memory-events'),
    ]);
    if (request === searchRequest.current) { setItems(results); setEventOptions(options); }
  };
  useEffect(() => { void refresh().catch((error: Error) => setNotice(error.message)); }, []);

  const save = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setNotice('');
    try {
      await api<Event>(editing ? `/memory-events/${encodeURIComponent(editing.id)}` : '/memory-events', {
        method: editing ? 'PUT' : 'POST',
        body: JSON.stringify(editing ? { ...form, expected_version: editing.version } : form),
      });
      setForm(emptyForm()); setEditing(null);
      await refresh();
      setNotice('Ereignis gespeichert.');
    } catch (error) {
      setNotice((error as Error).message);
      await refresh().catch(() => undefined);
    } finally { inFlight.current = false; setBusy(false); }
  };
  const remove = async (item: Event) => {
    if (inFlight.current || !window.confirm(`Ereignis „${item.title}“ löschen?`)) return;
    inFlight.current = true; setBusy(true); setNotice('');
    try {
      await api(`/memory-events/${encodeURIComponent(item.id)}?expected_version=${item.version}`,
        { method: 'DELETE' });
      if (editing?.id === item.id) { setEditing(null); setForm(emptyForm()); }
      await refresh();
      setNotice('Ereignis gelöscht.');
    } catch (error) {
      setNotice((error as Error).message);
      await refresh().catch(() => undefined);
    } finally { inFlight.current = false; setBusy(false); }
  };
  const exportMarkdown = async () => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setNotice('');
    try {
      const blob = await downloadMemoryTimeline(query, fromDate, toDate);
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = 'lebenschronik.md';
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setNotice('Gefilterte Zeitleiste als Markdown heruntergeladen.');
    } catch (error) { setNotice((error as Error).message); }
    finally { inFlight.current = false; setBusy(false); }
  };

  return <div className="section-stack"><h2>Lebenschronik</h2>
    <p className="intro">Halte ausgewählte Erlebnisse mit einem Datum selbst fest. Ort und Beschreibung sind optional. Jarvis leitet hier keine Ereignisse aus Fotos oder Gesprächen ab und ruft kein Modell auf.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Zeitleiste durchsuchen</h3><div className="memory-form">
      <input aria-label="Erlebnisse durchsuchen" maxLength={200} value={query}
        onChange={(event) => setQuery(event.target.value)} placeholder="Titel, Ort oder Beschreibung" />
      <label>Von <input aria-label="Ereignisse ab" type="date" value={fromDate}
        onChange={(event) => setFromDate(event.target.value)} /></label>
      <label>Bis <input aria-label="Ereignisse bis" type="date" value={toDate}
        onChange={(event) => setToDate(event.target.value)} /></label>
      <div className="item-actions"><button disabled={busy} onClick={() => void refresh().catch((error: Error) => setNotice(error.message))}>Suchen</button>
        <button disabled={busy} onClick={() => void exportMarkdown()}>Gefilterte Zeitleiste als Markdown herunterladen</button></div>
      <small>Die Ansicht zeigt höchstens 200 Treffer. Der Export enthält bis zu 1000 passende Ereignisse.</small>
    </div></section>
    <section className="card"><h3>{editing ? 'Ereignis korrigieren' : 'Ereignis eintragen'}</h3>
      <div className="memory-form"><label>Datum <input aria-label="Ereignisdatum" type="date" value={form.date_local}
        onChange={(event) => setForm({ ...form, date_local: event.target.value })} /></label>
      <input aria-label="Ereignistitel" maxLength={160} value={form.title}
        onChange={(event) => setForm({ ...form, title: event.target.value })} placeholder="z. B. Wochenende in Lyon" />
      <input aria-label="Ereignisort" maxLength={160} value={form.location}
        onChange={(event) => setForm({ ...form, location: event.target.value })} placeholder="Ort (optional)" />
      <textarea aria-label="Ereignisbeschreibung" maxLength={5000} value={form.description}
        onChange={(event) => setForm({ ...form, description: event.target.value })} placeholder="Was möchtest du dazu festhalten?" />
      <div className="item-actions"><button className="primary" disabled={busy || !form.date_local || !form.title.trim()}
        onClick={() => void save()}>{editing ? 'Änderung speichern' : 'Ereignis speichern'}</button>
        {editing && <button disabled={busy} onClick={() => { setEditing(null); setForm(emptyForm()); }}>Abbrechen</button>}</div></div>
    </section>
    <section className="card"><h3>Ereignisse</h3><div className="item-list">{items.map((item) => <div className="item-row" key={item.id}>
      <div><strong>{item.date_local} · {item.title}</strong><small>{item.location || 'Ohne Ort'} · manuell bestätigt · Version {item.version} · geändert {new Date(item.updated_at).toLocaleString('de-DE')}</small>
        {item.description && <p>{item.description}</p>}</div>
      <div className="item-actions"><button disabled={busy} onClick={() => { setEditing(item); setForm({ date_local: item.date_local, title: item.title, description: item.description, location: item.location }); }}>Bearbeiten</button>
        <button disabled={busy} onClick={() => void remove(item)}>Löschen</button></div></div>)}</div>
      {!items.length && <p>Keine passenden Ereignisse.</p>}
    </section>
    <PhotoLibraryView events={eventOptions} />
  </div>;
}
