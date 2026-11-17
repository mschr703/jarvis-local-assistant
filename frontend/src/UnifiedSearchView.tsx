import { useState, type FormEvent } from 'react';
import { api } from './api';

type Kind = 'document' | 'memory' | 'project';
type Hit = { kind: Kind; id: string; title: string; excerpt: string; source: string;
  score: number; policy?: string; document_id?: string; passage_id?: number | null;
  page?: number | null; sha256?: string; version?: number };
type SearchResult = { query: string; results: Hit[]; limited: boolean;
  counts: Record<Kind, number> };
const names: Record<Kind, string> = {
  document: 'Dokument', memory: 'Erinnerung', project: 'Arbeitsstand',
};

export default function UnifiedSearchView() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(''); setResult(null);
    try { setResult(await api<SearchResult>(`/search/unified?q=${encodeURIComponent(query.trim())}`)); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
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
      {result.limited && <p className="notice">Die Trefferliste ist begrenzt. Präzisiere die Suche für weitere Ergebnisse.</p>}
      {!result.results.length && <p>Keine passenden gespeicherten Quellen gefunden.</p>}
      <div className="item-list">{result.results.map((hit) => <article className="item-row" key={`${hit.kind}:${hit.id}`}>
        <div><strong>{names[hit.kind]} · {hit.title}</strong>
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
