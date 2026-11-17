import { useEffect, useRef, useState } from 'react';
import { api } from './api';

export type Skill = {
  id: string; name: string; active_version: number | null; revision: number;
  versions: { version: number; content: string; content_hash: string; created_at: string }[];
  activations: { from_version: number | null; to_version: number | null; changed_at: string }[];
};

export default function SkillView({ visible }: { visible: boolean }) {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [name, setName] = useState('');
  const [content, setContent] = useState('');
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const action = useRef(false);
  const refresh = async () => setSkills(await api<Skill[]>('/skills'));
  useEffect(() => { if (visible) void refresh().catch((error: Error) => setNotice(error.message)); }, [visible]);
  const change = async (path: string, method: 'POST' | 'PUT', body: object) => {
    if (action.current) return;
    action.current = true; setBusy(true); setNotice('');
    try {
      await api<Skill>(path, { method, body: JSON.stringify(body) });
      await refresh();
      setName(''); setContent(''); setEdits({});
      setNotice('Skill-Fassung gespeichert. Frühere Chatantworten behalten ihren Versionsbeleg.');
    } catch (error) {
      setNotice((error as Error).message);
      await refresh().catch(() => undefined);
    } finally { action.current = false; setBusy(false); }
  };
  return <div className="section-stack"><h2>Chat-Skills</h2>
    <p className="intro">Benannte Zusatzanweisungen für Chatantworten. Wähle einen aktiven Skill bei jeder Nachricht bewusst im Chat aus. Er ändert keine Werkzeugrechte oder festen Systemregeln. Bei OpenRouter wird sein Text mit der freigegebenen Anfrage übertragen.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Neuen Skill anlegen</h3><div className="memory-form">
      <input aria-label="Skill-Name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="z. B. Kurze Projektantwort" />
      <textarea aria-label="Skill-Inhalt" maxLength={2000} value={content} onChange={(event) => setContent(event.target.value)} placeholder="z. B. Nenne zuerst den nächsten konkreten Schritt." />
      <button className="primary" disabled={busy || !name.trim() || !content.trim()} onClick={() => void change('/skills', 'POST', { name, content })}>Skill anlegen</button>
    </div></section>
    {skills.map((skill) => <section className="card skill-card" key={skill.id}>
      <h3>{skill.name}</h3><p>{skill.active_version == null ? 'Inaktiv' : `Version ${skill.active_version} aktiv`} · Revision {skill.revision}</p>
      <p>{skill.versions.find((version) => version.version === skill.active_version)?.content || 'Dieser Skill wird nicht verwendet.'}</p>
      {skill.active_version != null && <button disabled={busy} onClick={() => void change(`/skills/${skill.id}/active`, 'PUT', { version: null, expected_revision: skill.revision })}>Deaktivieren</button>}
      <h4>Neue Fassung</h4><div className="memory-form"><textarea aria-label={`Neue Fassung für ${skill.name}`} maxLength={2000} value={edits[skill.id] || ''} onChange={(event) => setEdits((current) => ({ ...current, [skill.id]: event.target.value }))} />
        <button disabled={busy || !edits[skill.id]?.trim()} onClick={() => void change(`/skills/${skill.id}/versions`, 'POST', { content: edits[skill.id], expected_revision: skill.revision })}>Neue Fassung aktivieren</button></div>
      <h4>Fassungen und Rückkehr</h4><div className="item-list">{skill.versions.map((version) => <div className="item-row" key={version.version}><div><strong>Version {version.version}{version.version === skill.active_version ? ' · aktiv' : ''}</strong><small>{new Date(version.created_at).toLocaleString('de-DE')} · SHA-256 {version.content_hash.slice(0, 12)}…</small><p>{version.content}</p></div>{version.version !== skill.active_version && <button disabled={busy} onClick={() => void change(`/skills/${skill.id}/active`, 'PUT', { version: version.version, expected_revision: skill.revision })}>Diese Fassung aktivieren</button>}</div>)}</div>
      <h4>Aktivierungsverlauf</h4><div className="item-list">{skill.activations.map((entry, index) => <div className="item-row" key={index}><span>{entry.from_version == null ? 'Inaktiv' : `v${entry.from_version}`} → {entry.to_version == null ? 'Inaktiv' : `v${entry.to_version}`}</span><small>{new Date(entry.changed_at).toLocaleString('de-DE')}</small></div>)}</div>
    </section>)}
  </div>;
}
