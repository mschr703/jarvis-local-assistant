import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { api } from './api';

export type Settings = {
  provider: 'ollama' | 'openrouter'; model: string | null; ollama_model: string;
  openrouter_model: string | null; daily_budget_usd: number; spent_today_usd: number;
  max_output_tokens: number; max_context_chars: number; timezone: string;
  personal_style_label: string;
  model_routes: Partial<Record<'ollama' | 'openrouter', Partial<Record<ModelTask, string>>>>;
  effective_models: Record<ModelTask, { model: string | null; reason: string }>;
};
type ModelTask = 'chat' | 'draft' | 'plan' | 'research';
const modelTasks: [ModelTask, string][] = [
  ['chat', 'Chat'], ['draft', 'Entwurf und Text'], ['plan', 'Plan'], ['research', 'Recherche'],
];
type UsageReceipt = {
  id: string; purpose: string; provider: string; model: string;
  input_tokens: number | null; output_tokens: number | null;
  cost_usd: number; cost_kind: string; status: string; created_at: string;
};
type ExtensionPair = { extension_id: string; created_at: string };
type PairingCode = { code: string; expires_at: string };
type BackupSchedule = { configured: boolean; enabled: boolean; directory?: string;
  time_local?: string; timezone?: string; keep?: number; last_status: string;
  last_error: string | null; last_success_at?: string | null; last_archive?: string | null };
const purposeLabels: Record<string, string> = { chat: 'Chat', draft: 'Entwurf', plan: 'Plan', research: 'Recherche' };
const statusLabels: Record<string, string> = {
  complete: 'abgeschlossen', running: 'läuft', interrupted: 'unterbrochen',
  failed: 'fehlgeschlagen', cancelled: 'abgebrochen', empty: 'ohne Antwort',
};

function costDescription(item: UsageReceipt): string {
  if (item.provider === 'ollama') return 'keine API-Gebühr (lokal)';
  const kind = item.cost_kind === 'reported' ? 'gemeldet' :
    item.cost_kind.startsWith('estimated') ? 'geschätzt' :
      item.cost_kind === 'preflight_reservation' ? 'reserviert' :
        item.cost_kind.startsWith('upper_bound') ? 'Obergrenze' : 'unklar';
  return `${item.cost_usd.toFixed(5)} USD · ${kind}`;
}

