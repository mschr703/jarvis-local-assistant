import { useEffect, useState, type FormEvent } from 'react';
import { api } from './api';

type Kind = 'document' | 'memory' | 'project';
type Hit = { kind: Kind; id: string; title: string; excerpt: string; source: string;
  score: number; policy?: string; document_id?: string; passage_id?: number | null;
  page?: number | null; sha256?: string; version?: number; preferred?: boolean;
  source_status?: 'current' | 'updated' };
type Preference = { document_id: string; path: string; saved_sha256: string;
  current_sha256: string | null; status: 'current' | 'updated' | 'needs_reindex' | 'unavailable' };
type Document = { id: string; path: string; folder_id: string; status: string; excluded: number };
type Folder = { id: string; path: string };
type SearchResult = { query: string; results: Hit[]; limited: boolean;
  counts: Record<Kind, number>; preferences: Preference[];
  stale_sources: { document_id: string; path: string; index_sha256: string; status: 'needs_reindex' }[] };
type SemanticHit = Omit<Hit, 'score'> & { similarity: number; preference_status?: 'current' | 'updated' | null };
type SemanticResult = { query: string; results: SemanticHit[]; limited: boolean;
  checked_passages: number; checked_memories: number; checked_projects: number;
  counts: Record<Kind, number>; model: string;
  document_scope: { id: string; title: string } | null;
  preferences: { document_id: string; path: string; status: Preference['status'] }[];
  changed_sources: { kind: 'memory' | 'project'; id: string }[];
  stale_sources: SearchResult['stale_sources'] };
const names: Record<Kind, string> = {
  document: 'Dokument', memory: 'Erinnerung', project: 'Arbeitsstand',
};

