import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type RuleVersion = { version: number; content: string; created_at: string };
type Activation = { id: number; from_version: number; to_version: number; changed_at: string };
type RuleHistory = { active_version: number; revision: number; versions: RuleVersion[]; activations: Activation[] };

export default function ChatRulesView({ visible, scope = 'chat' }: { visible: boolean; scope?: 'chat' | 'draft' | 'plan' }) {
  const isDraft = scope === 'draft';
  const isPlan = scope === 'plan';
  const rulesPath = isDraft ? '/draft-rules' : isPlan ? '/plan-rules' : '/chat-rules';
  const label = isDraft ? 'Entwurfsregel' : isPlan ? 'Planregel' : 'Chat-Regel';
  const [history, setHistory] = useState<RuleHistory | null>(null);
  const [content, setContent] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const action = useRef(false);
  const refresh = async () => {
    const current = ++request.current;
    const next = await api<RuleHistory>(rulesPath);
    if (current === request.current) setHistory(next);
  };
  useEffect(() => {
    if (!visible) return;
    void refresh().catch((error: Error) => setNotice(error.message));
    return () => { ++request.current; };
  }, [visible, scope]);
  const change = async (path: string, method: 'POST' | 'PUT', body: object) => {
    if (action.current || !history) return;
    action.current = true; setBusy(true); setNotice('');
    try {
      const next = await api<RuleHistory>(path, { method, body: JSON.stringify(body) });
      setHistory(next);
      setContent('');
      setNotice(`Aktive ${label} gespeichert. Bereits erzeugte ${isDraft ? 'Entwürfe' : isPlan ? 'Pläne' : 'Antworten'} behalten ihre Fassung.`);
    } catch (error) {
      setNotice((error as Error).message);
      await refresh().catch(() => undefined);
    } finally { action.current = false; setBusy(false); }
  };
  return <div className="section-stack"><h2>{isDraft ? 'Entwurfsregeln' : isPlan ? 'Planregeln' : 'Chat-Regeln'}</h2>
    <p className="intro">{isDraft ? 'Zusätzliche Anweisung für neue Entwürfe. Stilprofil und Schreibproben bleiben getrennt. Quellen- und Versandgrenzen gelten weiter. Bei OpenRouter wird die Regel mit jeder freigegebenen Entwurfsanfrage gesendet.' : isPlan ? 'Zusätzliche Anweisung für neue Mehrschrittplan-Vorschläge. Werkzeugregister, Freigaben und Grenzen bleiben in der App. Bei OpenRouter wird die Regel mit jedem ausdrücklich freigegebenen Planvorschlag gesendet.' : 'Zusätzliche Anweisungen für neue Chatantworten. Die technische Quellen- und Werkzeugfreigabe bleibt in der App. Entwürfe und Mehrschrittpläne verwenden diese Chat-Regel nicht. Bei OpenRouter wird die aktive Regel mit jeder freigegebenen Chatanfrage an den Provider gesendet.'}</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Aktive Fassung</h3>
      {history ? <><p>Version {history.active_version} · Revision {history.revision}</p><p>{history.versions.find((item) => item.version === history.active_version)?.content || 'Nur die feste Grundregel ist aktiv.'}</p></> : <p>Lade {isDraft ? 'Entwurfsregeln' : isPlan ? 'Planregeln' : 'Chat-Regeln'}…</p>}
    </section>
    <section className="card"><h3>Neue Fassung</h3><p>Die Eingabe wird erst nach „Neue Fassung aktivieren“ verwendet. Alte Fassungen bleiben erhalten.</p>
      <div className="memory-form"><textarea aria-label={`Neue ${label}`} maxLength={2000} value={content} onChange={(event) => setContent(event.target.value)} placeholder={isDraft ? 'z. B. Verwende kurze Absätze und nenne am Ende offene Fragen.' : isPlan ? 'z. B. Bevorzuge kurze Pläne mit möglichst wenigen Werkzeugschritten.' : 'z. B. Antworte bei Projektfragen mit einer kurzen Zusammenfassung und offenen Punkten.'} />
        <button className="primary" disabled={busy || !history || !content.trim()} onClick={() => void change(`${rulesPath}/versions`, 'POST', { content, expected_revision: history?.revision })}>{busy ? 'Speichere…' : 'Neue Fassung aktivieren'}</button></div>
    </section>
    <section className="card"><h3>Fassungen und Rückkehr</h3><div className="item-list">{history?.versions.map((item) => <div className="item-row" key={item.version}><div><strong>Version {item.version}{item.version === history.active_version ? ' · aktiv' : ''}</strong><small>{new Date(item.created_at).toLocaleString('de-DE')}</small><p>{item.content || 'Feste Grundregel ohne Zusatz'}</p></div>{item.version !== history.active_version && <button disabled={busy} onClick={() => void change(`${rulesPath}/active`, 'PUT', { version: item.version, expected_revision: history.revision })}>Diese Fassung aktivieren</button>}</div>)}</div>
      <h4>Aktivierungsverlauf</h4><div className="item-list">{history?.activations.length ? history.activations.map((item) => <div className="item-row" key={item.id}><span>Version {item.from_version} → {item.to_version}</span><small>{new Date(item.changed_at).toLocaleString('de-DE')}</small></div>) : <p>Noch kein Wechsel.</p>}</div>
    </section>
  </div>;
}
