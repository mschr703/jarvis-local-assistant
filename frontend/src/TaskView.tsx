import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Task = { id: string; title: string; notes: string; source_url: string | null; status: 'open' | 'done'; priority: number; due_utc: string | null; due_local: string | null; timezone: string | null; due_offset: string | null; due_fold: number | null; reminder_job_id: string | null; reminder_status: 'pending' | 'fired' | 'cancelled' | null };
type Notification = { id: string; task_id: string; title: string; due_utc: string; created_at: string; last_delivered_at: string | null; read_at: string | null; acknowledged_at: string | null; snoozed_until: string | null; followup_due_utc: string | null; focus_deferred_at: string | null; focus_suppressed: boolean; followup_count: number; priority: number; task_status: 'open' | 'done' };
type Settings = { timezone: string };
type DuePreview = { due_utc: string; due_local: string; timezone: string; offset: string; fold: number };
type Focus = { manual_enabled: boolean; schedule_enabled: boolean; start_time: string; end_time: string; timezone: string; version: number; active: boolean };
type FocusDraft = Pick<Focus, 'schedule_enabled' | 'start_time' | 'end_time' | 'timezone'>;

function dueLabel(task: Task): string {
  if (!task.due_local) return '';
  const offset = task.due_offset ? ` · UTC${task.due_offset}` : '';
  const occurrence = task.due_fold == null ? '' : ` · ${task.due_fold === 0 ? 'erstes' : 'zweites'} Vorkommen`;
  return ` · ${task.due_local.replace('T', ' ')} ${task.timezone}${offset}${occurrence}`;
}

const reminderStatusLabels: Record<string, string> = {
  pending: 'ausstehend', fired: 'ausgelöst', cancelled: 'abgebrochen',
};

