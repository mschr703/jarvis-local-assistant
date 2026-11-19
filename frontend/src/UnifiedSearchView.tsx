import { useEffect, useState, type FormEvent } from 'react';
import { api, verifiedPdfPassageForLocalChat, verifiedPdfPassagesForLocalChat,
  verifiedSearchDocumentForLocalChat, type PdfChatPassage } from './api';
import type { Source } from './DocumentView';

type Kind = 'document' | 'memory' | 'project' | 'life_event';
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
  checked_events: number; counts: Record<Kind, number>; model: string;
  document_scope: { id: string; title: string } | null;
  preferences: { document_id: string; path: string; status: Preference['status'] }[];
  changed_sources: { kind: 'memory' | 'project' | 'life_event'; id: string }[];
  stale_sources: SearchResult['stale_sources'] };
type SemanticStatus = { model: string; status: 'ready' | 'model_missing' | 'service_unavailable' | 'invalid_response' | 'invalid_url';
  service_available: boolean; ready: boolean; message: string };
type CacheStatus = { entries: number; bytes: number; ram_entries: number; available: boolean };
type ChatDocument = { id: string; path: string; sha256: string };
const names: Record<Kind, string> = {
  document: 'Dokument', memory: 'Erinnerung', project: 'Arbeitsstand',
  life_event: 'Lebensereignis',
};

function draftSource(hit: Hit | SemanticHit): Source | null {
  if (hit.kind !== 'document' || !hit.document_id || typeof hit.passage_id !== 'number'
      || hit.passage_id <= 0 || !hit.sha256 || !hit.policy
      || !['current', 'updated'].includes(hit.source_status || '')) return null;
  return { passage_id: hit.passage_id, document_id: hit.document_id,
    path: hit.source, page: hit.page ?? null, text: hit.excerpt,
    policy: hit.policy, sha256: hit.sha256, source_status: 'current' };
}

