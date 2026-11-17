import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Kind = 'work' | 'school' | 'commute' | 'break' | 'sleep' | 'other';
type Rule = { id: string; title: string; kind: Kind; weekdays: number[]; start_time: string; end_time: string; timezone: string; buffer_before: number; buffer_after: number; version: number };
type Exception = { id: string; date_local: string; mode: 'skip' | 'block'; rule_id: string | null; title: string | null; kind: Kind | null; start_time: string | null; end_time: string | null; timezone: string | null; buffer_before: number; buffer_after: number; version: number };
type Slot = { start_local: string; end_local: string; minutes: number };
type Busy = Slot & { id: string; source: 'rule' | 'exception'; title: string; kind: Kind; occurrence_date: string };
type Availability = { date_local: string; timezone: string; total_minutes: number; blocked_minutes: number; free_minutes: number; busy: Busy[]; free: Slot[] };
type RuleForm = Omit<Rule, 'id' | 'version'>;
type ExceptionForm = { mode: 'skip' | 'block'; date_local: string; rule_id: string; title: string; kind: Kind; start_time: string; end_time: string; timezone: string; buffer_before: number; buffer_after: number };

const weekdays = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'];
const kinds: [Kind, string][] = [['work', 'Arbeit'], ['school', 'Schule'], ['commute', 'Pendeln'], ['break', 'Pause'], ['sleep', 'Schlaf'], ['other', 'Sonstiges']];
const kindLabel = (value: Kind) => kinds.find(([id]) => id === value)?.[1] || value;
const localLabel = (value: string) => `${value.slice(0, 16).replace('T', ' ')} · UTC${value.slice(-6)}`;
const todayIn = (timezone: string) => {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(new Date());
  const part = (type: string) => parts.find((item) => item.type === type)?.value || '';
  return `${part('year')}-${part('month')}-${part('day')}`;
};
const emptyRule = (timezone: string): RuleForm => ({ title: '', kind: 'work', weekdays: [0, 1, 2, 3, 4], start_time: '09:00', end_time: '17:00', timezone, buffer_before: 0, buffer_after: 0 });
const emptyException = (timezone: string, date_local: string): ExceptionForm => ({ mode: 'skip', date_local, rule_id: '', title: '', kind: 'other', start_time: '18:00', end_time: '19:00', timezone, buffer_before: 0, buffer_after: 0 });

