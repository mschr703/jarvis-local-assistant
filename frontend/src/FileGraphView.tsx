import { useEffect, useState } from 'react';
import { api } from './api';

type Folder = { id: string; path: string };
type Node = { id: string; label: string; kind: string; source_file: string; source_location: string };
type Edge = { source: string; target: string; relation: string; confidence: 'EXTRACTED' | 'INFERRED'; source_file: string; source_location: string };
type Graph = { folder_id: string; source_root: string; files: Record<string, string>; nodes: Node[]; edges: Edge[]; fingerprint: string; graphify_version: string };
type ExportPreview = { target: string; files: string[]; fingerprint: string; nodes: number; edges: number };
type ExportRecord = { id: string; target: string; folder_id: string; files: number };

export default function FileGraphView({ visible, onDocuments }: { visible: boolean; onDocuments: () => void }) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState('');
  const [graph, setGraph] = useState<Graph | null>(null);
  const [vaultPath, setVaultPath] = useState('');
  const [exportPreview, setExportPreview] = useState<ExportPreview | null>(null);
  const [exports, setExports] = useState<ExportRecord[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const refresh = async () => {
    const nextFolders = await api<Folder[]>('/folders');
    setFolders(nextFolders);
    if (!folderId && nextFolders.length) setFolderId(nextFolders[0].id);
    setExports(await api<ExportRecord[]>('/file-graphs/exports'));
  };
  useEffect(() => {
    if (!visible) return;
    void refresh().catch((cause: Error) => setError(cause.message));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible]);

  const run = async (operation: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    setNotice('');
    try { await operation(); } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setBusy(false); }
  };

  const previewGraph = () => void run(async () => {
    const result = await api<Graph>(`/file-graphs/${folderId}/preview`, { method: 'POST' });
    setGraph(result);
    setExportPreview(null);
    setNotice(`${result.nodes.length} Knoten und ${result.edges.length} belegte Kanten gelesen.`);
  });
  const previewExport = () => void run(async () => {
    const result = await api<ExportPreview>(`/file-graphs/${folderId}/export-preview`, {
      method: 'POST', body: JSON.stringify({ vault_path: vaultPath }),
    });
    setExportPreview(result);
  });
  const exportGraph = () => void run(async () => {
    if (!exportPreview || !graph || exportPreview.fingerprint !== graph.fingerprint) return;
    const result = await api<ExportRecord>(`/file-graphs/${folderId}/exports`, {
      method: 'POST', body: JSON.stringify({ vault_path: vaultPath, fingerprint: graph.fingerprint }),
    });
    setExportPreview(null);
    await refresh();
    setNotice(`Exportiert nach ${result.target}. Die Rücknahme ist unten verfügbar.`);
  });
  const rollback = (record: ExportRecord) => void run(async () => {
    await api(`/file-graphs/exports/${record.id}`, { method: 'DELETE' });
    await refresh();
    setNotice(`Eigene Exportdateien in ${record.target} entfernt.`);
  });

  const labels = new Map(graph?.nodes.map((node) => [node.id, node.label]) || []);
  return <section className="file-graph">
    <p className="eyebrow">LOKALER DATEI-GRAPH</p>
    <h2>Belegte Verbindungen</h2>
    <p className="intro">Graphify liest Code als AST. Verweise zwischen Markdown-Notizen werden direkt aus den Dateien gelesen. Dieser Graph bleibt getrennt vom persönlichen Projektgraphen.</p>
    <section className="card">
      <div className="card-heading"><h3>Quelle wählen</h3><button type="button" onClick={onDocuments}>Ordner freigeben</button></div>
      <label>Freigegebener Ordner
        <select value={folderId} onChange={(event) => { setFolderId(event.target.value); setGraph(null); setExportPreview(null); }} disabled={busy}>
          {!folders.length && <option value="">Noch kein Ordner freigegeben</option>}
          {folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.path}</option>)}
        </select>
      </label>
      <p>Höchstens 100 Code- und Markdown-Dateien bis insgesamt 5 MiB; Verknüpfungen und versteckte Ordner werden ausgelassen. Es erfolgt kein Cloud-Aufruf.</p>
      <button type="button" disabled={!folderId || busy} onClick={previewGraph}>{busy ? 'Bitte warten…' : 'Graph erstellen'}</button>
    </section>
    {error && <p className="notice error" role="alert">{error}</p>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {graph && <section className="card">
      <h3>Vorschau</h3>
      <p>{graph.nodes.length} Knoten · {graph.edges.length} Kanten · Graphify {graph.graphify_version}</p>
      <h4>Kanten mit Beleg</h4>
      {graph.edges.length ? <ul>{graph.edges.map((edge, index) => <li key={index} style={{ overflowWrap: 'anywhere' }}>
        <strong>{labels.get(edge.source) || edge.source} → {labels.get(edge.target) || edge.target}</strong> · {edge.relation} · {edge.confidence} · <code>{edge.source_file}:{edge.source_location}</code>
      </li>)}</ul> : <p>Keine Verbindungen in den unterstützten Dateien gefunden.</p>}
      <details><summary>Alle Knoten ({graph.nodes.length})</summary><ul>{graph.nodes.map((node) => <li key={node.id} style={{ overflowWrap: 'anywhere' }}>{node.label} · {node.kind} · <code>{node.source_file}:{node.source_location}</code></li>)}</ul></details>
    </section>}
    {graph && <section className="card">
      <h3>In Obsidian exportieren</h3>
      <p>Jarvis schreibt ausschließlich in einen neuen Unterordner <code>Jarvis Graphify/Graph-…</code>. Vorhandene Dateien und <code>.obsidian</code> bleiben erhalten.</p>
      <label>Absoluter Pfad zu einem vorhandenen Obsidian-Vault
        <input value={vaultPath} onChange={(event) => { setVaultPath(event.target.value); setExportPreview(null); }} placeholder="Absoluter Pfad zum Obsidian-Vault" />
      </label>
      <div className="item-actions">
        <button type="button" disabled={!vaultPath.trim() || busy} onClick={previewExport}>Pfad prüfen</button>
      </div>
      {exportPreview && <div className="notice">
        <p><strong>Ziel:</strong> <code style={{ overflowWrap: 'anywhere' }}>{exportPreview.target}</code></p>
        <p><strong>Dateien ({exportPreview.files.length + 1}):</strong> {exportPreview.files.slice(0, 6).join(', ')}{exportPreview.files.length > 6 ? ', …' : ''} und manifest.json</p>
        <button type="button" disabled={busy || exportPreview.fingerprint !== graph.fingerprint} onClick={exportGraph}>Jetzt exportieren</button>
      </div>}
    </section>}
    <section className="card">
      <h3>Eigene Exporte zurücknehmen</h3>
      {exports.length ? <ul>{exports.map((record) => <li key={record.id} style={{ overflowWrap: 'anywhere' }}>
        <code>{record.target}</code> · {record.files} Inhalt{record.files === 1 ? '' : 'e'}
        <button type="button" disabled={busy} onClick={() => rollback(record)}>Eigene Dateien entfernen</button>
      </li>)}</ul> : <p>Keine aktiven Exporte.</p>}
      <p>Wenn eine Exportdatei seitdem verändert wurde, bricht die Rücknahme ab.</p>
    </section>
  </section>;
}
