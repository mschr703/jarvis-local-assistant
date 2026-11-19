import { useEffect, useRef, useState } from 'react';
import { api, downloadDocumentPackage, downloadPdfIndexText } from './api';
import MarkdownEditor from './MarkdownEditor';

export type Source = { passage_id: number; document_id: string; path: string; page: number | null; text: string; policy: string; sha256: string; source_status?: 'current' | 'needs_reindex' };
type Folder = { id: string; path: string; policy: 'local_only' | 'cloud_allowed' };
type Document = { id: string; folder_id: string; path: string; policy: string; status: string; excluded: number; error: string | null; sha256: string | null; indexed_at: string | null; size: number | null };
type Freshness = { folder_id: string; checked: number; current: number; stale: number; limited: boolean; stale_sources: { document_id: string; path: string; status: string }[] };
type DocumentIndexResult = { document_id: string; outcome: 'indexed' | 'unchanged' | 'removed' | 'ocr_pending' | 'error'; document: Document | null };
type OcrPagePreview = { document_id: string; sha256: string; pages_total: number;
  pages: { page: number; already_indexed: boolean }[]; max_selection: number; filename: string };
type Memory = { id: string; text: string; source_ref: string; policy: 'local_only' | 'cloud_allowed'; updated_at: string };
type RequestedSource = { kind: 'document' | 'memory'; id: string; serial: number };

