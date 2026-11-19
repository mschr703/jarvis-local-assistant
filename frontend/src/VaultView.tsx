import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Credential = { id: string; provider: string; account: string;
  kind: string; version: number; usable: boolean; created_at: string; updated_at: string };
const kindLabels: Record<string, string> = {
  api_key: 'API-Schlüssel', access_token: 'Access-Token',
  refresh_token: 'Refresh-Token', password: 'Passwort',
};

export default function VaultView({ visible, onChanged }: {
  visible: boolean; onChanged: () => void;
}) {
  const [items, setItems] = useState<Credential[]>([]);
  const [provider, setProvider] = useState('');
  const [account, setAccount] = useState('');
  const [kind, setKind] = useState('api_key');
  const [secret, setSecret] = useState('');
  const [rotateId, setRotateId] = useState<string | null>(null);
  const [rotateSecret, setRotateSecret] = useState('');
  const [revokeId, setRevokeId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [notice, setNotice] = useState('');
  const request = useRef(0);

  const refresh = async () => {
    const current = ++request.current;
    const next = await api<Credential[]>('/vault/credentials');
    if (current === request.current) setItems(next);
  };
  useEffect(() => {
    if (visible) void refresh().catch((error: Error) => setNotice(error.message));
    else { setSecret(''); setRotateSecret(''); setRotateId(null); setRevokeId(null); }
    return () => { ++request.current; };
  }, [visible]);

  const withBusy = async (action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try { await action(); }
    catch (error) { setNotice((error as Error).message); await refresh().catch(() => undefined); }
    finally {
      setSecret(''); setRotateSecret('');
      busyRef.current = false; setBusy(false);
    }
  };
  const add = () => void withBusy(async () => {
    await api<Credential>('/vault/credentials', {
      method: 'POST', body: JSON.stringify({ provider: provider.trim(), account: account.trim(), kind, secret }),
    });
    setNotice('Zugangsdaten geschützt gespeichert. Der Wert wird nicht angezeigt.');
    setProvider(''); setAccount(''); await refresh(); onChanged();
  });
  const rotate = (item: Credential) => void withBusy(async () => {
    await api<Credential>('/vault/credentials/' + item.id + '/rotate', {
      method: 'POST', body: JSON.stringify({ expected_version: item.version, secret: rotateSecret }),
    });
    setRotateId(null); setNotice('Zugangsdaten ersetzt. Der neue Wert wird nicht angezeigt.');
    await refresh(); onChanged();
  });
  const revoke = (item: Credential) => void withBusy(async () => {
    await api<{ revoked: boolean }>('/vault/credentials/' + item.id + '?expected_version=' + item.version,
      { method: 'DELETE' });
    setRevokeId(null); setNotice('Zugangsdaten entzogen.');
    await refresh(); onChanged();
  });

  return <div className="section-stack"><h2>Zugänge</h2>
    <p className="intro">Zugangsdaten werden für diesen Windows-Benutzer geschützt. Ein OpenRouter-Schlüssel mit Anbieter <code>openrouter</code>, Kontokennung <code>default</code> und Typ API-Schlüssel kann für neue Cloud-Anfragen verwendet werden. Jarvis bleibt bei Ollama, bis der Provider bewusst umgestellt wird.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Zugangsdaten hinzufügen</h3>
      <p>Wert wird nach dem Speichern nicht mehr angezeigt. Bei Gerätewechsel oder neuem Windows-Benutzer erneut eingeben.</p>
      <button disabled={busy} onClick={() => { setProvider('openrouter'); setAccount('default'); setKind('api_key'); }}>OpenRouter-Eintrag vorbereiten</button>
      <div className="work-form">
        <label>Anbieterkennung<input aria-label="Anbieterkennung" value={provider} disabled={busy}
          placeholder="z. B. openrouter" onChange={(event) => setProvider(event.target.value)} /></label>
        <label>Kontokennung<input aria-label="Kontokennung" value={account} disabled={busy}
          placeholder="z. B. Name oder E-Mail" onChange={(event) => setAccount(event.target.value)} /></label>
        <label>Geheimnistyp<select aria-label="Geheimnistyp" value={kind} disabled={busy}
          onChange={(event) => setKind(event.target.value)}>{Object.entries(kindLabels).map(([value, label]) =>
            <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Geheimnis<input aria-label="Geheimnis" type="password" autoComplete="off" spellCheck={false}
          value={secret} disabled={busy} onChange={(event) => setSecret(event.target.value)} /></label>
        <div className="item-actions"><button disabled={busy || !provider.trim() || !account.trim() || !secret}
          onClick={add}>Geschützt speichern</button></div>
      </div>
    </section>
    <section className="card"><h3>Gespeicherte Zugänge</h3>
      {items.length === 0 ? <p>Keine Zugangsdaten gespeichert.</p> : <div className="item-list">{items.map((item) =>
        <div className="item-row" key={item.id}><div><strong>{item.provider} · {item.account}</strong>
          <small>{kindLabels[item.kind] || item.kind} · Version {item.version} · {item.usable ? 'Für diesen Windows-Benutzer lesbar' : 'Nicht lesbar: ersetzen oder entziehen'}</small>
          <small>Zuletzt geändert: {new Date(item.updated_at).toLocaleString('de-DE')}</small>
          {rotateId === item.id && <div className="work-form"><label>Neues Geheimnis<input
            aria-label={'Neues Geheimnis für ' + item.account} type="password" autoComplete="off"
            spellCheck={false} value={rotateSecret} disabled={busy}
            onChange={(event) => setRotateSecret(event.target.value)} /></label>
            <div className="item-actions"><button disabled={busy || !rotateSecret}
              onClick={() => rotate(item)}>Neuen Wert speichern</button>
              <button disabled={busy} onClick={() => { setRotateId(null); setRotateSecret(''); }}>Abbrechen</button></div></div>}
          {revokeId === item.id && <p>Dieser Zugang wird aus Jarvis entfernt. Ein beim Anbieter gültiger Token wird dadurch nicht beim Anbieter widerrufen.</p>}
        </div><div className="item-actions">
          {rotateId !== item.id && <button disabled={busy} onClick={() => { setRotateId(item.id); setRotateSecret(''); setRevokeId(null); }}>Ersetzen</button>}
          {revokeId !== item.id ? <button disabled={busy} onClick={() => { setRevokeId(item.id); setRotateId(null); setRotateSecret(''); }}>Entziehen…</button>
            : <><button disabled={busy} onClick={() => revoke(item)}>Lokal entziehen</button>
              <button disabled={busy} onClick={() => setRevokeId(null)}>Abbrechen</button></>}
        </div></div>)}</div>}
    </section>
  </div>;
}
