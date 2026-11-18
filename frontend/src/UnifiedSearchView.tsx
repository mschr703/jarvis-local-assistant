import { useState, type FormEvent } from 'react';
import { api } from './api';

type Kind = 'document' | 'memory' | 'project';
type Hit = { kind: Kind; id: string; title: string; excerpt: string; source: string;
  score: number; policy?: string; document_id?: string; passage_id?: number | null;
  page?: number | null; sha256?: string; version?: number; preferred?: boolean;
  source_status?: 'current' | 'updated' };
type Preference = { document_id: string; path: string; saved_sha256: string;
  current_sha256: string | null; status: 'current' | 'updated' | 'needs_reindex' | 'unavailable' };
type Document = { id: string; path: string; status: string; excluded: number };
type SearchResult = { query: string; results: Hit[]; limited: boolean;
  counts: Record<Kind, number>; preferences: Preference[] };
const names: Record<Kind, string> = {
  document: 'Dokument', memory: 'Erinnerung', project: 'Arbeitsstand',
};

export default function UnifiedSearchView() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [preferredId, setPreferredId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const load = async (value: string) => {
    const [found, available] = await Promise.all([
      api<SearchResult>(`/search/unified?q=${encodeURIComponent(value)}`),
      api<Document[]>('/documents'),
    ]);
    setResult(found); setDocuments(available);
  };
  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(''); setResult(null);
    try { await load(query.trim()); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const changePreference = async (documentId: string, method: 'PUT' | 'DELETE') => {
    if (!result || busy) return;
    setBusy(true); setError('');
    try {
      await api('/search/unified/preferences', { method,
        body: JSON.stringify({ query: result.query, document_id: documentId }) });
      await load(result.query);
      setPreferredId('');
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const candidates = documents.filter((document) => document.status === 'indexed'
    && !document.excluded && !result?.preferences.some((item) => item.document_id === document.id));
  return <div className="section-stack unified-search"><h2>Gemeinsame Suche</h2>
    <p className="intro">Suche lokal in indexierten Dateien, bewusst gespeicherten Erinnerungen und Arbeitsständen. Treffer zeigen den gespeicherten Quellenstand; nach Dateiänderungen den Ordner unter „Dokumente“ neu indexieren. Die Suche ruft kein Modell und keine Cloud auf.</p>
    <section className="card"><form onSubmit={(event) => void run(event)}>
      <label style={{ display: 'grid', gap: 7, minWidth: 0, maxWidth: '100%' }}>Suchbegriff oder Stichwortfrage<input aria-label="Gemeinsame Suche" value={query}
        style={{ width: '100%', minWidth: 0, maxWidth: '100%', background: '#101823',
          border: '1px solid #3b4b60', borderRadius: 8, padding: 9, color: '#e8ebef' }}
        maxLength={160} onChange={(event) => setQuery(event.target.value)}
        placeholder="Projektname, Thema oder Dokumenttitel" /></label>
      <button className="primary" type="submit" disabled={busy || query.trim().length < 2}>
        {busy ? 'Suche…' : 'Alle lokalen Quellen durchsuchen'}</button>
    </form></section>
    {error && <p className="notice error" role="alert">{error}</p>}
    {result && <section className="card" aria-label="Suchergebnisse">
      <h3>Treffer für „{result.query}“</h3>
      <p>{result.counts.document} Dokumente · {result.counts.memory} Erinnerungen · {result.counts.project} Arbeitsstände</p>
      <div className="item-list"><h4>Zusätzliche Dokumentquellen für diese Frage</h4>
        <p>Du wählst die Quelle selbst. Eine Vormerkung beweist keine Aussage; Jarvis zeigt den aktuellen Indexstand und den gesicherten Hash.</p>
        {result.preferences.map((item) => <div className="item-row" key={item.document_id}>
          <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}><strong>{item.path}</strong>
            <small>{item.status === 'current' ? 'Aktueller gespeicherter Stand' :
              item.status === 'updated' ? 'Seit der Vormerkung neu indexiert: Beleg prüfen und bei Bedarf erneut vormerken' :
              item.status === 'needs_reindex' ? 'Original und Index weichen ab: zuerst neu indexieren' :
              'Quelle entfernt, ausgeschlossen oder nicht mehr verfügbar'} · gesichert SHA-256 {item.saved_sha256}
              {item.current_sha256 && item.current_sha256 !== item.saved_sha256 ? ` · Index SHA-256 ${item.current_sha256}` : ''}</small>
            <button type="button" disabled={busy} onClick={() => void changePreference(item.document_id, 'DELETE')}>Vormerkung entfernen</button></div>
        </div>)}
        {result.preferences.length === 0 && <p>Noch keine zusätzliche Quelle vorgemerkt.</p>}
        <label>Weiteres indexiertes Dokument berücksichtigen
          <select aria-label="Zusätzliches Dokument" value={preferredId} disabled={busy || result.preferences.length >= 5}
            onChange={(event) => setPreferredId(event.target.value)}>
            <option value="">Dokument wählen</option>
            {candidates.map((item) => <option key={item.id} value={item.id}>{item.path}</option>)}
          </select></label>
        <button type="button" disabled={busy || !preferredId || result.preferences.length >= 5}
          onClick={() => void changePreference(preferredId, 'PUT')}>Dieses Dokument berücksichtigen</button>
      </div>
      {result.limited && <p className="notice">Die Trefferliste ist begrenzt. Präzisiere die Suche für weitere Ergebnisse.</p>}
      {!result.results.length && <p>Keine passenden gespeicherten Quellen gefunden.</p>}
      <div className="item-list">{result.results.map((hit) => <article className="item-row" key={`${hit.kind}:${hit.id}`}>
        <div><strong>{names[hit.kind]} · {hit.title}{hit.preferred ? ' · bewusst vorgemerkt' : ''}</strong>
          <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{hit.excerpt || '(kein Auszug)'}</p>
          <small style={{ overflowWrap: 'anywhere' }}>Quelle: {hit.source}
            {hit.page != null ? ` · Seite ${hit.page}` : ''}
            {hit.policy ? ` · ${hit.policy === 'local_only' ? 'nur lokal' : 'Cloud erlaubt'}` : ''}
            {hit.sha256 ? ` · SHA-256 ${hit.sha256}` : ''}
            {hit.passage_id != null ? ` · Fundstelle ${hit.passage_id}` : ''}
            {hit.kind === 'project' ? ` · ID ${hit.id}` : ''}
          </small>
        </div>
      </article>)}</div>
    </section>}
  </div>;
}
