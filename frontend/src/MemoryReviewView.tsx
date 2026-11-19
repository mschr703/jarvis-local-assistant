import { useEffect, useRef, useState } from 'react';
import { api, type Chat, type ChatSummary } from './api';

type Proposal = {
  id: string; chat_id: string; chat_title: string; snapshot_message_id: string;
  message_id: string; message_created_at: string; source_quote: string;
  candidate_text: string; decided_text: string | null; memory_id: string | null;
  current_memory_text: string | null; status: 'pending' | 'rejected' | 'accepted' | 'deleted';
  valid_from: string | null; valid_until: string | null;
  validity_state: 'current' | 'future' | 'expired';
  source_available: boolean; possible_duplicates: { id: string; text: string }[];
  last_used: { chat_id: string; assistant_message_id: string;
    text_snapshot: string; source_quote: string; used_at: string } | null;
  version: number; created_at: string;
  events: { action: string; text: string | null; created_at: string;
    valid_from: string | null; valid_until: string | null }[];
};
type PossibleConflict = { first_id: string; second_id: string;
  first_quote: string; second_quote: string; reason: string };

const labels = {
  pending: 'Offen', rejected: 'Verworfen', accepted: 'Bestätigt', deleted: 'Gelöscht',
};
type StatusFilter = 'all' | Proposal['status'];
const statusOrder: Record<Proposal['status'], number> = {
  pending: 0, accepted: 1, rejected: 2, deleted: 3,
};