export default function DocumentView({ onDraft, onLearn, onSourcesInvalidated, visible, requestedSource }: {
  onDraft: (sources: Source[]) => void; onLearn: (sources: Source[]) => void;
  onSourcesInvalidated: () => void; visible: boolean; requestedSource: RequestedSource | null;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [packageSelection, setPackageSelection] = useState<string[]>([]);
  const [packageBusy, setPackageBusy] = useState(false);
  const [indexTextBusy, setIndexTextBusy] = useState<string | null>(null);
  const [ocrPreview, setOcrPreview] = useState<OcrPagePreview | null>(null);
  const [ocrPageSelection, setOcrPageSelection] = useState<number[]>([]);
  const [ocrPreviewBusy, setOcrPreviewBusy] = useState<string | null>(null);
  const [memories, setMemories] = useState<Memory[]>([]);
  const [folderPath, setFolderPath] = useState('');
  const [folderPolicy, setFolderPolicy] = useState<Folder['policy']>('local_only');
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<Source[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [indexing, setIndexing] = useState(false);
  const indexingRef = useRef(false);
  const [checkingFolder, setCheckingFolder] = useState<string | null>(null);
  const [freshness, setFreshness] = useState<Record<string, Freshness>>({});
  const [memoryText, setMemoryText] = useState('');
  const [memorySource, setMemorySource] = useState('');
  const [memoryPolicy, setMemoryPolicy] = useState<Memory['policy']>('local_only');
  const [memorySearch, setMemorySearch] = useState('');
  const [memoryFilter, setMemoryFilter] = useState('');
  const [editing, setEditing] = useState<string | null>(null);
  const [memorySaving, setMemorySaving] = useState(false);
  const memorySavingRef = useRef(false);
  const [notice, setNotice] = useState('');
  const [editingMarkdown, setEditingMarkdown] = useState<string | null>(null);
  const refreshRequest = useRef(0);
  const searchRequest = useRef(0);
  const handledSourceSerial = useRef(0);
  const [sourceFocus, setSourceFocus] = useState<(RequestedSource & { found: boolean }) | null>(null);

  const clearResults = () => {
    ++searchRequest.current;
    setResults([]);
    setSelected([]);
  };

  const refresh = async (filter = memoryFilter) => {
    const request = ++refreshRequest.current;
    try {
      const [nextFolders, nextDocuments, nextMemories] = await Promise.all([
        api<Folder[]>('/folders'), api<Document[]>('/documents'),
        api<Memory[]>(`/memories${filter ? `?q=${encodeURIComponent(filter)}` : ''}`),
      ]);
      if (request === refreshRequest.current) {
        setFolders(nextFolders); setDocuments(nextDocuments); setMemories(nextMemories);
        setOcrPreview((current) => current && nextDocuments.some((item) =>
          item.id === current.document_id && item.sha256 === current.sha256
          && ['indexed', 'ocr_pending'].includes(item.status) && !item.excluded) ? current : null);
        setPackageSelection((current) => current.filter((id) => nextDocuments.some(
          (item) => item.id === id && item.status === 'indexed' && !item.excluded)));
        if (requestedSource && handledSourceSerial.current !== requestedSource.serial) {
          handledSourceSerial.current = requestedSource.serial;
          const found = requestedSource.kind === 'document'
            ? nextDocuments.some((item) => item.id === requestedSource.id)
            : nextMemories.some((item) => item.id === requestedSource.id);
          setSourceFocus({ ...requestedSource, found });
          if (found && requestedSource.kind === 'document') {
            try {
              const check = await api<{ status: 'current' | 'needs_reindex' }>(
                `/documents/${encodeURIComponent(requestedSource.id)}/freshness`);
              if (request === refreshRequest.current && check.status !== 'current')
                setNotice('Das Original hat sich seit der Suche geändert oder ist nicht mehr freigegeben. Vor weiterer Nutzung neu indexieren.');
            } catch (error) {
              if (request === refreshRequest.current)
                setNotice(`Dokumentquelle nicht mehr prüfbar: ${(error as Error).message}`);
            }
          }
        }
      }
    } catch (error) {
      if (request === refreshRequest.current) throw error;
    }
  };
  useEffect(() => {
    if (visible) {
      if (requestedSource && handledSourceSerial.current !== requestedSource.serial) setNotice('');
      if (requestedSource?.kind === 'memory') setMemoryFilter('');
      void refresh(requestedSource?.kind === 'memory' ? '' : memoryFilter)
        .catch((error: Error) => setNotice(error.message));
    }
    return () => { ++refreshRequest.current; };
  }, [visible, requestedSource?.serial]);
  useEffect(() => {
    if (!visible || !sourceFocus || sourceFocus.serial !== requestedSource?.serial) return;
    if (!sourceFocus.found) {
      setNotice('Die gesuchte Quelle ist nicht mehr vorhanden. Suche erneut.');
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      window.document.getElementById(`search-source-${sourceFocus.kind}-${sourceFocus.id}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [visible, sourceFocus, requestedSource?.serial]);

  const addFolder = async () => {
    try {
      await api<Folder>('/folders', { method: 'POST', body: JSON.stringify({ path: folderPath, policy: folderPolicy }) });
      setFolderPath(''); setNotice('Ordner freigegeben. Starte jetzt die Indexierung.'); await refresh();
    } catch (error) { setNotice((error as Error).message); }
  };
  const index = async (id: string) => {
    if (indexingRef.current) return;
    indexingRef.current = true;
    setIndexing(true);
    try {
      const counts = await api<Record<string, number>>(`/folders/${id}/index`, { method: 'POST' });
      setFreshness((prior) => { const next = { ...prior }; delete next[id]; return next; });
      setNotice(`Index aktualisiert: ${counts.indexed} neu/geändert, ${counts.unchanged} unverändert, ${counts.ocr_pending} OCR ausstehend, ${counts.errors} Fehler.${counts.limited ? ' Scanlimit erreicht; vorhandene übrige Indexeinträge bleiben erhalten.' : ''}`);
      clearResults();
      setPackageSelection([]);
      onSourcesInvalidated();
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { indexingRef.current = false; setIndexing(false); }
  };
  const reindexDocument = async (id: string) => {
    if (indexingRef.current) return;
    indexingRef.current = true; setIndexing(true);
    try {
      const result = await api<DocumentIndexResult>(`/documents/${encodeURIComponent(id)}/index`,
        { method: 'POST' });
      if (result.outcome !== 'unchanged') {
        clearResults(); onSourcesInvalidated();
        setPackageSelection((current) => current.filter((selectedId) => selectedId !== id));
      }
      setNotice(result.outcome === 'indexed'
        ? `Datei neu indexiert. SHA-256 ${result.document?.sha256 || 'fehlt'}.`
        : result.outcome === 'unchanged' ? 'Datei ist unverändert.'
          : result.outcome === 'removed' ? 'Original fehlt; dieser Eintrag wurde aus dem Index entfernt.'
            : result.outcome === 'ocr_pending' ? 'Datei hat keine lesbare Textschicht; OCR steht aus.'
              : `Datei konnte nicht indexiert werden: ${result.document?.error || 'unbekannter Fehler'}.`);
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { indexingRef.current = false; setIndexing(false); }
  };
  const ocrDocument = async (id: string, wasIndexed = false,
                             pages?: number[], expectedSha256?: string) => {
    if (indexingRef.current) return;
    indexingRef.current = true; setIndexing(true);
    try {
      const result = await api<DocumentIndexResult>(`/documents/${encodeURIComponent(id)}/ocr`,
        { method: 'POST', ...(pages && expectedSha256 ? { body: JSON.stringify({
          pages, expected_sha256: expectedSha256,
        }) } : {}) });
      if (result.outcome === 'indexed') {
        clearResults(); onSourcesInvalidated();
      }
      setNotice(result.outcome === 'indexed'
        ? wasIndexed ? 'PDF-Bildseiten lokal ergänzt. Die Texte sind mit Seitenbeleg durchsuchbar.'
          : 'Scan-PDF lokal erkannt. Der Text ist jetzt mit Seitenbeleg durchsuchbar.'
        : result.outcome === 'unchanged' ? 'Keine neuen Bildseiten-Texte erkannt; der Index bleibt unverändert.'
          : result.outcome === 'ocr_pending' ? 'OCR hat keinen lesbaren Text erkannt.'
            : `OCR fehlgeschlagen: ${result.document?.error || 'unbekannter Fehler'}.`);
      setOcrPreview(null); setOcrPageSelection([]);
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { indexingRef.current = false; setIndexing(false); }
  };
  const previewOcrPages = async (document: Document) => {
    if (indexing || ocrPreviewBusy) return;
    setOcrPreviewBusy(document.id); setNotice('');
    try {
      const preview = await api<Omit<OcrPagePreview, 'filename'>>(
        `/documents/${encodeURIComponent(document.id)}/ocr-pages`);
      setOcrPreview({ ...preview, filename: document.path.split(/[\\/]/).at(-1) || 'PDF' });
      setOcrPageSelection([]);
      if (!preview.pages.length) setNotice('Dieses PDF hat keine Seiten ohne extrahierbare Textschicht.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setOcrPreviewBusy(null); }
  };
  const checkFreshness = async (id: string) => {
    if (checkingFolder) return;
    setCheckingFolder(id);
    try {
      const report = await api<Freshness>(`/folders/${id}/freshness`);
      setFreshness((prior) => ({ ...prior, [id]: report }));
      setNotice(`Frischecheck: ${report.current} aktuell, ${report.stale} geändert oder fehlend.`);
    } catch (error) { setNotice((error as Error).message); }
    finally { setCheckingFolder(null); }
  };
  const changePolicy = async (folder: Folder) => {
    const policy = folder.policy === 'local_only' ? 'cloud_allowed' : 'local_only';
    try {
      await api<Folder>(`/folders/${folder.id}/policy`, { method: 'PATCH', body: JSON.stringify({ policy }) });
      clearResults();
      onSourcesInvalidated();
      setNotice(policy === 'cloud_allowed' ? 'Dieser Ordner und seine indexierten Texte sind für ausdrücklich freigegebene Cloud-Anfragen verfügbar.' : 'Cloud-Freigabe dieses Ordners entfernt.');
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
  };
  const removeFolder = async (folder: Folder) => {
    try {
      await api(`/folders/${folder.id}`, { method: 'DELETE' });
      setFreshness((prior) => { const next = { ...prior }; delete next[folder.id]; return next; });
      if (documents.some((document) => document.folder_id === folder.id && document.id === editingMarkdown)) setEditingMarkdown(null);
      clearResults();
      onSourcesInvalidated();
      setNotice('Freigabe und lokaler Index entfernt. Originaldateien bleiben bestehen.');
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
  };
  const toggleDocument = async (document: Document) => {
    try {
      await api<Document>(`/documents/${document.id}/excluded`, {
        method: 'PATCH', body: JSON.stringify({ excluded: !document.excluded }),
      });
      setFreshness((prior) => { const next = { ...prior }; delete next[document.folder_id]; return next; });
      if (!document.excluded && editingMarkdown === document.id) setEditingMarkdown(null);
      clearResults();
      setPackageSelection((current) => current.filter((id) => id !== document.id));
      onSourcesInvalidated();
      setNotice(document.excluded
        ? 'Dokument wieder einbezogen. Aktualisiere den Index, bevor es in der Suche erscheint.'
        : 'Dokument aus dem lokalen Suchindex entfernt.');
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
  };
  const downloadPackage = async () => {
    if (packageBusy || indexing || !packageSelection.length) return;
    setPackageBusy(true); setNotice('');
    try {
      const blob = await downloadDocumentPackage(packageSelection);
      const url = URL.createObjectURL(blob);
      try {
        const link = window.document.createElement('a');
        link.href = url; link.download = 'jarvis-unterlagenpaket.zip';
        window.document.body.append(link); link.click(); link.remove();
      } finally { window.setTimeout(() => URL.revokeObjectURL(url), 10_000); }
      setNotice(`${packageSelection.length} geprüfte Originale als ZIP heruntergeladen.`);
      setPackageSelection([]);
    } catch (error) { setNotice((error as Error).message); }
    finally { setPackageBusy(false); }
  };
  const downloadIndexText = async (document: Document) => {
    if (indexTextBusy || indexing) return;
    setIndexTextBusy(document.id); setNotice('');
    try {
      const blob = await downloadPdfIndexText(document.id);
      const url = URL.createObjectURL(blob);
      try {
        const link = window.document.createElement('a');
        link.href = url;
        link.download = `${document.path.split(/[\\/]/).at(-1)?.replace(/\.pdf$/i, '') || 'dokument'}-indextext.txt`;
        window.document.body.append(link); link.click(); link.remove();
      } finally { window.setTimeout(() => URL.revokeObjectURL(url), 10_000); }
      setNotice('Geprüften PDF-Indextext heruntergeladen.');
    } catch (error) { setNotice((error as Error).message); }
    finally { setIndexTextBusy(null); }
  };
  const search = async () => {
    clearResults();
    if (!query.trim()) return;
    const request = searchRequest.current;
    try {
      const found = await api<Source[]>(`/search?q=${encodeURIComponent(query)}`);
      if (request === searchRequest.current) setResults(found);
    } catch (error) {
      if (request === searchRequest.current) setNotice((error as Error).message);
    }
  };
  const addMemory = async () => {
    if (memorySavingRef.current) return;
    memorySavingRef.current = true;
    setMemorySaving(true);
    try {
      await api<Memory>('/memories', { method: 'POST', body: JSON.stringify({ text: memoryText, source_ref: memorySource, policy: memoryPolicy }) });
      setMemoryText(''); setMemorySource(''); setNotice('Erinnerung gespeichert.'); await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { memorySavingRef.current = false; setMemorySaving(false); }
  };
  const saveMemory = async (id: string) => {
    if (memorySavingRef.current) return;
    memorySavingRef.current = true;
    setMemorySaving(true);
    try {
      await api<Memory>(`/memories/${id}`, { method: 'PATCH', body: JSON.stringify({ text: memoryText, source_ref: memorySource, policy: memoryPolicy }) });
      setEditing(null); setMemoryText(''); setMemorySource(''); setNotice('Erinnerung geändert.'); await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { memorySavingRef.current = false; setMemorySaving(false); }
  };
  const deleteMemory = async (id: string) => {
    try {
      await api<{ deleted: boolean }>(`/memories/${id}`, { method: 'DELETE' });
      if (editing === id) { setEditing(null); setMemoryText(''); setMemorySource(''); }
      setNotice('Erinnerung gelöscht.'); await refresh();
    }
    catch (error) { setNotice((error as Error).message); }
  };
  const searchMemories = async (filter: string) => {
    setMemoryFilter(filter);
    try { await refresh(filter); }
    catch (error) { setNotice((error as Error).message); }
  };

  return <div className="section-stack">
    <h2>Dokumente und Wissen</h2><p className="intro">Nur ausdrücklich freigegebene Ordner werden indexiert. Unterstützt werden TXT, Markdown, Text-PDF und DOCX mit lesbarem Haupttext. Nach Installation von Tesseract können reine Scan-PDFs und Bildseiten gemischter PDFs einzeln und ausdrücklich lokal per OCR gelesen werden. Ein lokaler Index bleibt auf diesem Laptop. „Cloud erlaubt“ gibt nur ausgewählte Ausschnitte für OpenRouter-Anfragen frei.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Ordner freigeben</h3>
      <div className="inline-form"><input aria-label="Absoluter Ordnerpfad" placeholder="C:\\Pfad\\zum\\Ordner" value={folderPath} onChange={(event) => setFolderPath(event.target.value)} />
        <select aria-label="Cloud-Freigabe" value={folderPolicy} onChange={(event) => setFolderPolicy(event.target.value as Folder['policy'])}><option value="local_only">Nur lokal</option><option value="cloud_allowed">Cloud erlaubt</option></select>
        <button className="primary" onClick={() => void addFolder()} disabled={!folderPath.trim()}>Freigeben</button></div>
      <div className="item-list">{folders.map((folder) => <div key={folder.id} className="item-row"><div><strong>{folder.path}</strong><small>{folder.policy === 'local_only' ? 'Nur lokal' : 'Cloud erlaubt'}</small>{freshness[folder.id] && <div role="status"><small>Frischecheck: {freshness[folder.id].current} aktuell, {freshness[folder.id].stale} geändert oder fehlend (von {freshness[folder.id].checked} geprüft). {freshness[folder.id].limited && 'Auf 100 Dokumente begrenzt; weitere Dateien wurden nicht geprüft.'}</small>{freshness[folder.id].stale_sources.map((source) => <small key={source.document_id}>Neu indexieren: {source.path}</small>)}</div>}</div><div className="item-actions"><button disabled={indexing || checkingFolder !== null} onClick={() => void checkFreshness(folder.id)}>{checkingFolder === folder.id ? 'Prüfe…' : 'Frische prüfen'}</button><button disabled={indexing || checkingFolder !== null} onClick={() => void index(folder.id)}>{indexing ? 'Indexiere…' : 'Index aktualisieren'}</button><button onClick={() => void changePolicy(folder)}>{folder.policy === 'local_only' ? 'Cloud für ausgewählte Ausschnitte erlauben' : 'Cloud-Freigabe entfernen'}</button><button onClick={() => void removeFolder(folder)}>Ordnerfreigabe entfernen</button></div></div>)}</div>
    </section>
    <section className="card"><h3>Dokumente</h3>{documents.length === 0 ? <p>Noch keine Dokumente indexiert.</p> : <div className="item-list">{documents.map((document) => <div id={`search-source-document-${document.id}`} key={document.id} className={`item-row document-row${requestedSource?.kind === 'document' && requestedSource.id === document.id ? ' source-opened' : ''}`}><div><strong>{document.path}</strong><small>{document.excluded ? 'Ausgeschlossen' : document.status === 'ocr_pending' ? 'OCR ausstehend: keine Textschicht' : document.status === 'pending_index' ? 'Index ausstehend' : document.status}{document.error && ` · ${document.error}`} · {document.policy === 'local_only' ? 'nur lokal' : 'Cloud erlaubt'}</small>{!document.excluded && document.sha256 && document.indexed_at && document.status === 'indexed' && <small>Indexstand: {new Date(document.indexed_at).toLocaleString('de-DE')} · SHA-256 {document.sha256}</small>}</div><div className="item-actions">{!document.excluded && document.status === 'indexed' && <label><input type="checkbox" aria-label={`Dokument ${document.path} für Unterlagenpaket auswählen`} checked={packageSelection.includes(document.id)} disabled={indexing || packageBusy || (packageSelection.length >= 10 && !packageSelection.includes(document.id))} onChange={() => setPackageSelection((current) => current.includes(document.id) ? current.filter((selectedId) => selectedId !== document.id) : current.length < 10 ? [...current, document.id] : current)} />Für Paket auswählen</label>}{!document.excluded && <button disabled={indexing} onClick={() => void reindexDocument(document.id)}>Diese Datei neu indexieren</button>}{!document.excluded && document.status === 'ocr_pending' && document.sha256 && /\.pdf$/i.test(document.path) && <button disabled={indexing} onClick={() => void ocrDocument(document.id)}>{indexing ? 'OCR läuft…' : 'Scan-PDF lokal per OCR lesen'}</button>}{!document.excluded && ['indexed', 'ocr_pending'].includes(document.status) && /\.pdf$/i.test(document.path) && <button disabled={indexing || ocrPreviewBusy !== null} onClick={() => void previewOcrPages(document)}>{ocrPreviewBusy === document.id ? 'Prüfe PDF-Seiten…' : document.status === 'ocr_pending' ? 'Scan-PDF-Seiten auswählen' : 'PDF-Bildseiten für OCR auswählen'}</button>}{!document.excluded && document.status === 'indexed' && /\.pdf$/i.test(document.path) && <button disabled={indexing || indexTextBusy !== null} onClick={() => void downloadIndexText(document)}>{indexTextBusy === document.id ? 'Indextext wird geprüft…' : 'Geprüften PDF-Indextext herunterladen'}</button>}{!document.excluded && document.status === 'indexed' && /\.(?:md|markdown)$/i.test(document.path) && <button disabled={indexing} onClick={() => setEditingMarkdown(document.id)}>Markdown bearbeiten</button>}<button disabled={indexing} onClick={() => void toggleDocument(document)}>{document.excluded ? 'Einbeziehen' : 'Ausschließen'}</button></div></div>)}</div>}
      {packageSelection.length > 0 && <div className="item-actions package-actions"><p>{packageSelection.length} von höchstens zehn Dokumenten ausgewählt. Zusammen höchstens 32 MiB Originaldaten. Der Download ist ein unverschlüsseltes ZIP.</p><button type="button" className="primary" disabled={indexing || packageBusy} onClick={() => void downloadPackage()}>{packageBusy ? 'Originale werden geprüft…' : `Geprüftes Unterlagenpaket herunterladen (${packageSelection.length})`}</button><button type="button" disabled={packageBusy} onClick={() => setPackageSelection([])}>Paketauswahl entfernen</button></div>}</section>
    {ocrPreview && <section className="card" aria-label="PDF-Seiten für OCR auswählen">
      <h3>PDF-Seiten lokal per OCR prüfen</h3>
      <p>{ocrPreview.filename} · {ocrPreview.pages_total} Seiten insgesamt. Nur Seiten ohne extrahierbare Textschicht sind wählbar. Bereits erkannte Bildseiten können erneut gelesen werden.</p>
      {ocrPreview.pages.length === 0 ? <p>Keine Seiten ohne Textschicht gefunden.</p> : <>
        <p>{ocrPageSelection.length} von höchstens {ocrPreview.max_selection} Seiten ausgewählt. OCR läuft nur lokal und ändert das Original nicht.</p>
        <div className="ocr-page-choices">{ocrPreview.pages.map((item) => <label key={item.page}>
          <input type="checkbox" aria-label={`PDF-Seite ${item.page} für OCR auswählen`}
            checked={ocrPageSelection.includes(item.page)}
            disabled={indexing || (ocrPageSelection.length >= ocrPreview.max_selection && !ocrPageSelection.includes(item.page))}
            onChange={() => setOcrPageSelection((current) => current.includes(item.page)
              ? current.filter((page) => page !== item.page)
              : current.length < ocrPreview.max_selection ? [...current, item.page] : current)} />
          Seite {item.page}{item.already_indexed ? ' · OCR-Text bereits im Index' : ' · noch kein OCR-Text im Index'}
        </label>)}</div>
      </>}
      <div className="item-actions"><button type="button" className="primary"
        disabled={indexing || !ocrPageSelection.length}
        onClick={() => void ocrDocument(ocrPreview.document_id,
          documents.some((item) => item.id === ocrPreview.document_id && item.status === 'indexed'),
          ocrPageSelection, ocrPreview.sha256)}>
        {indexing ? 'OCR läuft…' : `Ausgewählte ${ocrPageSelection.length} Seiten per OCR lesen`}</button>
        <button type="button" disabled={indexing} onClick={() => {
          setOcrPreview(null); setOcrPageSelection([]);
        }}>Auswahl schließen</button></div>
    </section>}
    {editingMarkdown && <MarkdownEditor key={editingMarkdown} documentId={editingMarkdown} onClose={() => setEditingMarkdown(null)} onChanged={async () => { clearResults(); onSourcesInvalidated(); await refresh(); }} />}
    <section className="card"><h3>Suche</h3><div className="inline-form"><input aria-label="Dokumente durchsuchen" value={query} onChange={(event) => { clearResults(); setQuery(event.target.value); }} onKeyDown={(event) => { if (event.key === 'Enter') void search(); }} /><button onClick={() => void search()} disabled={!query.trim()}>Suchen</button></div>
      <p>Wähle höchstens fünf aktuelle Fundstellen.</p><div className="item-list">{results.map((result) => <label key={result.passage_id} className="source-result"><input type="checkbox" checked={selected.includes(result.passage_id)} disabled={result.source_status === 'needs_reindex' || (selected.length >= 5 && !selected.includes(result.passage_id))} onChange={(event) => setSelected(event.target.checked ? [...selected, result.passage_id] : selected.filter((id) => id !== result.passage_id))} /><span><strong>{result.path}{result.page ? ` · Seite ${result.page}` : ''}</strong><small>{result.policy === 'local_only' ? 'Nur lokal' : 'Cloud erlaubt'} · {result.source_status === 'needs_reindex' ? 'Original geändert oder fehlt: zuerst Index aktualisieren' : 'Original geprüft'}</small><p>{result.source_status === 'needs_reindex' ? 'Gespeicherter Auszug (veraltet): ' : ''}{result.text}</p></span></label>)}</div>
      {selected.length > 0 && <div className="item-actions"><button className="primary" onClick={() => onDraft(results.filter((item) => selected.includes(item.passage_id)))}>Mit {selected.length} Fundstelle(n) Entwurf erstellen</button>
        <button onClick={() => onLearn(results.filter((item) => selected.includes(item.passage_id)))} disabled={selected.length > 3}>Mit Fundstelle(n) lernen</button></div>}
    </section>
    <section className="card"><h3>Bewusst gespeichertes Wissen</h3><p>Eine Erinnerung braucht immer eine Quelle. Sie lässt sich später bearbeiten oder löschen.</p>
      <div className="inline-form"><input aria-label="Erinnerungstext durchsuchen" value={memorySearch} onChange={(event) => setMemorySearch(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') void searchMemories(memorySearch.trim()); }} /><button onClick={() => void searchMemories(memorySearch.trim())}>Wissen suchen</button>{memoryFilter && <button onClick={() => { setMemorySearch(''); void searchMemories(''); }}>Alle anzeigen</button>}</div>
      {memoryFilter && <p>{memories.length} Treffer für „{memoryFilter}“</p>}
      <div className="memory-form"><textarea aria-label="Erinnerung" value={memoryText} disabled={memorySaving} onChange={(event) => setMemoryText(event.target.value)} placeholder="Projektentscheidung…" /><input aria-label="Quelle der Erinnerung" value={memorySource} disabled={memorySaving} onChange={(event) => setMemorySource(event.target.value)} placeholder="Quelle / Anlass" /><select aria-label="Cloud-Freigabe der Erinnerung" value={memoryPolicy} disabled={memorySaving} onChange={(event) => setMemoryPolicy(event.target.value as Memory['policy'])}><option value="local_only">Nur lokal</option><option value="cloud_allowed">Cloud erlaubt</option></select><button className="primary" onClick={() => void (editing ? saveMemory(editing) : addMemory())} disabled={memorySaving || !memoryText.trim() || !memorySource.trim()}>{memorySaving ? 'Speichere…' : editing ? 'Änderung speichern' : 'Erinnerung speichern'}</button></div>
      <div className="item-list">{memories.map((memory) => <div id={`search-source-memory-${memory.id}`} key={memory.id} className={`item-row${requestedSource?.kind === 'memory' && requestedSource.id === memory.id ? ' source-opened' : ''}`}><div><strong>{memory.text}</strong><small>Quelle: {memory.source_ref} · {memory.policy === 'local_only' ? 'nur lokal' : 'Cloud erlaubt'}</small></div><div className="item-actions"><button disabled={memorySaving} onClick={() => { setEditing(memory.id); setMemoryText(memory.text); setMemorySource(memory.source_ref); setMemoryPolicy(memory.policy); }}>Bearbeiten</button><button disabled={memorySaving} onClick={() => void deleteMemory(memory.id)}>Löschen</button></div></div>)}</div>
    </section>
  </div>;
}