export default function UnifiedSearchView({ onDraft, onOpenSource, onLocalChat, localChatReady }: {
  onDraft: (sources: Source[]) => void; onOpenSource: (kind: Kind, id: string) => void;
  onLocalChat: (files: File[]) => void; localChatReady: boolean }) {
  const [query, setQuery] = useState('');
  const [sourceKind, setSourceKind] = useState<SourceKind>('all');
  const [selectedDraftSources, setSelectedDraftSources] = useState<Source[]>([]);
  const [selectedChatDocuments, setSelectedChatDocuments] = useState<ChatDocument[]>([]);
  const [selectedChatPassages, setSelectedChatPassages] = useState<PdfChatPassage[]>([]);
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
  const [notice, setNotice] = useState('');
  const [preparingChat, setPreparingChat] = useState('');
  const [reindexingDocument, setReindexingDocument] = useState('');
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
    if (busy || cacheBusy || preparingChat) return;
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
    if (busy || cacheBusy || preparingChat) return;
    setBusy(true); setError(''); setNotice(''); setResult(null); setSemantic(null);
    setSelectedDraftSources([]); setSelectedChatDocuments([]); setSelectedChatPassages([]);
    try { await load(query.trim()); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const runSemantic = async () => {
    if (busy || cacheBusy || preparingChat || query.trim().length < 2) return;
    setBusy(true); setError(''); setNotice(''); setResult(null); setSemantic(null);
    setSelectedDraftSources([]); setSelectedChatDocuments([]); setSelectedChatPassages([]);
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
      setSelectedDraftSources([]); setSelectedChatDocuments([]); setSelectedChatPassages([]);
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
  const localChatAction = (hit: Hit | SemanticHit, firstForDocument: boolean) => {
    if (hit.kind !== 'document' || !hit.document_id || !hit.sha256
        || !['current', 'updated'].includes(hit.source_status || '')) return null;
    const key = `${hit.document_id}:${hit.sha256}`;
    const passageId = hit.passage_id;
    const passageKey = `${key}:${passageId}`;
    const canUsePassage = /\.pdf$/i.test(hit.source)
      && typeof passageId === 'number' && Number.isSafeInteger(passageId) && passageId > 0;
    const selectedPassage = selectedChatPassages.some((item) => item.passageId === passageId);
    const document = { id: hit.document_id, path: hit.source, sha256: hit.sha256 };
    const selected = selectedChatDocuments.some((item) => item.id === document.id);
    return <div className="item-actions">{firstForDocument && <label><input type="checkbox"
      aria-label={`Dokument ${hit.title} für lokalen Chat auswählen`}
      checked={selected} disabled={!localChatReady || !!preparingChat
        || (!selected && selectedChatDocuments.length >= 3)}
      onChange={() => { setSelectedChatPassages([]); setSelectedChatDocuments((current) => current.some(
        (item) => item.id === document.id) ? current.filter((item) => item.id !== document.id)
        : current.length < 3 ? [...current, document] : current); }} />Für lokalen Chat auswählen</label>}
    {canUsePassage && <label><input type="checkbox"
      aria-label={`PDF-Fundstelle ${passageId} für lokalen Chat auswählen`}
      checked={selectedPassage} disabled={!localChatReady || !!preparingChat
        || (!selectedPassage && selectedChatPassages.length >= 3)}
      onChange={() => { setSelectedChatDocuments([]); setSelectedChatPassages((current) =>
        current.some((item) => item.passageId === passageId)
          ? current.filter((item) => item.passageId !== passageId)
          : current.length < 3 ? [...current, { documentId: document.id,
            passageId: passageId as number, path: document.path, sha256: document.sha256 }]
            : current); }} />Fundstelle für lokalen Chat auswählen</label>}
    <button type="button" disabled={!localChatReady || !!preparingChat}
      title={!localChatReady ? 'Nur mit lokalem Ollama verfügbar' : undefined}
      onClick={async () => {
        setPreparingChat(key); setError('');
        try {
          const file = await verifiedSearchDocumentForLocalChat(document.id, document.path, document.sha256);
          setSelectedChatDocuments([]); setSelectedChatPassages([]); onLocalChat([file]);
        }
        catch (cause) { setError((cause as Error).message); }
        finally { setPreparingChat(''); }
      }}>{preparingChat === key ? 'Quelle wird geprüft…' : /\.pdf$/i.test(hit.source)
        ? 'PDF-Indextext im lokalen Chat verwenden' : 'Im lokalen Chat verwenden'}</button>
    {canUsePassage && <button type="button" disabled={!localChatReady || !!preparingChat}
      title={!localChatReady ? 'Nur mit lokalem Ollama verfügbar' : undefined}
      onClick={async () => {
        setPreparingChat(passageKey); setError('');
        try {
          const file = await verifiedPdfPassageForLocalChat(
            document.id, passageId, document.path, document.sha256);
          setSelectedChatDocuments([]); setSelectedChatPassages([]); onLocalChat([file]);
        } catch (cause) { setError((cause as Error).message); }
        finally { setPreparingChat(''); }
      }}>{preparingChat === passageKey ? 'Fundstelle wird geprüft…'
        : 'Diese PDF-Fundstelle im lokalen Chat verwenden'}</button>}</div>;
  };
  const prepareSelectedChat = async () => {
    if (!localChatReady || preparingChat || !selectedChatDocuments.length) return;
    setPreparingChat('selection'); setError('');
    try {
      const files: File[] = [];
      for (const item of selectedChatDocuments)
        files.push(await verifiedSearchDocumentForLocalChat(item.id, item.path, item.sha256));
      setSelectedChatDocuments([]); setSelectedChatPassages([]); onLocalChat(files);
    } catch (cause) { setError((cause as Error).message); }
    finally { setPreparingChat(''); }
  };
  const prepareSelectedPassages = async () => {
    if (!localChatReady || preparingChat || !selectedChatPassages.length) return;
    setPreparingChat('passage-selection'); setError('');
    try {
      const files = await verifiedPdfPassagesForLocalChat(selectedChatPassages);
      setSelectedChatPassages([]); setSelectedChatDocuments([]); onLocalChat(files);
    } catch (cause) { setError((cause as Error).message); }
    finally { setPreparingChat(''); }
  };
  const reindexFromSearch = async (id: string) => {
    if (busy || preparingChat) return;
    setBusy(true); setReindexingDocument(id); setError(''); setNotice('');
    try {
      const updated = await api<{ outcome: 'indexed' | 'unchanged' | 'removed' | 'ocr_pending' | 'error';
        document: { error?: string } | null }>(`/documents/${encodeURIComponent(id)}/index`,
        { method: 'POST' });
      setSelectedDraftSources([]); setSelectedChatDocuments([]); setSelectedChatPassages([]);
      if (semantic) setSemantic(null);
      if (result) setResult(null);
      setDocuments(await api<Document[]>('/documents'));
      if (updated.outcome === 'removed' && documentId === id) setDocumentId('');
      if (result) await load(result.query);
      setNotice(updated.outcome === 'indexed' ? `Datei neu indexiert. ${semantic ? 'Sinngemäße Suche bei Bedarf erneut starten.' : 'Aktuelle Treffer erneut prüfen.'}`
        : updated.outcome === 'removed' ? 'Original fehlt; der alte Suchindexeintrag wurde entfernt.'
          : updated.outcome === 'ocr_pending' ? 'Datei hat keine lesbare Textschicht; OCR steht aus.'
            : updated.outcome === 'error' ? `Datei konnte nicht indexiert werden: ${updated.document?.error || 'unbekannter Fehler'}.`
              : 'Datei war unverändert. Suchergebnis aktualisiert.');
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); setReindexingDocument(''); }
  };
  const candidates = documents.filter((document) => document.status === 'indexed'
    && !document.excluded && !result?.preferences.some((item) => item.document_id === document.id)
    && !result?.stale_sources.some((item) => item.document_id === document.id));
  const deepDocuments = documents.filter((document) => document.status === 'indexed'
    && !document.excluded && (!folderId || document.folder_id === folderId));
  return <div className="section-stack unified-search"><h2>Gemeinsame Suche</h2>
    <p className="intro">Die Stichwortsuche durchsucht lokal indexierte Dateien, Erinnerungen, Arbeitsstände und manuell bestätigte Lebensereignisse ohne Modell. Die optionale sinngemäße Suche nutzt Ollama derzeit für Dateien, Erinnerungen und Arbeitsstände. Nach Dateiänderungen kannst du veraltete Dateien direkt im Suchergebnis einzeln neu indexieren.</p>
    <section className="card"><form onSubmit={(event) => void run(event)}>
      <label>Quelltyp
        <select aria-label="Quelltyp der gemeinsamen Suche" value={sourceKind} disabled={busy || cacheBusy || !!preparingChat}
          onChange={(event) => { const next = event.target.value as SourceKind;
            setSourceKind(next); setResult(null); setSemantic(null);
            setSelectedDraftSources([]); setSelectedChatDocuments([]);
            setSelectedChatPassages([]); setNotice('');
            if (next === 'memory' || next === 'project' || next === 'life_event') { setFolderId(''); setDocumentId(''); }
          }}>
          <option value="all">Alle Quellen</option>
          <option value="document">Dokumente</option>
          <option value="memory">Erinnerungen</option>
          <option value="project">Arbeitsstände</option>
          <option value="life_event">Lebensereignisse</option>
        </select></label>
      <label style={{ display: 'grid', gap: 7, minWidth: 0, maxWidth: '100%' }}>Suchbegriff oder Stichwortfrage<input aria-label="Gemeinsame Suche" value={query}
        style={{ width: '100%', minWidth: 0, maxWidth: '100%', background: '#101823',
          border: '1px solid #3b4b60', borderRadius: 8, padding: 9, color: '#e8ebef' }}
        maxLength={160} onChange={(event) => setQuery(event.target.value)}
        placeholder="Projektname, Thema oder Dokumenttitel" /></label>
      <button className="primary" type="submit" disabled={busy || cacheBusy || !!preparingChat || query.trim().length < 2}>
        {busy ? 'Suche…' : 'Alle lokalen Quellen durchsuchen'}</button>
      <label>Dokumentordner für sinngemäße Suche
        <select aria-label="Dokumentordner für sinngemäße Suche" value={folderId}
          disabled={busy || !!preparingChat || sourceKind === 'memory' || sourceKind === 'project' || sourceKind === 'life_event'}
          onChange={(event) => { setFolderId(event.target.value); setDocumentId(''); }}>
          <option value="">Alle freigegebenen Ordner</option>
          {folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.path}</option>)}
        </select></label>
      <label>Einzelnes Dokument tief durchsuchen
        <select aria-label="Einzelnes Dokument tief durchsuchen" value={documentId}
          disabled={busy || !!preparingChat || sourceKind === 'memory' || sourceKind === 'project' || sourceKind === 'life_event'}
          onChange={(event) => setDocumentId(event.target.value)}>
          <option value="">Gemeinsame Suche, erste drei Fundstellen je Datei</option>
          {deepDocuments.map((document) => <option key={document.id} value={document.id}>{document.path}</option>)}
        </select></label>
      <button type="button" disabled={busy || cacheBusy || !!preparingChat || query.trim().length < 2}
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
      <small>Leert nur berechnete Suchvektoren. Dokumente, Erinnerungen, Lebensereignisse und Suchindex bleiben erhalten; die nächste sinngemäße Suche berechnet die Vektoren erneut.</small>
      <small>Benötigt lokal <code>embeddinggemma:300m</code>. {documentId ?
        'Im gewählten Dokument werden bis zu 100 Fundstellen und die fünf besten Treffer geprüft.' :
        sourceKind === 'memory' ? 'Es werden höchstens 30 gespeicherte Erinnerungen geprüft.' :
        sourceKind === 'project' ? 'Es werden höchstens 20 gespeicherte Arbeitsstände geprüft.' :
        sourceKind === 'document' ? 'Es werden höchstens 100 Fundstellen aus 30 Dateien geprüft.' :
        sourceKind === 'life_event' ? 'Es werden höchstens 30 zuletzt geänderte, manuell bestätigte Lebensereignisse geprüft.' :
        'Gemeinsam werden höchstens 100 Fundstellen aus 30 Dateien, 30 Erinnerungen, 20 Arbeitsstände und 30 Lebensereignisse geprüft. Für diese Frage bewusst vorgemerkte Dokumente werden innerhalb dieser Grenze zuerst geprüft.'} Die Ähnlichkeit ist kein Wahrheitsbeweis.</small>
    </form></section>
    {error && <p className="notice error" role="alert">{error}</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {selectedDraftSources.length > 0 && <section className="card" aria-label="Fundstellen für Entwurf">
      <p>{selectedDraftSources.length} von höchstens fünf Fundstellen für den Entwurf ausgewählt.</p>
      <div className="item-actions"><button type="button" className="primary"
        onClick={() => onDraft(selectedDraftSources)}>
        Mit {selectedDraftSources.length} Fundstellen Entwurf starten</button>
        <button type="button" onClick={() => setSelectedDraftSources([])}>Auswahl entfernen</button></div>
    </section>}
    {selectedChatDocuments.length > 0 && <section className="card" aria-label="Dokumente für lokalen Chat">
      <p>{selectedChatDocuments.length} von höchstens drei verschiedenen Dokumenten für den lokalen Chat ausgewählt. Bei PDFs wird der geprüfte Indextext mit Seitenbelegen als TXT angehängt, höchstens 8.000 Zeichen; das PDF-Original bleibt außerhalb des Chats.</p>
      <div className="item-actions"><button type="button" className="primary"
        disabled={!localChatReady || !!preparingChat}
        onClick={() => void prepareSelectedChat()}>
        {preparingChat === 'selection' ? 'Quellen werden geprüft…'
          : `Mit ${selectedChatDocuments.length} Dokumenten lokalen Chat vorbereiten`}</button>
        <button type="button" disabled={!!preparingChat}
          onClick={() => setSelectedChatDocuments([])}>Auswahl entfernen</button></div>
    </section>}
    {selectedChatPassages.length > 0 && <section className="card" aria-label="PDF-Fundstellen für lokalen Chat">
      <p>{selectedChatPassages.length} von höchstens drei verschiedenen PDF-Fundstellen ausgewählt. Jede wird vor dem lokalen Chat erneut gegen Original und Index geprüft. Der Klick startet noch kein Modell.</p>
      <div className="item-actions"><button type="button" className="primary"
        disabled={!localChatReady || !!preparingChat}
        onClick={() => void prepareSelectedPassages()}>
        {preparingChat === 'passage-selection' ? 'Fundstellen werden geprüft…'
          : `Mit ${selectedChatPassages.length} PDF-Fundstellen lokalen Chat vorbereiten`}</button>
        <button type="button" disabled={!!preparingChat}
          onClick={() => setSelectedChatPassages([])}>Auswahl entfernen</button></div>
    </section>}
    {semantic && <section className="card" aria-label="Sinngemäße Suchergebnisse">
      <h3>Sinngemäße Treffer für „{semantic.query}“</h3>
      <p>{semantic.document_scope ?
        `${semantic.counts.document} Fundstellen aus ${semantic.document_scope.title}` :
        `${semantic.counts.document} Dokumente · ${semantic.counts.memory} Erinnerungen · ${semantic.counts.project} Arbeitsstände · ${semantic.counts.life_event} Lebensereignisse`}</p>
      <small>Geprüft: {semantic.checked_passages} Dokumentfundstellen, {semantic.checked_memories} Erinnerungen, {semantic.checked_projects} Arbeitsstände, {semantic.checked_events} Lebensereignisse · Modell {semantic.model}</small>
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
        sourceKind === 'life_event' ? 'Es wurden höchstens die 30 zuletzt geänderten Lebensereignisse geprüft und höchstens zehn Treffer angezeigt.' :
        sourceKind === 'document' ? 'Es wurden höchstens 30 Dateien und 100 Fundstellen geprüft, je Datei die ersten drei; höchstens zehn Treffer erscheinen.' :
        'Die Kandidaten oder Treffer sind begrenzt. Dokumente: höchstens 30 Dateien und 100 Fundstellen in Pfadreihenfolge, je Datei die ersten drei. Zusätzlich 30 neueste Erinnerungen, 20 neueste Arbeitsstände und 30 zuletzt geänderte Lebensereignisse; höchstens zehn Treffer je Quellart.'}</p>}
      {semantic.stale_sources.length > 0 && <div className="notice" role="status">
        <strong>Originaldateien und Index weichen ab</strong><p>Die alten Auszüge werden nicht als Treffer gezeigt. Die betroffene Datei einzeln neu indexieren und die sinngemäße Suche danach ausdrücklich erneut starten.</p>
        <ul>{semantic.stale_sources.map((item) => <li key={item.document_id} style={{ overflowWrap: 'anywhere' }}>{item.path} <button type="button" disabled={busy || !!preparingChat} onClick={() => void reindexFromSearch(item.document_id)}>{reindexingDocument === item.document_id ? 'Indexiere…' : 'Diese Datei neu indexieren'}</button></li>)}</ul>
      </div>}
      {semantic.changed_sources.length > 0 && <p className="notice" role="status">
        {semantic.changed_sources.length} persönliche Quellen wurden während der Suche geändert oder entfernt und daher nicht als Treffer angezeigt. Erneut suchen.
      </p>}
      {!semantic.results.length && <p>Keine aktuellen Quellen gefunden.</p>}
      <div className="item-list">{semantic.results.map((hit, index) => <article className="item-row" key={`${hit.kind}:${hit.id}:${hit.passage_id ?? ''}`}>
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
          {localChatAction(hit, semantic.results.findIndex((item) => item.kind === 'document'
            && item.document_id === hit.document_id) === index)}
          {draftActions(hit)}
        </div>
      </article>)}</div>
    </section>}
    {result && <section className="card" aria-label="Suchergebnisse">
      <h3>Treffer für „{result.query}“</h3>
      <p>{result.counts.document} Dokumente · {result.counts.memory} Erinnerungen · {result.counts.project} Arbeitsstände · {result.counts.life_event} Lebensereignisse</p>
      {result.stale_sources.length > 0 && <div className="notice" role="status">
        <strong>Originaldateien und Index weichen ab</strong><p>Die betroffene Datei einzeln neu indexieren. Danach aktualisiert Jarvis die Stichworttreffer. Alte Auszüge erscheinen nicht als aktuelle Treffer.</p>
        <ul>{result.stale_sources.map((item) => <li key={item.document_id} style={{ overflowWrap: 'anywhere' }}>
          {item.path} · bisheriger Index SHA-256 {item.index_sha256} <button type="button" disabled={busy || !!preparingChat} onClick={() => void reindexFromSearch(item.document_id)}>{reindexingDocument === item.document_id ? 'Indexiere…' : 'Diese Datei neu indexieren'}</button></li>)}</ul>
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
      <div className="item-list">{result.results.map((hit, index) => <article className="item-row" key={`${hit.kind}:${hit.id}`}>
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
          {localChatAction(hit, result.results.findIndex((item) => item.kind === 'document'
            && item.document_id === hit.document_id) === index)}
          {draftActions(hit)}
        </div>
      </article>)}</div>
    </section>}
  </div>;
}
