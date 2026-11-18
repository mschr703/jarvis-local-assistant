import { useEffect, useState, type FormEvent } from 'react';
import { api } from './api';
import type { Source } from './DocumentView';

type Kind = 'document' | 'memory' | 'project';
type SourceKind = 'all' | Kind;
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
type SemanticStatus = { model: string; status: 'ready' | 'model_missing' | 'service_unavailable' | 'invalid_response' | 'invalid_url';
  service_available: boolean; ready: boolean; message: string };
type CacheStatus = { entries: number; bytes: number; ram_entries: number; available: boolean };
const names: Record<Kind, string> = {
  document: 'Dokument', memory: 'Erinnerung', project: 'Arbeitsstand',
};

function draftSource(hit: Hit | SemanticHit): Source | null {
  if (hit.kind !== 'document' || !hit.document_id || typeof hit.passage_id !== 'number'
      || hit.passage_id <= 0 || !hit.sha256 || !hit.policy
      || !['current', 'updated'].includes(hit.source_status || '')) return null;
  return { passage_id: hit.passage_id, document_id: hit.document_id,
    path: hit.source, page: hit.page ?? null, text: hit.excerpt,
    policy: hit.policy, sha256: hit.sha256, source_status: 'current' };
}

export default function UnifiedSearchView({ onDraft, onOpenSource }: {
  onDraft: (sources: Source[]) => void; onOpenSource: (kind: Kind, id: string) => void }) {
  const [query, setQuery] = useState('');
  const [sourceKind, setSourceKind] = useState<SourceKind>('all');
  const [selectedDraftSources, setSelectedDraftSources] = useState<Source[]>([]);
  const [result, setResult] = useState<SearchResult | null>(null);
  const [semantic, setSemantic] = useState<SemanticResult | null>(null);
  const [modelStatus, setModelStatus] = useState<SemanticStatus | null>(null);
  const [statusChecking, setStatusChecking] = useState(false);
  const [cacheStatus, setCacheStatus] = useState<CacheStatus | null>(null);
  const [cacheBusy, setCacheBusy] = useState(false);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState('');
  const [documentId, setDocumentId] = useState('');
  const [preferredId, setPreferredId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { void api<Folder[]>('/folders').then(setFolders).catch(() => setFolders([])); }, []);
  useEffect(() => { void api<Document[]>('/documents').then(setDocuments).catch(() => setDocuments([])); }, []);
  const refreshModelStatus = async () => {
    setStatusChecking(true);
    try { setModelStatus(await api<SemanticStatus>('/search/semantic/status')); }
    catch { setModelStatus({ model: 'embeddinggemma:300m', status: 'service_unavailable',
      service_available: false, ready: false, message: 'Modellstatus nicht erreichbar. Backend und Ollama prüfen.' }); }
    finally { setStatusChecking(false); }
  };
  useEffect(() => { void refreshModelStatus(); }, []);
  const refreshCache = async () => {
    setCacheBusy(true);
    try { setCacheStatus(await api<CacheStatus>('/search/semantic/cache')); }
    catch { setCacheStatus({ entries: 0, bytes: 0, ram_entries: 0, available: false }); }
    finally { setCacheBusy(false); }
  };
  useEffect(() => { void refreshCache(); }, []);
  const clearCache = async () => {
    if (busy || cacheBusy) return;
    setCacheBusy(true); setError('');
    try {
      setCacheStatus(await api<CacheStatus>('/search/semantic/cache', { method: 'DELETE' }));
    } catch (cause) { setError((cause as Error).message); }
    finally { setCacheBusy(false); }
  };
  const load = async (value: string) => {
    const params = new URLSearchParams({ q: value, kind: sourceKind });
    const [found, available] = await Promise.all([
      api<SearchResult>(`/search/unified?${params}`),
      sourceKind === 'all' || sourceKind === 'document' ?
        api<Document[]>('/documents') : Promise.resolve([] as Document[]),
    ]);
    setResult(found); setDocuments(available);
  };
  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || cacheBusy) return;
    setBusy(true); setError(''); setResult(null); setSemantic(null); setSelectedDraftSources([]);
    try { await load(query.trim()); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const runSemantic = async () => {
    if (busy || cacheBusy || query.trim().length < 2) return;
    setBusy(true); setError(''); setResult(null); setSemantic(null); setSelectedDraftSources([]);
    try {
      const params = new URLSearchParams({ q: query.trim(), kind: sourceKind });
      if (folderId) params.set('folder_id', folderId);
      if (documentId) params.set('document_id', documentId);
      setSemantic(await api<SemanticResult>(`/search/semantic?${params}`));
      void refreshCache();
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
      setSelectedDraftSources([]);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const draftActions = (hit: Hit | SemanticHit) => {
    const source = draftSource(hit);
    if (!source) return null;
    const selected = selectedDraftSources.some((item) => item.passage_id === source.passage_id);
    return <div className="item-actions"><label><input type="checkbox"
      aria-label={`Fundstelle ${source.passage_id} für Entwurf auswählen`}
      checked={selected} disabled={!selected && selectedDraftSources.length >= 5}
      onChange={() => setSelectedDraftSources((current) => current.some(
        (item) => item.passage_id === source.passage_id) ? current.filter(
          (item) => item.passage_id !== source.passage_id) : current.length < 5 ?
          [...current, source] : current)} />Für Entwurf auswählen</label>
      <button type="button" onClick={() => onDraft([source])}>
        Mit dieser Fundstelle Entwurf starten</button></div>;
  };
  const candidates = documents.filter((document) => document.status === 'indexed'
    && !document.excluded && !result?.preferences.some((item) => item.document_id === document.id)
    && !result?.stale_sources.some((item) => item.document_id === document.id));
  const deepDocuments = documents.filter((document) => document.status === 'indexed'
    && !document.excluded && (!folderId || document.folder_id === folderId));
  return <div className="section-stack unified-search"><h2>Gemeinsame Suche</h2>
    <p className="intro">Die Stichwortsuche durchsucht lokal indexierte Dateien, Erinnerungen und Arbeitsstände ohne Modell. Die optionale sinngemäße Suche nutzt ein lokales Ollama-Modell für dieselben Quellarten. Nach Dateiänderungen den Ordner unter „Dokumente“ neu indexieren.</p>
    <section className="card"><form onSubmit={(event) => void run(event)}>
      <label>Quelltyp
        <select aria-label="Quelltyp der gemeinsamen Suche" value={sourceKind} disabled={busy || cacheBusy}
          onChange={(event) => { const next = event.target.value as SourceKind;
            setSourceKind(next); setResult(null); setSemantic(null); setSelectedDraftSources([]);
            if (next === 'memory' || next === 'project') { setFolderId(''); setDocumentId(''); }
          }}>
          <option value="all">Alle Quellen</option>
          <option value="document">Dokumente</option>
          <option value="memory">Erinnerungen</option>
          <option value="project">Arbeitsstände</option>
        </select></label>
      <label style={{ display: 'grid', gap: 7, minWidth: 0, maxWidth: '100%' }}>Suchbegriff oder Stichwortfrage<input aria-label="Gemeinsame Suche" value={query}
        style={{ width: '100%', minWidth: 0, maxWidth: '100%', background: '#101823',
          border: '1px solid #3b4b60', borderRadius: 8, padding: 9, color: '#e8ebef' }}
        maxLength={160} onChange={(event) => setQuery(event.target.value)}
        placeholder="Projektname, Thema oder Dokumenttitel" /></label>
      <button className="primary" type="submit" disabled={busy || cacheBusy || query.trim().length < 2}>
        {busy ? 'Suche…' : 'Alle lokalen Quellen durchsuchen'}</button>
      <label>Dokumentordner für sinngemäße Suche
        <select aria-label="Dokumentordner für sinngemäße Suche" value={folderId}
          disabled={busy || sourceKind === 'memory' || sourceKind === 'project'}
          onChange={(event) => { setFolderId(event.target.value); setDocumentId(''); }}>
          <option value="">Alle freigegebenen Ordner</option>
          {folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.path}</option>)}
        </select></label>
      <label>Einzelnes Dokument tief durchsuchen
        <select aria-label="Einzelnes Dokument tief durchsuchen" value={documentId}
          disabled={busy || sourceKind === 'memory' || sourceKind === 'project'}
          onChange={(event) => setDocumentId(event.target.value)}>
          <option value="">Gemeinsame Suche, erste drei Fundstellen je Datei</option>
          {deepDocuments.map((document) => <option key={document.id} value={document.id}>{document.path}</option>)}
        </select></label>
      <button type="button" disabled={busy || cacheBusy || query.trim().length < 2}
        onClick={() => void runSemantic()}>{busy ? 'Bitte warten…' : 'Sinngemäß lokal suchen'}</button>
      <div role="status"><small>{statusChecking ? 'Prüfe lokales Suchmodell…' :
        modelStatus?.message || 'Modellstatus noch nicht geprüft.'}</small>
        <button type="button" disabled={statusChecking} onClick={() => void refreshModelStatus()}>
          {statusChecking ? 'Prüfe…' : 'Modellstatus prüfen'}</button></div>
      <div role="status"><small>{cacheStatus ? cacheStatus.available ?
        `Suchcache: ${cacheStatus.entries} gespeicherte Vektoren (${Math.ceil(cacheStatus.bytes / 1024)} KiB), ${cacheStatus.ram_entries} im Arbeitsspeicher.` :
        'Suchcache nicht lesbar. Datei im Datenordner nach dem Stoppen von Jarvis prüfen.' :
        'Suchcache-Stand noch nicht geprüft.'}</small>
        <button type="button" disabled={cacheBusy || busy} onClick={() => void refreshCache()}>
          {cacheBusy ? 'Prüfe…' : 'Cache-Stand prüfen'}</button>
        <button type="button" disabled={cacheBusy || busy} onClick={() => void clearCache()}>
          Suchcache leeren</button></div>
      <small>Leert nur berechnete Suchvektoren. Dokumente, Erinnerungen und Suchindex bleiben erhalten; die nächste sinngemäße Suche berechnet die Vektoren erneut.</small>
      <small>Benötigt lokal <code>embeddinggemma:300m</code>. {documentId ?
        'Im gewählten Dokument werden bis zu 100 Fundstellen und die fünf besten Treffer geprüft.' :
        sourceKind === 'memory' ? 'Es werden höchstens 30 gespeicherte Erinnerungen geprüft.' :
        sourceKind === 'project' ? 'Es werden höchstens 20 gespeicherte Arbeitsstände geprüft.' :
        sourceKind === 'document' ? 'Es werden höchstens 100 Fundstellen aus 30 Dateien geprüft.' :
        'Gemeinsam werden höchstens 100 Fundstellen aus 30 Dateien, 30 Erinnerungen und 20 Arbeitsstände geprüft. Für diese Frage bewusst vorgemerkte Dokumente werden innerhalb dieser Grenze zuerst geprüft.'} Die Ähnlichkeit ist kein Wahrheitsbeweis.</small>
    </form></section>
    {error && <p className="notice error" role="alert">{error}</p>}
    {selectedDraftSources.length > 0 && <section className="card" aria-label="Fundstellen für Entwurf">
      <p>{selectedDraftSources.length} von höchstens fünf Fundstellen für den Entwurf ausgewählt.</p>
      <div className="item-actions"><button type="button" className="primary"
        onClick={() => onDraft(selectedDraftSources)}>
        Mit {selectedDraftSources.length} Fundstellen Entwurf starten</button>
        <button type="button" onClick={() => setSelectedDraftSources([])}>Auswahl entfernen</button></div>
    </section>}
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
        sourceKind === 'memory' ? 'Es wurden höchstens die 30 neuesten Erinnerungen geprüft und höchstens zehn Treffer angezeigt.' :
        sourceKind === 'project' ? 'Es wurden höchstens die 20 neuesten Arbeitsstände geprüft und höchstens zehn Treffer angezeigt.' :
        sourceKind === 'document' ? 'Es wurden höchstens 30 Dateien und 100 Fundstellen geprüft, je Datei die ersten drei; höchstens zehn Treffer erscheinen.' :
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
          <button type="button" onClick={() => onOpenSource(hit.kind, hit.id)}>Quelle öffnen</button>
          {draftActions(hit)}
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
      {(sourceKind === 'all' || sourceKind === 'document') && <div className="item-list"><h4>Zusätzliche Dokumentquellen für diese Frage</h4>
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
      </div>}
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
          <button type="button" onClick={() => onOpenSource(hit.kind, hit.id)}>Quelle öffnen</button>
          {draftActions(hit)}
        </div>
      </article>)}</div>
    </section>}
  </div>;
}