export default function TaskView({ visible, onNotificationsChanged, suggestedTitle, onSuggestionTaken }: {
  visible: boolean; onNotificationsChanged: () => void;
  suggestedTitle: string; onSuggestionTaken: () => void;
}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [title, setTitle] = useState('');
  const [pendingSuggestion, setPendingSuggestion] = useState('');
  const [notes, setNotes] = useState('');
  const [priority, setPriority] = useState(3);
  const [snoozeMinutes, setSnoozeMinutes] = useState<Record<string, number>>({});
  const [focus, setFocus] = useState<Focus | null>(null);
  const [focusDraft, setFocusDraft] = useState<FocusDraft | null>(null);
  const focusTouched = useRef(false);
  const focusBusyRef = useRef(false);
  const [focusBusy, setFocusBusy] = useState(false);
  const [localDue, setLocalDue] = useState('');
  const [timezone, setTimezone] = useState('Europe/Paris');
  const [timezoneReady, setTimezoneReady] = useState(false);
  const timezoneTouched = useRef(false);
  const [fold, setFold] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [originalDue, setOriginalDue] = useState('');
  const [originalTimezone, setOriginalTimezone] = useState('');
  const [originalFold, setOriginalFold] = useState('');
  const [preview, setPreview] = useState<{ key: string; value: DuePreview } | null>(null);
  const [previewError, setPreviewError] = useState<{ key: string; message: string } | null>(null);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [acting, setActing] = useState(false);
  const actingRef = useRef(false);
  const [notice, setNotice] = useState('');
  const refreshRequest = useRef(0);
  const needsPreview = Boolean(localDue && (!editing || localDue !== originalDue || timezone !== originalTimezone || fold !== originalFold));
  const previewKey = JSON.stringify([localDue, timezone, fold]);
  const validPreview = !needsPreview || preview?.key === previewKey;

  const refresh = async () => {
    const request = ++refreshRequest.current;
    const [nextTasks, nextNotifications, nextFocus] = await Promise.all([
      api<Task[]>('/tasks'), api<Notification[]>('/notifications'), api<Focus>('/focus'),
    ]);
    if (request === refreshRequest.current) {
      setTasks(nextTasks); setNotifications(nextNotifications);
      setFocus(nextFocus);
      if (!focusTouched.current) setFocusDraft({ schedule_enabled: nextFocus.schedule_enabled,
        start_time: nextFocus.start_time, end_time: nextFocus.end_time, timezone: nextFocus.timezone });
    }
  };
  useEffect(() => {
    if (!visible || !suggestedTitle) return;
    if (title.trim() || notes.trim() || localDue || editing) {
      setPendingSuggestion(suggestedTitle);
    } else {
      setTitle(suggestedTitle);
      setNotice('Vorschlag ins Aufgabenformular übernommen. Erst „Aufgabe speichern“ legt sie an.');
    }
    onSuggestionTaken();
  }, [visible, suggestedTitle]);
  useEffect(() => {
    if (!visible) return;
    let active = true;
    void api<Settings>('/settings').then((value) => {
      if (!active) return;
      if (!timezoneTouched.current) setTimezone(value.timezone);
      setTimezoneReady(true);
    }).catch((error: Error) => { if (active) { setNotice(error.message); setTimezoneReady(true); } });
    void refresh().catch((error: Error) => setNotice(error.message));
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 15_000);
    return () => { active = false; ++refreshRequest.current; window.clearInterval(timer); };
  }, [visible]);
  useEffect(() => {
    if (!visible || !needsPreview || !timezoneReady) return;
    let active = true;
    const timer = window.setTimeout(() => {
      void api<DuePreview>('/tasks/preview', {
        method: 'POST', body: JSON.stringify({
          local_due: localDue, timezone, fold: fold === '' ? null : Number(fold),
        }),
      }).then((value) => {
        if (active) { setPreview({ key: previewKey, value }); setPreviewError(null); }
      }).catch((error: Error) => {
        if (active) { setPreview(null); setPreviewError({ key: previewKey, message: error.message }); }
      });
    }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [localDue, timezone, fold, previewKey, needsPreview, timezoneReady, visible]);

  const submit = async () => {
    if (savingRef.current || actingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    try {
      const body = JSON.stringify({ title, notes, priority, local_due: localDue || null, timezone, fold: fold === '' ? null : Number(fold) });
      await api<Task>(editing ? `/tasks/${editing}` : '/tasks', { method: editing ? 'PUT' : 'POST', body });
      setNotice(editing ? 'Aufgabe geändert.' : 'Aufgabe gespeichert. Erinnerung läuft nur, während Jarvis auf diesem Laptop gestartet ist.');
      setTitle(''); setNotes(''); setPriority(3); setLocalDue(''); setFold(''); setEditing(null);
      setOriginalDue(''); setOriginalTimezone(''); setOriginalFold(''); await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { savingRef.current = false; setSaving(false); }
  };
  const edit = (task: Task) => {
    if (savingRef.current || actingRef.current) return;
    setEditing(task.id); setTitle(task.title); setNotes(task.notes); setPriority(task.priority);
    timezoneTouched.current = true;
    const savedFold = task.due_fold == null ? '' : String(task.due_fold);
    setLocalDue(task.due_local || ''); setTimezone(task.timezone || timezone); setFold(savedFold);
    setOriginalDue(task.due_local || ''); setOriginalTimezone(task.timezone || timezone); setOriginalFold(savedFold);
  };
  const act = async (path: string, method: string, body?: unknown) => {
    if (savingRef.current || actingRef.current) return;
    actingRef.current = true;
    setActing(true);
    try {
      await api(path, { method, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
      if (method === 'DELETE' && path === `/tasks/${editing}`) {
        setEditing(null); setTitle(''); setNotes(''); setPriority(3); setLocalDue(''); setFold('');
        setOriginalDue(''); setOriginalTimezone(''); setOriginalFold('');
      }
      onNotificationsChanged();
      await refresh();
    }
    catch (error) { setNotice((error as Error).message); }
    finally { actingRef.current = false; setActing(false); }
  };

  const saveFocus = async (manual?: boolean) => {
    if (!focus || !focusDraft || focusBusyRef.current) return;
    focusBusyRef.current = true;
    setFocusBusy(true);
    try {
      const source = manual === undefined ? focusDraft : focus;
      const result = await api<Focus>('/focus', { method: 'PUT', body: JSON.stringify({
        manual_enabled: manual ?? focus.manual_enabled,
        schedule_enabled: source.schedule_enabled,
        start_time: source.start_time, end_time: source.end_time,
        timezone: source.timezone, expected_version: focus.version,
      }) });
      setFocus(result);
      if (manual === undefined) {
        focusTouched.current = false;
        setFocusDraft({ schedule_enabled: result.schedule_enabled, start_time: result.start_time,
          end_time: result.end_time, timezone: result.timezone });
      }
      setNotice(result.active ? 'Fokusmodus aktiv. Normale Erinnerungen werden gesammelt.' : 'Fokusmodus aus. Gesammelte Erinnerungen sind jetzt sichtbar.');
      onNotificationsChanged();
      await refresh();
    } catch (error) { setNotice((error as Error).message); await refresh().catch(() => undefined); }
    finally { focusBusyRef.current = false; setFocusBusy(false); }
  };

  const notificationRow = (item: Notification, fresh: boolean) => <div key={item.id} className="item-row">
    <div><strong>{item.title}{item.priority >= 4 ? ' · Wichtig' : ''}</strong>
      <small>Geplant: {new Date(item.due_utc).toLocaleString('de-DE')} · Zuletzt angezeigt: {new Date(item.last_delivered_at || item.created_at).toLocaleString('de-DE')} (Laptop-Zeitzone)</small>
      {item.followup_count > 0 && <small>Nachfrage {item.followup_count} von höchstens 2</small>}
      {item.snoozed_until && <small>Verschoben bis {new Date(item.snoozed_until).toLocaleString('de-DE')}</small>}
      {item.acknowledged_at && <small>Bestätigt am {new Date(item.acknowledged_at).toLocaleString('de-DE')}</small>}
      {!item.acknowledged_at && !item.snoozed_until && item.followup_due_utc && <small>Nächste Nachfrage frühestens {new Date(item.followup_due_utc).toLocaleString('de-DE')}</small>}
    </div>
    {item.task_status === 'open' && !item.acknowledged_at && <div className="item-actions">
      {fresh && <button disabled={saving || acting} onClick={() => void act(`/notifications/${item.id}/read`, 'POST')}>Gesehen</button>}
      <button disabled={saving || acting} onClick={() => void act(`/notifications/${item.id}/ack`, 'POST')}>Bestätigen</button>
      {!item.snoozed_until && <><select aria-label={`Erinnerung ${item.title} verschieben um`} value={snoozeMinutes[item.id] || 15} disabled={saving || acting} onChange={(event) => setSnoozeMinutes((current) => ({ ...current, [item.id]: Number(event.target.value) }))}>
        <option value="15">15 Minuten</option><option value="60">1 Stunde</option><option value="240">4 Stunden</option><option value="1440">24 Stunden</option>
      </select><button disabled={saving || acting} onClick={() => void act(`/notifications/${item.id}/snooze`, 'POST', { minutes: snoozeMinutes[item.id] || 15 })}>Verschieben</button></>}
    </div>}
  </div>;

  const deferredNotifications = notifications.filter((item) => !item.read_at && !item.snoozed_until && !item.acknowledged_at && item.task_status === 'open' && item.focus_deferred_at && item.priority < 4);
  const newNotifications = notifications.filter((item) => !item.read_at && !item.snoozed_until && !item.acknowledged_at && item.task_status === 'open' && !item.focus_suppressed && !deferredNotifications.includes(item));
  const snoozedNotifications = notifications.filter((item) => item.snoozed_until && !item.acknowledged_at && item.task_status === 'open');
  const history = notifications.filter((item) => !newNotifications.includes(item) && !deferredNotifications.includes(item) && !snoozedNotifications.includes(item));

  return <div className="section-stack"><h2>Aufgaben und Erinnerungen</h2>
    <p className="intro">Fälligkeiten werden lokal gespeichert. Jarvis zeigt Hinweise in der geöffneten App, nicht als Windows-Benachrichtigung. War der Laptop aus oder im Schlafmodus, erscheint eine fällige Erinnerung nach dem Aufwachen oder Neustart. Wichtige offene Aufgaben können höchstens zwei Nachfragen erhalten; Bestätigen oder Erledigen stoppt sie.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    {pendingSuggestion && <section className="card"><h3>Vorgeschlagene Aufgabe</h3><p>{pendingSuggestion}</p><p>Im Formular steht bereits eine Eingabe. Übernimm den Vorschlag nur, wenn sie ersetzt werden soll.</p><div className="item-actions"><button onClick={() => setPendingSuggestion('')}>Bestehende Eingabe behalten</button><button onClick={() => { setEditing(null); setTitle(pendingSuggestion); setNotes(''); setLocalDue(''); setFold(''); setPriority(3); setPendingSuggestion(''); setNotice('Vorschlag übernommen. Erst „Aufgabe speichern“ legt sie an.'); }}>Vorschlag ins Formular übernehmen</button></div></section>}
    <section className="card"><h3>Fokusmodus</h3>
      <p>{focus?.active ? 'Aktiv: wichtige Erinnerungen erscheinen sofort; normale warten in der Sammlung.' : 'Aus: offene Erinnerungen erscheinen regulär.'}</p>
      <button disabled={!focus || focusBusy} onClick={() => void saveFocus(!focus?.manual_enabled)}>{focus?.manual_enabled ? 'Manuellen Fokus ausschalten' : 'Manuellen Fokus einschalten'}</button>
      {focusDraft && <div className="memory-form">
        <label><input type="checkbox" checked={focusDraft.schedule_enabled} disabled={focusBusy} onChange={(event) => { focusTouched.current = true; setFocusDraft({ ...focusDraft, schedule_enabled: event.target.checked }); }} /> Tägliches Fokusfenster aktivieren</label>
        <label>Beginn <input aria-label="Fokusbeginn" type="time" value={focusDraft.start_time} disabled={focusBusy} onChange={(event) => { focusTouched.current = true; setFocusDraft({ ...focusDraft, start_time: event.target.value }); }} /></label>
        <label>Ende <input aria-label="Fokusende" type="time" value={focusDraft.end_time} disabled={focusBusy} onChange={(event) => { focusTouched.current = true; setFocusDraft({ ...focusDraft, end_time: event.target.value }); }} /></label>
        <label>Zeitzone <input aria-label="Fokus-Zeitzone" value={focusDraft.timezone} disabled={focusBusy} onChange={(event) => { focusTouched.current = true; setFocusDraft({ ...focusDraft, timezone: event.target.value }); }} /></label>
        <button disabled={focusBusy} onClick={() => void saveFocus()}>Fokuszeiten speichern</button>
      </div>}
      <p className="hint">Ein Fenster über Mitternacht endet am Folgetag. Die manuelle Aktivierung gilt zusätzlich zum Zeitfenster.</p>
    </section>
    {newNotifications.length > 0 && <section className="card"><h3>Neue Erinnerungen</h3><div className="item-list">{newNotifications.map((item) => notificationRow(item, true))}</div></section>}
    {deferredNotifications.length > 0 && <section className="card"><h3>{focus?.active ? 'Im Fokus gesammelt' : 'Nach dem Fokus gesammelt'}</h3><p>Diese Meldungen bleiben zugänglich. „Gesehen“ entfernt sie aus der Sammlung; Bestätigen stoppt weitere Nachfragen.</p><div className="item-list">{deferredNotifications.map((item) => notificationRow(item, true))}</div></section>}
    {snoozedNotifications.length > 0 && <section className="card"><h3>Zurückgestellte Erinnerungen</h3><div className="item-list">{snoozedNotifications.map((item) => notificationRow(item, false))}</div></section>}
    {history.length > 0 && <section className="card"><h3>Erinnerungsverlauf</h3><div className="item-list">{history.map((item) => notificationRow(item, false))}</div></section>}
    <section className="card"><h3>{editing ? 'Aufgabe ändern' : 'Neue Aufgabe'}</h3><div className="memory-form">
      <input aria-label="Aufgabe" value={title} disabled={saving || acting} onChange={(event) => setTitle(event.target.value)} placeholder="Was ist zu tun?" />
      <textarea aria-label="Notizen" value={notes} disabled={saving || acting} onChange={(event) => setNotes(event.target.value)} placeholder="Kontext oder Quelle (optional)" />
      <label>Priorität <select aria-label="Aufgabenpriorität" value={priority} disabled={saving || acting} onChange={(event) => setPriority(Number(event.target.value))}><option value="1">1 · Niedrig</option><option value="2">2</option><option value="3">3 · Normal</option><option value="4">4 · Wichtig</option><option value="5">5 · Sehr wichtig</option></select></label>
      <label>Erinnerung am <input aria-label="Lokale Fälligkeit" type="datetime-local" value={localDue} disabled={saving || acting} onChange={(event) => setLocalDue(event.target.value)} /></label>
      <label>Zeitzone <input aria-label="IANA-Zeitzone" value={timezone} disabled={saving || acting} onChange={(event) => { timezoneTouched.current = true; setTimezone(event.target.value); }} /></label>
      <label>Doppelte Stunde bei Zeitumstellung <select aria-label="Zeitumstellung wählen" value={fold} disabled={saving || acting} onChange={(event) => setFold(event.target.value)}><option value="">Bitte bei Bedarf wählen</option><option value="0">Erstes Vorkommen</option><option value="1">Zweites Vorkommen</option></select></label>
      {needsPreview && <p className={previewError?.key === previewKey ? 'notice error' : 'preview'} role="status">{preview?.key === previewKey
        ? `Fällig: ${preview.value.due_local.replace('T', ' ')} ${preview.value.timezone} (UTC${preview.value.offset.slice(0, 3)}:${preview.value.offset.slice(3)}${preview.value.fold ? ', zweites Vorkommen' : ''})`
        : previewError?.key === previewKey ? previewError.message : 'Prüfe Fälligkeit…'}</p>}
      <button className="primary" disabled={!title.trim() || !timezoneReady || !validPreview || saving || acting} onClick={() => void submit()}>{saving ? 'Speichere…' : editing ? 'Änderung speichern' : 'Aufgabe speichern'}</button>
      {editing && <button disabled={saving || acting} onClick={() => { setEditing(null); setTitle(''); setNotes(''); setPriority(3); setLocalDue(''); setFold(''); setOriginalDue(''); setOriginalTimezone(''); setOriginalFold(''); }}>Abbrechen</button>}
    </div></section>
    <section className="card"><h3>Alle Aufgaben</h3><div className="item-list">{tasks.map((task) => <div key={task.id} className="item-row"><div><strong>{task.title}</strong><small>{task.status === 'done' ? 'Erledigt' : 'Offen'} · Priorität {task.priority}{dueLabel(task)}</small>{task.reminder_job_id && <small>Erinnerungsjob: {reminderStatusLabels[task.reminder_status || ''] || task.reminder_status} · ID {task.reminder_job_id}</small>}{task.source_url && <a href={task.source_url} target="_blank" rel="noopener noreferrer">Quellenlink öffnen</a>}{task.notes && <p>{task.notes}</p>}</div><div className="item-actions">{task.status === 'open' && <><button disabled={saving || acting} onClick={() => edit(task)}>Ändern</button><button disabled={saving || acting} onClick={() => void act(`/tasks/${task.id}/complete`, 'POST')}>Erledigt</button></>}<button disabled={saving || acting} onClick={() => void act(`/tasks/${task.id}`, 'DELETE')}>Löschen</button></div></div>)}</div></section>
  </div>;
}