export default function MemoryReviewView({ visible, configured, provider, onChat }: {
  visible: boolean; configured: boolean; provider: string; onChat: (chatId: string, messageId: string) => void;
}) {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [chatId, setChatId] = useState('');
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [validityDrafts, setValidityDrafts] = useState<Record<string, { from: string; until: string }>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [conflicts, setConflicts] = useState<PossibleConflict[] | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const request = useRef(0);

  useEffect(() => {
    if (!visible) return;
    const current = ++request.current;
    void Promise.all([api<ChatSummary[]>('/chats'), api<Proposal[]>('/memory-proposals')])
      .then(([chatList, items]) => {
        if (current !== request.current) return;
        setChats(chatList); setProposals(items);
        setChatId((prior) => chatList.some((chat) => chat.id === prior) ? prior : chatList[0]?.id ?? '');
      }).catch((error: Error) => { if (current === request.current) setNotice(error.message); });
    return () => { ++request.current; };
  }, [visible]);

  const generate = async () => {
    if (!chatId || busy || provider !== 'ollama') return;
    setBusy(true); setNotice('Ollama prüft höchstens sechs Nutzeraussagen…');
    try {
      const chat = await api<Chat>(`/chats/${chatId}`);
      const last = chat.messages.at(-1);
      if (!last) { setNotice('Dieses Gespräch enthält noch keine Nachrichten.'); return; }
      const items = await api<Proposal[]>(`/chats/${chatId}/memory-proposals`, {
        method: 'POST', body: JSON.stringify({ last_message_id: last.id }),
      });
      setProposals(await api<Proposal[]>('/memory-proposals'));
      setNotice(items.length ? `${items.length} Vorschlag/Vorschläge zur Prüfung bereit.` :
        'Keine belegten Erinnerungsvorschläge gefunden.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };

  const decide = async (item: Proposal, action: 'accept' | 'reject' | 'correct' | 'delete') => {
    if (busy) return;
    if (action === 'delete' && !window.confirm('Diese bestätigte Erinnerung löschen? Der Entscheidungsverlauf bleibt erhalten.')) return;
    const text = drafts[item.id] ?? (action === 'correct' ? item.current_memory_text || '' : item.candidate_text);
    const validity = validityDrafts[item.id] ?? { from: item.valid_from || '', until: item.valid_until || '' };
    setBusy(true); setNotice('');
    try {
      const updated = await api<Proposal>(`/memory-proposals/${item.id}/decision`, {
        method: 'POST', body: JSON.stringify({ expected_version: item.version, action,
          ...(action === 'accept' || action === 'correct' ? { text,
            valid_from: validity.from, valid_until: validity.until } : {}) }),
      });
      setProposals((current) => current.map((proposal) => proposal.id === item.id ? updated : proposal));
      setConflicts(null);
      setDrafts((current) => { const next = { ...current }; delete next[item.id]; return next; });
      setValidityDrafts((current) => { const next = { ...current }; delete next[item.id]; return next; });
      setNotice(({ accept: 'Erinnerung bewusst gespeichert.', reject: 'Vorschlag verworfen.',
        correct: 'Bestätigte Erinnerung korrigiert.', delete: 'Erinnerung gelöscht; Entscheidungsverlauf erhalten.' })[action]);
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };

  const checkConflicts = async () => {
    if (busy || provider !== 'ollama') return;
    setBusy(true); setNotice('Ollama prüft bestätigte, aktuell gültige Erinnerungen…');
    try {
      const results = await api<PossibleConflict[]>('/memory-proposals/conflicts', { method: 'POST' });
      setConflicts(results);
      setNotice(results.length ? `${results.length} mögliche Widersprüche zur eigenen Prüfung.` :
        'Keine belegten möglichen Widersprüche gefunden. Das ist keine Garantie für Widerspruchsfreiheit.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setBusy(false); }
  };

  const selected = proposals.filter((item) => !chatId || item.chat_id === chatId);
  const counts = {
    all: selected.length,
    pending: selected.filter((item) => item.status === 'pending').length,
    accepted: selected.filter((item) => item.status === 'accepted').length,
    rejected: selected.filter((item) => item.status === 'rejected').length,
    deleted: selected.filter((item) => item.status === 'deleted').length,
  };
  const shown = selected.filter((item) => statusFilter === 'all' || item.status === statusFilter)
    .sort((a, b) => statusOrder[a.status] - statusOrder[b.status]);
  return <div className="stack memory-review">
    <p className="eyebrow">LOKAL PRÜFEN</p><h2>Gedächtnisprüfung</h2>
    <p className="intro">Jarvis schlägt aus deinen eigenen Chatnachrichten bis zu drei Erinnerungen vor. Erst dein Bestätigen speichert etwas im bewussten Wissen. Quelle und Entscheidungen bleiben nachvollziehbar.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Gespräch wählen</h3>
      <label>Gespräch<select aria-label="Gespräch für Erinnerungen" value={chatId}
        onChange={(event) => setChatId(event.target.value)} disabled={busy}>
        <option value="">Alle Vorschläge anzeigen</option>
        {chats.map((chat) => <option key={chat.id} value={chat.id}>{chat.title}</option>)}
      </select></label>
      <button className="primary" onClick={() => void generate()} disabled={!chatId || busy || !configured || provider !== 'ollama'}>
        {busy ? 'Bitte warten…' : 'Erinnerungen vorschlagen'}</button>
      {provider !== 'ollama' && <small>Vorschläge verwenden nur lokales Ollama.</small>}
      <p className="muted">Geprüft werden höchstens sechs aktuelle, fertige Nutzernachrichten. Assistententext zählt nicht als Quelle. Ein erneuter Klick auf denselben Gesprächsstand erzeugt keine Duplikate.</p>
    </section>
    <section className="card memory-inbox"><div className="memory-inbox-heading"><div><h3>Vorschläge und Verlauf</h3><p>{counts.pending} offen · {counts.accepted} bestätigt · {counts.rejected + counts.deleted} abgeschlossen</p></div></div>
      <div className="memory-filters" role="group" aria-label="Erinnerungen nach Status filtern">
        {(['all', 'pending', 'accepted', 'rejected', 'deleted'] as const).map((status) =>
          <button key={status} type="button" aria-pressed={statusFilter === status}
            onClick={() => setStatusFilter(status)}>{status === 'all' ? 'Alle' : labels[status]} <span>{counts[status]}</span></button>)}
      </div>
      {!shown.length && <p className="memory-empty">Für diese Auswahl gibt es noch keine Vorschläge.</p>}
      <div className="item-list">{shown.map((item) => {
        const externalChange = item.status === 'accepted' && item.current_memory_text !== item.decided_text;
        const editable = item.status === 'pending' || item.status === 'accepted';
        const draft = drafts[item.id] ?? (item.status === 'accepted' ? item.current_memory_text || '' : item.candidate_text);
        const validity = validityDrafts[item.id] ?? { from: item.valid_from || '', until: item.valid_until || '' };
        return <div className={`item-row memory-proposal memory-proposal-${item.status}`} key={item.id}><div>
          <div className="memory-proposal-head"><span className={`memory-status memory-status-${item.status}`}>{labels[item.status]}</span><div><strong>{item.chat_title}</strong><small>Originalnachricht: {new Date(item.message_created_at).toLocaleString('de-DE')} · {item.message_id.slice(0, 8)}…</small></div></div>
          <div className="memory-source"><small>Originalstelle</small><blockquote>„{item.source_quote}“</blockquote></div>
          {item.source_available && <button onClick={() => onChat(item.chat_id, item.message_id)}>Original im Chat öffnen</button>}
          {!item.source_available && <small>Originalchat nicht mehr vorhanden; der gesicherte Textausschnitt bleibt als Herkunft.</small>}
          <p className="memory-candidate"><small>Modellvorschlag</small>{item.candidate_text}</p>
          {item.possible_duplicates.length > 0 && <p className="notice">Mögliches identisches Duplikat: {item.possible_duplicates.map((duplicate) => duplicate.text).join(' · ')}</p>}
          {editable && <label>{item.status === 'pending' ? 'Vor dem Speichern korrigieren' : 'Bestätigte Erinnerung korrigieren'}
            <textarea aria-label={`Erinnerungstext ${item.id}`} value={draft} maxLength={500}
              onChange={(event) => setDrafts((current) => ({ ...current, [item.id]: event.target.value }))}
              disabled={busy || externalChange} /></label>}
          {editable && <div className="item-actions"><label>Gültig ab (optional)<input type="date" aria-label={`Gültig ab ${item.id}`} value={validity.from} disabled={busy || externalChange}
            onChange={(event) => setValidityDrafts((current) => ({ ...current, [item.id]: { ...validity, from: event.target.value } }))} /></label>
            <label>Gültig bis (optional)<input type="date" aria-label={`Gültig bis ${item.id}`} value={validity.until} disabled={busy || externalChange}
              onChange={(event) => setValidityDrafts((current) => ({ ...current, [item.id]: { ...validity, until: event.target.value } }))} /></label></div>}
          {item.status === 'accepted' && item.validity_state === 'future' && <p className="notice">Diese Erinnerung gilt erst ab {item.valid_from} und kann vorher nicht für Antworten gewählt werden.</p>}
          {item.status === 'accepted' && item.validity_state === 'expired' && <p className="notice">Diese Erinnerung ist seit {item.valid_until} abgelaufen und kann nicht für Antworten gewählt werden.</p>}
          {externalChange && <p className="notice">Diese Erinnerung wurde an anderer Stelle geändert oder gelöscht. Prüfe sie unter „Dokumente“; hier wird nichts überschrieben.</p>}
          {item.status === 'accepted' && !externalChange && <p>Aktuell gespeichert: {item.current_memory_text}</p>}
          {item.last_used && <p>Zuletzt für eine Chatantwort verwendet: {new Date(item.last_used.used_at).toLocaleString('de-DE')} · damalige Fassung: {item.last_used.text_snapshot} <button onClick={() => onChat(item.last_used!.chat_id, item.last_used!.assistant_message_id)}>Antwort öffnen</button></p>}
          {item.status === 'accepted' && !item.last_used && <small>Noch in keiner Chatantwort verwendet.</small>}
          <div className="item-actions">
            {item.status === 'pending' && <>
              <button className="primary" onClick={() => void decide(item, 'accept')} disabled={busy || draft.trim().length < 8}>Bestätigen</button>
              <button onClick={() => void decide(item, 'reject')} disabled={busy}>Verwerfen</button>
            </>}
            {item.status === 'accepted' && <>
              <button onClick={() => void decide(item, 'correct')} disabled={busy || externalChange || draft.trim().length < 8 || (draft === item.current_memory_text && validity.from === (item.valid_from || '') && validity.until === (item.valid_until || ''))}>Korrektur speichern</button>
              <button onClick={() => void decide(item, 'delete')} disabled={busy || externalChange}>Erinnerung löschen</button>
            </>}
          </div>
          {item.events.length > 0 && <details><summary>Entscheidungsverlauf ({item.events.length})</summary>
            <ul>{item.events.map((event, index) => <li key={index}>{new Date(event.created_at).toLocaleString('de-DE')} · {event.action}{event.text ? `: ${event.text}` : ''}{event.valid_from ? ` · ab ${event.valid_from}` : ''}{event.valid_until ? ` · bis ${event.valid_until}` : ''}</li>)}</ul>
          </details>}
        </div></div>;
      })}</div>
    </section>
    <section className="card"><h3>Mögliche Widersprüche</h3>
      <p>Ein lokales Modell vergleicht höchstens 20 bestätigte, aktuell gültige Erinnerungen. Es muss beide Textstellen wörtlich nennen. Du entscheidest selbst, ob eine Aussage veraltet ist oder nur eine Veränderung beschreibt.</p>
      <button onClick={() => void checkConflicts()} disabled={busy || !configured || provider !== 'ollama'}>Widersprüche prüfen</button>
      {conflicts?.map((result, index) => <div className="item-row" key={`${result.first_id}-${result.second_id}-${index}`}>
        <strong>Möglicher Widerspruch</strong>
        <p>„{result.first_quote}“ ↔ „{result.second_quote}“</p>
        <p>{result.reason}</p>
        <small>Quellen: {proposals.find((item) => item.id === result.first_id)?.chat_title || result.first_id} · {proposals.find((item) => item.id === result.second_id)?.chat_title || result.second_id}</small>
      </div>)}
    </section>
  </div>;
}