export default function UnifiedSearchView() {
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<SearchResult | null>(null);
  const [semantic, setSemantic] = useState<SemanticResult | null>(null);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState('');
  const [documentId, setDocumentId] = useState('');
  const [preferredId, setPreferredId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { void api<Folder[]>('/folders').then(setFolders).catch(() => setFolders([])); }, []);
  useEffect(() => { void api<Document[]>('/documents').then(setDocuments).catch(() => setDocuments([])); }, []);
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
    setBusy(true); setError(''); setResult(null); setSemantic(null);
    try { await load(query.trim()); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const runSemantic = async () => {
    if (busy || query.trim().length < 2) return;
    setBusy(true); setError(''); setResult(null); setSemantic(null);
    try {
      const params = new URLSearchParams({ q: query.trim() });
      if (folderId) params.set('folder_id', folderId);
      if (documentId) params.set('document_id', documentId);
      setSemantic(await api<SemanticResult>(`/search/semantic?${params}`));
    }
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
    && !document.excluded && !result?.preferences.some((item) => item.document_id === document.id)
    && !result?.stale_sources.some((item) => item.document_id === document.id));
  const deepDocuments = documents.filter((document) => document.status === 'indexed'
    && !document.excluded && (!folderId || document.folder_id === folderId));
  return <div className="section-stack unified-search"><h2>Gemeinsame Suche</h2>
    <p className="intro">Die Stichwortsuche durchsucht lokal indexierte Dateien, Erinnerungen und Arbeitsstände ohne Modell. Die optionale sinngemäße Suche nutzt ein lokales Ollama-Modell für dieselben Quellarten. Nach Dateiänderungen den Ordner unter „Dokumente“ neu indexieren.</p>
    <section className="card"><form onSubmit={(event) => void run(event)}>
      <label style={{ display: 'grid', gap: 7, minWidth: 0, maxWidth: '100%' }}>Suchbegriff oder Stichwortfrage<input aria-label="Gemeinsame Suche" value={query}
        style={{ width: '100%', minWidth: 0, maxWidth: '100%', background: '#101823',
          border: '1px solid #3b4b60', borderRadius: 8, padding: 9, color: '#e8ebef' }}
        maxLength={160} onChange={(event) => setQuery(event.target.value)}
        placeholder="Projektname, Thema oder Dokumenttitel" /></label>
      <button className="primary" type="submit" disabled={busy || query.trim().length < 2}>
        {busy ? 'Suche…' : 'Alle lokalen Quellen durchsuchen'}</button>
      <label>Dokumentordner für sinngemäße Suche
        <select aria-label="Dokumentordner für sinngemäße Suche" value={folderId} disabled={busy}
          onChange={(event) => { setFolderId(event.target.value); setDocumentId(''); }}>
          <option value="">Alle freigegebenen Ordner</option>
          {folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.path}</option>)}
        </select></label>
      <label>Einzelnes Dokument tief durchsuchen
        <select aria-label="Einzelnes Dokument tief durchsuchen" value={documentId} disabled={busy}
          onChange={(event) => setDocumentId(event.target.value)}>
          <option value="">Gemeinsame Suche, erste drei Fundstellen je Datei</option>
          {deepDocuments.map((document) => <option key={document.id} value={document.id}>{document.path}</option>)}
        </select></label>
      <button type="button" disabled={busy || query.trim().length < 2}
        onClick={() => void runSemantic()}>{busy ? 'Bitte warten…' : 'Sinngemäß lokal suchen'}</button>
      <small>Benötigt lokal <code>embeddinggemma:300m</code>. {documentId ?
        'Im gewählten Dokument werden bis zu 100 Fundstellen und die fünf besten Treffer geprüft.' :
        'Gemeinsam werden höchstens 100 Fundstellen aus 30 Dateien, 30 Erinnerungen und 20 Arbeitsstände geprüft. Für diese Frage bewusst vorgemerkte Dokumente werden innerhalb dieser Grenze zuerst geprüft.'} Die Ähnlichkeit ist kein Wahrheitsbeweis.</small>
    </form></section>
    {error && <p className="notice error" role="alert">{error}</p>}
    {semantic && <section className="card" aria-label="Sinngemäße Suchergebnisse">
      <h3>Sinngemäße Treffer für „{semantic.query}“</h3>
      <p>{semantic.document_scope ?
        `${semantic.counts.document} Fundstellen aus ${semantic.document_scope.title}` :
        `${semantic.counts.document} Dokumente · ${semantic.counts.memory} Erinnerungen · ${semantic.counts.project} Arbeitsstände`}</p>
      <small>Geprüft: {semantic.checked_passages} Dokumentfundstellen, {semantic.checked_memories} Erinnerungen, {semantic.checked_projects} Arbeitsstände · Modell {semantic.model}</small>
      {semantic.preferences.some((item) => item.status === 'needs_reindex' || item.status === 'unavailable') &&
        <div className="notice" role="status"><strong>Vorgemerkte Quellen nicht aktuell</strong>
          <p>Diese Dokumente wurden nicht an das Modell gesendet. Original und Freigabe prüfen; bei Änderungen neu indexieren.</p>
          <ul>{semantic.preferences.filter((item) => item.status === 'needs_reindex' || item.status === 'unavailable')
            .map((item) => <li key={item.document_id} style={{ overflowWrap: 'anywhere' }}>{item.path} · {item.status === 'needs_reindex' ? 'Index veraltet' : 'nicht verfügbar'}</li>)}</ul>
        </div>}
      {semantic.limited && <p className="notice">{semantic.document_scope ?
        'Die tiefe Suche prüft höchstens die ersten 100 Fundstellen dieser Datei und zeigt höchstens fünf Treffer.' :
        'Die Kandidaten oder Treffer sind begrenzt. Dokumente: höchstens 30 Dateien und 100 Fundstellen in Pfadreihenfolge, je Datei die ersten drei. Zusätzlich die 30 neuesten Erinnerungen und 20 neuesten Arbeitsstände; höchstens zehn Treffer je Quellart.'}</p>}
      {semantic.stale_sources.length > 0 && <div className="notice" role="status">
        <strong>Originaldateien und Index weichen ab</strong><p>Die alten Auszüge werden nicht als Treffer gezeigt. Unter „Dokumente“ neu indexieren.</p>
        <ul>{semantic.stale_sources.map((item) => <li key={item.document_id} style={{ overflowWrap: 'anywhere' }}>{item.path}</li>)}</ul>
      </div>}
      {semantic.changed_sources.length > 0 && <p className="notice" role="status">
        {semantic.changed_sources.length} gespeicherte Erinnerungen oder Arbeitsstände wurden während der Suche geändert oder entfernt und daher nicht als Treffer angezeigt. Erneut suchen.
      </p>}
      {!semantic.results.length && <p>Keine aktuellen Quellen gefunden.</p>}
      <div className="item-list">{semantic.results.map((hit) => <article className="item-row" key={`${hit.kind}:${hit.id}:${hit.passage_id ?? ''}`}>
        <div><strong>{names[hit.kind]} · {hit.title}{hit.preferred ? ' · bewusst vorgemerkt' : ''}</strong>
          <p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{hit.excerpt}</p>
          <small style={{ overflowWrap: 'anywhere' }}>Quelle: {hit.source}
            {hit.page != null ? ` · Seite ${hit.page}` : ''}
            {hit.passage_id != null ? ` · Fundstelle ${hit.passage_id}` : ''}
            {hit.sha256 ? ` · SHA-256 ${hit.sha256}` : ''}
            {hit.policy ? ` · ${hit.policy === 'local_only' ? 'nur lokal' : 'Cloud erlaubt'}` : ''}
            {hit.preference_status === 'updated' ? ' · seit Vormerkung neu indexiert' : ''}
            {hit.kind === 'project' ? ` · ID ${hit.id}` : ''}
            {` · Ähnlichkeit ${hit.similarity.toFixed(2)}`}</small>
          {hit.kind === 'document' && hit.document_id && hit.source_status === 'current' &&
            <a href={`/api/documents/${encodeURIComponent(hit.document_id)}/original`}
              download={hit.title}>Geprüftes Original herunterladen</a>}
        </div>
      </article>)}</div>
    </section>}
    {result && <section className="card" aria-label="Suchergebnisse">
      <h3>Treffer für „{result.query}“</h3>
      <p>{result.counts.document} Dokumente · {result.counts.memory} Erinnerungen · {result.counts.project} Arbeitsstände</p>
      {result.stale_sources.length > 0 && <div className="notice" role="status">
        <strong>Originaldateien und Index weichen ab</strong><p>Unter „Dokumente“ den Ordner neu indexieren. Die alten Auszüge erscheinen nicht als aktuelle Treffer.</p>
        <ul>{result.stale_sources.map((item) => <li key={item.document_id} style={{ overflowWrap: 'anywhere' }}>
          {item.path} · bisheriger Index SHA-256 {item.index_sha256}</li>)}</ul>
      </div>}
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
          {hit.kind === 'document' && hit.document_id && hit.source_status &&
            <a href={`/api/documents/${encodeURIComponent(hit.document_id)}/original`}
              download={hit.title}>Geprüftes Original herunterladen</a>}
        </div>
      </article>)}</div>
    </section>}
  </div>;
}