export default function TimeModelView({ visible }: { visible: boolean }) {
  const [timezone, setTimezone] = useState('Europe/Paris');
  const [date, setDate] = useState(() => todayIn('Europe/Paris'));
  const [rules, setRules] = useState<Rule[]>([]);
  const [exceptions, setExceptions] = useState<Exception[]>([]);
  const [availability, setAvailability] = useState<Availability | null>(null);
  const [ruleForm, setRuleForm] = useState<RuleForm>(() => emptyRule('Europe/Paris'));
  const [exceptionForm, setExceptionForm] = useState<ExceptionForm>(() => emptyException('Europe/Paris', todayIn('Europe/Paris')));
  const [editingRule, setEditingRule] = useState<Rule | null>(null);
  const [editingException, setEditingException] = useState<Exception | null>(null);
  const [confirmDelete, setConfirmDelete] = useState('');
  const [busy, setBusy] = useState(false);
  const operation = useRef(false);
  const initialized = useRef(false);
  const dateTouched = useRef(false);
  const timezoneTouched = useRef(false);
  const ruleTimezoneTouched = useRef(false);
  const exceptionDateTouched = useRef(false);
  const exceptionTimezoneTouched = useRef(false);
  const refreshId = useRef(0);
  const previewId = useRef(0);
  const [notice, setNotice] = useState('');
  const [previewError, setPreviewError] = useState('');

  const refreshLists = async () => {
    const request = ++refreshId.current;
    const [nextRules, nextExceptions] = await Promise.all([
      api<Rule[]>('/time/rules'), api<Exception[]>('/time/exceptions'),
    ]);
    if (request === refreshId.current) { setRules(nextRules); setExceptions(nextExceptions); }
  };
  const refreshAvailability = async (targetDate: string, targetTimezone: string) => {
    const request = ++previewId.current;
    try {
      const result = await api<Availability>(`/time/availability?date_local=${encodeURIComponent(targetDate)}&timezone=${encodeURIComponent(targetTimezone)}`);
      if (request === previewId.current) { setAvailability(result); setPreviewError(''); }
    } catch (cause) {
      if (request === previewId.current) { setAvailability(null); setPreviewError((cause as Error).message); }
    }
  };
  useEffect(() => {
    if (!visible) return;
    let active = true;
    if (!initialized.current) {
      void api<{ timezone: string }>('/settings').then((settings) => {
        if (!active) return;
        initialized.current = true;
        if (!timezoneTouched.current) setTimezone(settings.timezone);
        if (!dateTouched.current) setDate(todayIn(settings.timezone));
        if (!ruleTimezoneTouched.current) setRuleForm((current) => ({ ...current, timezone: settings.timezone }));
        setExceptionForm((current) => ({ ...current,
          timezone: exceptionTimezoneTouched.current ? current.timezone : settings.timezone,
          date_local: exceptionDateTouched.current ? current.date_local : todayIn(settings.timezone) }));
      }).catch((cause: Error) => { if (active) setNotice(cause.message); });
    }
    void refreshLists().catch((cause: Error) => { if (active) setNotice(cause.message); });
    return () => { active = false; ++refreshId.current; ++previewId.current; };
  }, [visible]);
  useEffect(() => {
    if (visible && date && timezone) void refreshAvailability(date, timezone);
    else { ++previewId.current; setAvailability(null); }
  }, [visible, date, timezone]);

  const runAction = async (action: () => Promise<void>, success: string) => {
    if (operation.current) return;
    operation.current = true; setBusy(true); setNotice('');
    try {
      await action();
      await refreshLists();
      await refreshAvailability(date, timezone);
      setNotice(success);
    } catch (cause) { setNotice((cause as Error).message); }
    finally { operation.current = false; setBusy(false); }
  };
  const saveRule = () => void runAction(async () => {
    const path = editingRule ? `/time/rules/${editingRule.id}` : '/time/rules';
    await api<Rule>(path, { method: editingRule ? 'PUT' : 'POST', body: JSON.stringify({ ...ruleForm, ...(editingRule ? { version: editingRule.version } : {}) }) });
    setEditingRule(null); setRuleForm(emptyRule(timezone));
  }, editingRule ? 'Zeitregel geändert.' : 'Zeitregel gespeichert.');
  const saveException = () => void runAction(async () => {
    const payload = exceptionForm.mode === 'skip'
      ? { mode: 'skip', date_local: exceptionForm.date_local, rule_id: exceptionForm.rule_id }
      : { ...exceptionForm, rule_id: null };
    const path = editingException ? `/time/exceptions/${editingException.id}` : '/time/exceptions';
    await api<Exception>(path, { method: editingException ? 'PUT' : 'POST', body: JSON.stringify({ ...payload, ...(editingException ? { version: editingException.version } : {}) }) });
    setEditingException(null); setExceptionForm(emptyException(timezone, date));
  }, editingException ? 'Ausnahme geändert.' : 'Ausnahme gespeichert.');
  const deleteItem = (type: 'rules' | 'exceptions', id: string, version: number) => void runAction(async () => {
    await api(`/time/${type}/${id}?version=${version}`, { method: 'DELETE' });
    setConfirmDelete('');
    if (type === 'rules' && editingRule?.id === id) { setEditingRule(null); setRuleForm(emptyRule(timezone)); }
    if (type === 'exceptions' && editingException?.id === id) { setEditingException(null); setExceptionForm(emptyException(timezone, date)); }
  }, 'Eintrag gelöscht.');
  const chooseRule = (rule: Rule) => { if (operation.current) return; setEditingRule(rule); setRuleForm({ title: rule.title, kind: rule.kind, weekdays: rule.weekdays, start_time: rule.start_time, end_time: rule.end_time, timezone: rule.timezone, buffer_before: rule.buffer_before, buffer_after: rule.buffer_after }); setConfirmDelete(''); };
  const chooseException = (item: Exception) => { if (operation.current) return; setEditingException(item); setExceptionForm({ mode: item.mode, date_local: item.date_local, rule_id: item.rule_id || '', title: item.title || '', kind: item.kind || 'other', start_time: item.start_time || '18:00', end_time: item.end_time || '19:00', timezone: item.timezone || timezone, buffer_before: item.buffer_before, buffer_after: item.buffer_after }); setConfirmDelete(''); };
  const weekdayOfException = exceptionForm.date_local ? (new Date(`${exceptionForm.date_local}T00:00:00Z`).getUTCDay() + 6) % 7 : -1;
  const skippableRules = rules.filter((rule) => rule.weekdays.includes(weekdayOfException));

  return <div className="section-stack"><h2>Zeitmodell</h2>
    <p className="intro">Feste Zeiten und Ausnahmen werden nur auf diesem Laptop gespeichert. Puffer zählen zur belegten Zeit. Die Tagesansicht zeigt tatsächliche freie Minuten auch an Tagen mit Zeitumstellung.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Tagesverfügbarkeit</h3><div className="work-form">
      <label>Datum <input aria-label="Zeitmodell-Datum" type="date" value={date} onChange={(event) => { dateTouched.current = true; setDate(event.target.value); }} /></label>
      <label>Ansicht-Zeitzone <input aria-label="Zeitmodell-Zeitzone" value={timezone} onChange={(event) => { timezoneTouched.current = true; setTimezone(event.target.value); }} /></label>
    </div>
      {previewError && <p className="notice error" role="alert">{previewError}</p>}
      {availability && <><p><strong>{availability.free_minutes} Minuten frei</strong> · {availability.blocked_minutes} Minuten belegt · {availability.total_minutes} Minuten Tag</p>
        <h4>Belegt</h4><div className="item-list">{availability.busy.length ? availability.busy.map((slot, index) => <div className="item-row" key={`${slot.source}-${slot.id}-${slot.occurrence_date}-${index}`}><div><strong>{slot.title} · {kindLabel(slot.kind)}</strong><small>{localLabel(slot.start_local)} bis {localLabel(slot.end_local)} · {slot.minutes} Min.</small></div></div>) : <p>Keine festen Zeiten.</p>}</div>
        <h4>Frei</h4><div className="item-list">{availability.free.map((slot) => <div className="item-row" key={slot.start_local}><div><strong>{slot.minutes} Minuten</strong><small>{localLabel(slot.start_local)} bis {localLabel(slot.end_local)}</small></div></div>)}</div>
      </>}
    </section>
    <section className="card"><h3>{editingRule ? 'Zeitregel ändern' : 'Wiederkehrende Zeit eintragen'}</h3><div className="work-form">
      <label>Titel <input aria-label="Zeitregel-Titel" value={ruleForm.title} onChange={(event) => setRuleForm({ ...ruleForm, title: event.target.value })} /></label>
      <label>Art <select aria-label="Zeitregel-Art" value={ruleForm.kind} onChange={(event) => setRuleForm({ ...ruleForm, kind: event.target.value as Kind })}>{kinds.map(([id, name]) => <option value={id} key={id}>{name}</option>)}</select></label>
      <fieldset><legend>Wochentage</legend><div className="weekday-grid">{weekdays.map((name, index) => <label key={name}><input type="checkbox" checked={ruleForm.weekdays.includes(index)} onChange={(event) => setRuleForm({ ...ruleForm, weekdays: event.target.checked ? [...ruleForm.weekdays, index].sort() : ruleForm.weekdays.filter((day) => day !== index) })} />{name}</label>)}</div></fieldset>
      <label>Beginn <input aria-label="Zeitregel-Beginn" type="time" value={ruleForm.start_time} onChange={(event) => setRuleForm({ ...ruleForm, start_time: event.target.value })} /></label>
      <label>Ende <input aria-label="Zeitregel-Ende" type="time" value={ruleForm.end_time} onChange={(event) => setRuleForm({ ...ruleForm, end_time: event.target.value })} /></label>
      <p>Ende vor oder gleich Beginn bedeutet: endet am nächsten Tag.</p>
      <label>Puffer davor (Minuten) <input aria-label="Puffer davor" type="number" min="0" max="180" value={ruleForm.buffer_before} onChange={(event) => setRuleForm({ ...ruleForm, buffer_before: Number(event.target.value) })} /></label>
      <label>Puffer danach (Minuten) <input aria-label="Puffer danach" type="number" min="0" max="180" value={ruleForm.buffer_after} onChange={(event) => setRuleForm({ ...ruleForm, buffer_after: Number(event.target.value) })} /></label>
      <label>Zeitzone <input aria-label="Zeitregel-Zeitzone" value={ruleForm.timezone} onChange={(event) => { ruleTimezoneTouched.current = true; setRuleForm({ ...ruleForm, timezone: event.target.value }); }} /></label>
      <div className="item-actions"><button className="primary" disabled={busy || !ruleForm.title.trim() || !ruleForm.weekdays.length || !ruleForm.start_time || !ruleForm.end_time || !ruleForm.timezone} onClick={saveRule}>{editingRule ? 'Regel speichern' : 'Regel anlegen'}</button>{editingRule && <button disabled={busy} onClick={() => { setEditingRule(null); setRuleForm(emptyRule(timezone)); }}>Abbrechen</button>}</div>
    </div></section>
    <section className="card"><h3>Wochenregeln</h3><div className="item-list">{rules.length ? rules.map((rule) => <div className="item-row" key={rule.id}><div><strong>{rule.title} · {kindLabel(rule.kind)}</strong><small>{rule.weekdays.map((day) => weekdays[day]).join(', ')} · {rule.start_time}–{rule.end_time} · {rule.timezone}</small><small>Puffer {rule.buffer_before} Min. davor / {rule.buffer_after} Min. danach</small></div><div className="item-actions"><button disabled={busy} onClick={() => chooseRule(rule)}>Ändern</button>{confirmDelete === `rule-${rule.id}` ? <button disabled={busy} onClick={() => deleteItem('rules', rule.id, rule.version)}>Löschen bestätigen</button> : <button disabled={busy} onClick={() => setConfirmDelete(`rule-${rule.id}`)}>Löschen…</button>}</div></div>) : <p>Noch keine Wochenregel.</p>}</div></section>
    <section className="card"><h3>{editingException ? 'Tagesausnahme ändern' : 'Tagesausnahme eintragen'}</h3><div className="work-form">
      <label>Art der Ausnahme <select aria-label="Ausnahme-Art" value={exceptionForm.mode} onChange={(event) => setExceptionForm({ ...exceptionForm, mode: event.target.value as 'skip' | 'block', rule_id: '' })}><option value="skip">Regel an diesem Tag aussetzen</option><option value="block">Zusätzliche feste Zeit</option></select></label>
      <label>Datum <input aria-label="Ausnahme-Datum" type="date" value={exceptionForm.date_local} onChange={(event) => { exceptionDateTouched.current = true; setExceptionForm({ ...exceptionForm, date_local: event.target.value, rule_id: '' }); }} /></label>
      {exceptionForm.mode === 'skip' ? <label>Wochenregel <select aria-label="Ausfallende Regel" value={exceptionForm.rule_id} onChange={(event) => setExceptionForm({ ...exceptionForm, rule_id: event.target.value })}><option value="">Regel wählen</option>{skippableRules.map((rule) => <option value={rule.id} key={rule.id}>{rule.title}</option>)}</select></label> : <>
        <label>Titel <input aria-label="Ausnahme-Titel" value={exceptionForm.title} onChange={(event) => setExceptionForm({ ...exceptionForm, title: event.target.value })} /></label>
        <label>Art <select aria-label="Zusatzblock-Art" value={exceptionForm.kind} onChange={(event) => setExceptionForm({ ...exceptionForm, kind: event.target.value as Kind })}>{kinds.map(([id, name]) => <option value={id} key={id}>{name}</option>)}</select></label>
        <label>Beginn <input aria-label="Ausnahme-Beginn" type="time" value={exceptionForm.start_time} onChange={(event) => setExceptionForm({ ...exceptionForm, start_time: event.target.value })} /></label>
        <label>Ende <input aria-label="Ausnahme-Ende" type="time" value={exceptionForm.end_time} onChange={(event) => setExceptionForm({ ...exceptionForm, end_time: event.target.value })} /></label>
        <label>Puffer davor (Minuten) <input aria-label="Ausnahme-Puffer davor" type="number" min="0" max="180" value={exceptionForm.buffer_before} onChange={(event) => setExceptionForm({ ...exceptionForm, buffer_before: Number(event.target.value) })} /></label>
        <label>Puffer danach (Minuten) <input aria-label="Ausnahme-Puffer danach" type="number" min="0" max="180" value={exceptionForm.buffer_after} onChange={(event) => setExceptionForm({ ...exceptionForm, buffer_after: Number(event.target.value) })} /></label>
        <label>Zeitzone <input aria-label="Ausnahme-Zeitzone" value={exceptionForm.timezone} onChange={(event) => { exceptionTimezoneTouched.current = true; setExceptionForm({ ...exceptionForm, timezone: event.target.value }); }} /></label>
      </>}
      <div className="item-actions"><button className="primary" disabled={busy || !exceptionForm.date_local || (exceptionForm.mode === 'skip' ? !exceptionForm.rule_id : !exceptionForm.title.trim() || !exceptionForm.start_time || !exceptionForm.end_time || !exceptionForm.timezone)} onClick={saveException}>{editingException ? 'Ausnahme speichern' : 'Ausnahme anlegen'}</button>{editingException && <button disabled={busy} onClick={() => { setEditingException(null); setExceptionForm(emptyException(timezone, date)); }}>Abbrechen</button>}</div>
    </div></section>
    <section className="card"><h3>Gespeicherte Ausnahmen</h3><div className="item-list">{exceptions.length ? exceptions.map((item) => <div className="item-row" key={item.id}><div><strong>{item.date_local} · {item.mode === 'skip' ? `Ausfall: ${rules.find((rule) => rule.id === item.rule_id)?.title || 'Regel gelöscht'}` : item.title}</strong>{item.mode === 'block' && <small>{item.start_time}–{item.end_time} · {item.timezone} · Puffer {item.buffer_before}/{item.buffer_after} Min.</small>}</div><div className="item-actions"><button disabled={busy} onClick={() => chooseException(item)}>Ändern</button>{confirmDelete === `exception-${item.id}` ? <button disabled={busy} onClick={() => deleteItem('exceptions', item.id, item.version)}>Löschen bestätigen</button> : <button disabled={busy} onClick={() => setConfirmDelete(`exception-${item.id}`)}>Löschen…</button>}</div></div>) : <p>Noch keine Ausnahmen.</p>}</div></section>
  </div>;
}
