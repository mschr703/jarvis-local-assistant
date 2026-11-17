import { useEffect, useState } from 'react';
import { api } from './api';

type Folder = { id: string; path: string };
type ProjectFile = { path: string; size: number; reason: string };
type Role = 'entrypoints' | 'tests' | 'config' | 'docs';
type Overview = {
  folder_id: string; root: string; git_checkout: boolean; scanned_files: number;
  scanned_directories: number; skipped: number; limited: boolean;
  languages: Record<string, number>; modules: { path: string; code_files: number }[];
  counts: Record<Role, number>; files: Record<Role, ProjectFile[]>;
  lists_limited: Record<Role, boolean>;
};
type Preview = { path: string; lines: { number: number; text: string }[];
  line_count: number; limited: boolean; size: number };

const roles: [Role, string][] = [
  ['entrypoints', 'Mögliche Einstiegspunkte'], ['tests', 'Tests'],
  ['config', 'Konfiguration'], ['docs', 'Projektbeschreibung'],
];

export default function CodeProjectView({ visible, onDocuments }: {
  visible: boolean; onDocuments: () => void;
}) {
  const [folders, setFolders] = useState<Folder[]>([]);
  const [folderId, setFolderId] = useState('');
  const [overview, setOverview] = useState<Overview | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!visible) return;
    let active = true;
    void api<Folder[]>('/folders').then((items) => {
      if (!active) return;
      setFolders(items);
      setFolderId((current) => items.some((item) => item.id === current)
        ? current : items[0]?.id || '');
    }).catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [visible]);

  const scan = async () => {
    if (!folderId || busy) return;
    setBusy(true); setError(''); setOverview(null); setPreview(null);
    try { setOverview(await api<Overview>(`/code-projects/${folderId}/overview`)); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const openFile = async (path: string) => {
    if (!folderId || busy) return;
    setBusy(true); setError(''); setPreview(null);
    try { setPreview(await api<Preview>(
      `/code-projects/${folderId}/file?path=${encodeURIComponent(path)}`)); }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };

  return <div className="section-stack"><h2>Codeprojekt-Überblick</h2>
    <p className="intro">Jarvis zeigt tatsächlich vorhandene Dateien und konventionelle Rollen in einem ausdrücklich freigegebenen lokalen Ordner. Die Zuordnung ist eine Dateinamen-Heuristik; prüfe den Inhalt vor Änderungen. Es erfolgt kein Modell- oder Cloud-Aufruf.</p>
    <section className="card"><div className="card-heading"><h3>Projektordner wählen</h3><button onClick={onDocuments}>Ordner freigeben</button></div>
      <label style={{ display: 'grid', minWidth: 0, maxWidth: '100%', gap: 6, overflowWrap: 'anywhere' }}>Freigegebener Projektordner<select aria-label="Codeprojekt-Ordner" style={{ width: '100%', minWidth: 0, maxWidth: '100%', background: '#101823', border: '1px solid #3b4b60', borderRadius: 8, padding: 9, color: '#e8ebef' }} value={folderId} disabled={busy} onChange={(event) => { setFolderId(event.target.value); setOverview(null); setPreview(null); }}>
        {!folders.length && <option value="">Noch kein Ordner freigegeben</option>}
        {folders.map((folder) => <option value={folder.id} key={folder.id}>{folder.path}</option>)}
      </select></label>
      <p>Der Scan liest höchstens 3.000 Dateien in 400 Ordnern. Versteckte Pfade, Abhängigkeiten und Verknüpfungen werden ausgelassen. Originaldateien werden nicht geändert.</p>
      <button className="primary" disabled={!folderId || busy} onClick={() => void scan()}>{busy ? 'Lese…' : 'Projekt überblicken'}</button>
    </section>
    {error && <p className="notice error" role="alert">{error}</p>}
    {overview && <section className="card" aria-label="Projektüberblick"><h3>Gefundene Struktur</h3>
      <p style={{ overflowWrap: 'anywhere' }}>{overview.root}</p>
      <p>{overview.git_checkout ? 'Lokaler Git-Checkout erkannt' : 'Kein Git-Checkout am Ordnerstamm erkannt'} · {overview.scanned_files} Dateien in {overview.scanned_directories} Ordnern geprüft</p>
      {overview.limited && <p className="notice">Scan-Grenze erreicht: Die Übersicht ist unvollständig. Einen kleineren Projektordner freigeben.</p>}
      {overview.skipped > 0 && <p>{overview.skipped} versteckte, ausgelassene, verknüpfte oder nicht lesbare Einträge übersprungen.</p>}
      <h4>Sprachen</h4><p>{Object.entries(overview.languages).map(([name, count]) => `${name}: ${count}`).join(' · ') || 'Keine unterstützten Codedateien gefunden'}</p>
      <h4>Module nach oberstem Ordner</h4><ul>{overview.modules.map((item) => <li key={item.path}><code>{item.path}</code> · {item.code_files} Codedateien</li>)}</ul>
      {roles.map(([role, title]) => <div key={role}><h4>{title} ({overview.counts[role]})</h4>
        {overview.files[role].length ? <ul>{overview.files[role].map((item) => <li key={item.path} style={{ overflowWrap: 'anywhere' }}>
          <code>{item.path}</code> · {item.reason} · {item.size} Bytes <button disabled={busy} onClick={() => void openFile(item.path)}>Datei ansehen</button>
        </li>)}</ul> : <p>Keine passende Datei gefunden.</p>}
        {overview.lists_limited[role] && <p>Die Liste zeigt die ersten 50 Treffer.</p>}
      </div>)}
    </section>}
    {preview && <section className="card" aria-label="Projektdatei"><div className="card-heading"><h3>{preview.path}</h3><button onClick={() => setPreview(null)}>Schließen</button></div>
      <p>{preview.size} Bytes · {preview.line_count} Zeilen{preview.limited ? ' · nur die ersten 250 Zeilen sichtbar' : ''}</p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{preview.lines.map((line) => `${line.number}: ${line.text}`).join('\n')}</pre>
    </section>}
  </div>;
}