export default function SettingsPanel({ onSaved, healthStatus, visible, settings, setSettings }: {
  onSaved: () => void; healthStatus: string; visible: boolean;
  settings: Settings | null; setSettings: Dispatch<SetStateAction<Settings | null>>;
}) {
  const [message, setMessage] = useState('');
  const [retry, setRetry] = useState(0);
  const [receipts, setReceipts] = useState<UsageReceipt[] | null>(null);
  const [usageError, setUsageError] = useState('');
  const [saving, setSaving] = useState(false);
  const [extensionPairs, setExtensionPairs] = useState<ExtensionPair[]>([]);
  const [pairingCode, setPairingCode] = useState<PairingCode | null>(null);
  const [extensionMessage, setExtensionMessage] = useState('');
  const [backupSchedule, setBackupSchedule] = useState<BackupSchedule | null>(null);
  const [backupError, setBackupError] = useState('');
  const [backupBusy, setBackupBusy] = useState(false);
  const backupBusyRef = useRef(false);
  const backupRequest = useRef(0);
  const savingRef = useRef(false);
  const editRevision = useRef(0);
  const usageRequest = useRef(0);
  function change<K extends keyof Settings>(key: K, value: Settings[K]) {
    ++editRevision.current;
    setSettings((current) => current ? { ...current, [key]: value } : current);
  }
  function changeRoute(task: ModelTask, value: string) {
    if (!settings) return;
    const provider = settings.provider;
    const current = { ...(settings.model_routes[provider] || {}) };
    if (value) current[task] = value;
    else delete current[task];
    const routes = { ...settings.model_routes, [provider]: current };
    change('model_routes', routes);
  }
  const loadUsage = useCallback(async () => {
    const request = ++usageRequest.current;
    try {
      const result = await api<UsageReceipt[]>('/usage');
      if (request === usageRequest.current) { setReceipts(result); setUsageError(''); }
    } catch (error) {
      if (request === usageRequest.current) setUsageError((error as Error).message);
    }
  }, []);
  useEffect(() => {
    if (visible) void loadUsage();
    else ++usageRequest.current;
  }, [healthStatus, visible, loadUsage]);
  const loadExtensionPairs = useCallback(async () => {
    try { setExtensionPairs(await api<ExtensionPair[]>('/extension-management/pairs')); setExtensionMessage(''); }
    catch (error) { setExtensionMessage((error as Error).message); }
  }, []);
  useEffect(() => { if (visible) void loadExtensionPairs(); }, [visible, loadExtensionPairs]);
  const loadBackupSchedule = useCallback(async () => {
    if (backupBusyRef.current) return;
    const request = ++backupRequest.current;
    try {
      const result = await api<BackupSchedule>('/backups/schedule');
      if (request === backupRequest.current) { setBackupSchedule(result); setBackupError(''); }
    } catch (error) {
      if (request === backupRequest.current) setBackupError((error as Error).message);
    }
  }, []);
  useEffect(() => {
    if (!visible) { ++backupRequest.current; return; }
    void loadBackupSchedule();
    const timer = window.setInterval(() => void loadBackupSchedule(), 15_000);
    return () => { ++backupRequest.current; window.clearInterval(timer); };
  }, [visible, loadBackupSchedule]);
  useEffect(() => {
    if (settings) return;
    let active = true;
    setMessage('');
    void api<Settings>('/settings').then((value) => { if (active) setSettings(value); })
      .catch((error: Error) => { if (active) setMessage(error.message); });
    return () => { active = false; };
  }, [healthStatus, retry, settings]);
  if (!settings) return <section className="card"><h3>Einstellungen</h3><p>{message || 'Lade…'}</p>{message && <button onClick={() => setRetry((current) => current + 1)}>Erneut laden</button>}</section>;
  const save = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setMessage('Speichere Einstellungen…');
    const submitted = settings;
    const revision = editRevision.current;
    try {
      const result = await api<Settings>('/settings', { method: 'PATCH', body: JSON.stringify({
        provider: submitted.provider,
        ollama_model: submitted.ollama_model,
        openrouter_model: submitted.openrouter_model || undefined,
        model_routes: submitted.model_routes,
        daily_budget_usd: submitted.daily_budget_usd,
        max_output_tokens: submitted.max_output_tokens,
        max_context_chars: submitted.max_context_chars,
        timezone: submitted.timezone,
        personal_style_label: submitted.personal_style_label,
      }) });
      const hasNewEdits = editRevision.current !== revision;
      if (!hasNewEdits) setSettings((current) => current === submitted ? result : current);
      setMessage(hasNewEdits ? 'Vorherige Werte gespeichert; neuere Eingaben sind noch ungespeichert.' : 'Gespeichert.');
      onSaved();
    } catch (error) { setMessage((error as Error).message); }
    finally { savingRef.current = false; setSaving(false); }
  };
  const issuePairingCode = async () => {
    try {
      setPairingCode(await api<PairingCode>('/extension-management/code', { method: 'POST' }));
      setExtensionMessage('Code innerhalb von fünf Minuten in die Edge-Erweiterung eingeben.');
    } catch (error) { setExtensionMessage((error as Error).message); }
  };
  const revokeExtension = async (id: string) => {
    try {
      await api(`/extension-management/pairs/${id}`, { method: 'DELETE' });
      setExtensionMessage('Verbindung getrennt.');
      await loadExtensionPairs();
    } catch (error) { setExtensionMessage((error as Error).message); }
  };
  const runBackupNow = async () => {
    if (backupBusyRef.current) return;
    backupBusyRef.current = true;
    ++backupRequest.current;
    setBackupBusy(true);
    try { setBackupSchedule(await api<BackupSchedule>('/backups/schedule/run', { method: 'POST' })); setBackupError(''); }
    catch (error) { setBackupError((error as Error).message); }
    finally { backupBusyRef.current = false; setBackupBusy(false); }
  };
  return <section className="card settings">
    <h3>Modell und Limits</h3>
    <p>OpenRouter-Schlüssel unter „Zugänge“ mit „OpenRouter-Eintrag vorbereiten“ für diesen Windows-Benutzer geschützt speichern oder vor dem Start als Backend-Prozessvariable setzen. Die Prozessvariable hat Vorrang. Den Schlüssel nicht im Projektordner speichern. Cloud-Verarbeitung verlangt weiterhin eine bewusste Freigabe je Anfrage.</p>
    <div className="form-grid">
      <label>Provider<select value={settings.provider} onChange={(event) => change('provider', event.target.value as Settings['provider'])}><option value="ollama">Ollama (lokal)</option><option value="openrouter">OpenRouter</option></select></label>
      <label>Ollama-Modell<input value={settings.ollama_model} onChange={(event) => change('ollama_model', event.target.value)} /></label>
      <label>OpenRouter-Modell-ID<input value={settings.openrouter_model || ''} onChange={(event) => change('openrouter_model', event.target.value)} placeholder="Aus dem OpenRouter-Katalog" /></label>
      <label>Tagesbudget (USD)<input type="number" min="0" max="100" step="0.01" value={settings.daily_budget_usd} onChange={(event) => change('daily_budget_usd', Number(event.target.value))} /></label>
      <label>Max. Ausgabetoken<input type="number" min="1" max="8192" value={settings.max_output_tokens} onChange={(event) => change('max_output_tokens', Number(event.target.value))} /></label>
      <label>Max. Eingabezeichen<input type="number" min="4000" max="200000" value={settings.max_context_chars} onChange={(event) => change('max_context_chars', Number(event.target.value))} /></label>
      <label>Zeitzone (IANA)<input value={settings.timezone} onChange={(event) => change('timezone', event.target.value)} /></label>
      <label>Name für persönliches Schreibprofil<input maxLength={60} value={settings.personal_style_label} onChange={(event) => change('personal_style_label', event.target.value)} /></label>
    </div>
    <h4>Modell je Aufgabe</h4>
    <p>Leere Felder verwenden das Standardmodell des gewählten Providers. Die angezeigte Wahl wird erst nach „Einstellungen speichern“ aktiv. Es gibt keinen automatischen Provider- oder Modellwechsel. OpenRouter-Preise und Tagesbudget werden vor jedem bezahlten Aufruf geprüft; Cloudfreigaben bleiben erforderlich.</p>
    <div className="form-grid">{modelTasks.map(([task, label]) => {
      const selected = settings.model_routes[settings.provider]?.[task] || '';
      const fallback = settings.provider === 'ollama' ? settings.ollama_model : settings.openrouter_model;
      return <label key={task}>{label}<input aria-label={`Modell für ${label}`} maxLength={120}
        style={{ minWidth: 0, maxWidth: '100%' }}
        value={selected} placeholder={fallback || 'Standardmodell noch nicht gesetzt'}
        onChange={(event) => changeRoute(task, event.target.value)} />
        <small>{selected ? 'Aufgabenregel' : 'Standardmodell'} · {selected || fallback || 'nicht eingerichtet'}</small>
      </label>;
    })}</div>
    <p>Heute erfasst: {settings.spent_today_usd.toFixed(5)} USD. Bei fehlender Usage wird die vorab berechnete Obergrenze gespeichert.</p>
    <button className="primary" disabled={saving} onClick={() => void save()}>{saving ? 'Speichere…' : 'Einstellungen speichern'}</button>
    {message && <span className="form-message" role="status">{message}</span>}
    <h4>Geplante verschlüsselte Backups</h4>
    <p>Einmalig bei gestopptem Jarvis mit <code>scripts/backup_schedule.py configure</code> einrichten. Die Sicherung läuft nur, während Jarvis auf diesem Laptop gestartet ist.</p>
    {backupError && <p className="notice error" role="status">Backup-Status: {backupError}</p>}
    {!backupSchedule ? <p>Lade Backup-Status…</p> : !backupSchedule.configured ?
      <p>Keine automatische Sicherung eingerichtet.</p> : <>
        <p>{backupSchedule.enabled ? 'Aktiv' : 'Deaktiviert'} · täglich {backupSchedule.time_local} {backupSchedule.timezone} · {backupSchedule.keep} Archive behalten</p>
        <p>Letzter Lauf: {backupSchedule.last_status}{backupSchedule.last_success_at ? ` · Erfolg ${new Date(backupSchedule.last_success_at).toLocaleString('de-DE')}` : ''}</p>
        {backupSchedule.last_error && <p className="notice error" role="status">Backupfehler: {backupSchedule.last_error}</p>}
        {backupSchedule.last_archive && <p className="hint">Letztes geprüftes Archiv: {backupSchedule.last_archive}</p>}
        <div className="item-actions"><button onClick={() => void loadBackupSchedule()}>Backup-Status aktualisieren</button>
          {backupSchedule.enabled && <button disabled={backupBusy} onClick={() => void runBackupNow()}>{backupBusy ? 'Sichere und prüfe…' : 'Jetzt sichern und prüfen'}</button>}</div>
      </>}
    <h4>Letzte Modellläufe</h4>
    <div className="item-actions"><button onClick={() => void loadUsage()}>Belege aktualisieren</button></div>
    {usageError ? <p className="notice error">Kostenbelege konnten nicht geladen werden: {usageError}</p> :
      receipts === null ? <p>Lade Kostenbelege…</p> : receipts.length === 0 ? <p>Noch keine Modellaufrufe.</p> :
        <div className="item-list">{receipts.map((item) => <div className="item-row" key={item.id}><div>
          <strong>{purposeLabels[item.purpose] || item.purpose} · {item.provider} / {item.model} · {statusLabels[item.status] || item.status}</strong>
          <small>{new Date(item.created_at).toLocaleString('de-DE')} · {item.input_tokens ?? '?'} Eingabe / {item.output_tokens ?? '?'} Ausgabe · {costDescription(item)}</small>
        </div></div>)}</div>}
    <h4>Edge-Erweiterung koppeln</h4>
    <p>Nur nach einem Klick in der Erweiterung werden Tabdaten übertragen. Ein Kopplungscode gilt fünf Minuten; die Verbindung kann hier getrennt werden.</p>
    <div className="item-actions"><button onClick={() => void issuePairingCode()}>Neuen Kopplungscode erzeugen</button><button onClick={() => void loadExtensionPairs()}>Verbindungen aktualisieren</button></div>
    {pairingCode && <p className="notice">Code: <code>{pairingCode.code}</code><br />Gültig bis {new Date(pairingCode.expires_at).toLocaleTimeString('de-DE')}</p>}
    {extensionMessage && <p className="form-message" role="status">{extensionMessage}</p>}
    <div className="item-list">{extensionPairs.map((pair) => <div className="item-row" key={pair.extension_id}><div><strong>Edge-Erweiterung {pair.extension_id}</strong><small>Gekoppelt: {new Date(pair.created_at).toLocaleString('de-DE')}</small></div><button onClick={() => void revokeExtension(pair.extension_id)}>Trennen</button></div>)}</div>
  </section>;
}
