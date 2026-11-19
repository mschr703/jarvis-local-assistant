import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Opened = { document_id: string; path: string; content: string; sha256: string; indexed_sha256: string | null; undo_revision_id: string | null };
type Preview = { before_sha256: string; after_sha256: string; diff: string; approval_token: string };
type SaveResult = { revision_id: string; sha256: string; index_error: string | null };

export default function MarkdownEditor({ documentId, onClose, onChanged }: {
  documentId: string; onClose: () => void; onChanged: () => Promise<void>;
}) {
  const [opened, setOpened] = useState<Opened | null>(null);
  const [draft, setDraft] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const busyRef = useRef(false);
  const loadRequest = useRef(0);
  const url = `/documents/${documentId}/markdown`;

  const load = async () => {
    const request = ++loadRequest.current;
    const next = await api<Opened>(url);
    if (request !== loadRequest.current) return;
    setOpened(next); setDraft(next.content); setPreview(null);
  };
  useEffect(() => {
    void load().catch((error: Error) => setNotice(error.message));
    return () => { ++loadRequest.current; };
  }, [documentId]);

  const work = async (action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try { await action(); }
    catch (error) { setNotice((error as Error).message); }
    finally { busyRef.current = false; setBusy(false); }
  };

  const makePreview = () => work(async () => {
    if (!opened) return;
    const next = await api<Preview>(url + '/preview', {
      method: 'POST', body: JSON.stringify({ expected_sha256: opened.sha256, content: draft }),
    });
    setPreview(next); setNotice('Änderungsvorschau erstellt. Prüfe den Unterschied vor dem Speichern.');
  });
  const save = () => work(async () => {
    if (!opened || !preview) return;
    const result = await api<SaveResult>(url, {
      method: 'PUT', body: JSON.stringify({ expected_sha256: opened.sha256,
        content: draft, approval_token: preview.approval_token }),
    });
    await load(); await onChanged();
    setNotice(result.index_error
      ? `Datei gespeichert. Der Suchindex konnte nicht aktualisiert werden: ${result.index_error}. Index manuell aktualisieren.`
      : 'Markdown-Datei gespeichert und Suchindex aktualisiert. Rückgängig ist verfügbar.');
  });
  const undo = () => work(async () => {
    if (!opened?.undo_revision_id) return;
    const result = await api<{ sha256: string; index_error: string | null }>(
      `${url}/${opened.undo_revision_id}/undo`, { method: 'POST' });
    await load(); await onChanged();
    setNotice(result.index_error
      ? `Änderung rückgängig gemacht. Der Suchindex konnte nicht aktualisiert werden: ${result.index_error}.`
      : 'Änderung rückgängig gemacht und Suchindex aktualisiert.');
  });

  return <section className="card markdown-editor"><div className="item-row"><div><h3>Markdown-Notiz bearbeiten</h3><p>{opened?.path || 'Datei wird geladen…'}</p></div><button onClick={onClose}>Schließen</button></div>
    <p>Nur diese freigegebene Datei wird geändert. Vor dem Speichern erscheint ein genauer Unterschied. Eine zwischenzeitliche Änderung durch ein anderes Programm stoppt das Speichern.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <div className="item-actions"><button disabled={busy} onClick={() => void work(async () => { await load(); setNotice('Datei neu geladen.'); })}>Datei neu laden</button>
      {opened?.undo_revision_id && <button disabled={busy} onClick={() => void undo()}>Letzte Änderung rückgängig</button>}</div>
    {opened && <><small>Aktueller SHA-256: {opened.sha256}{opened.sha256 !== opened.indexed_sha256 && ' · Suchindex ist älter als die Datei'}</small>
      <label>Notiztext<textarea aria-label="Markdown-Notiztext" value={draft} disabled={busy} onChange={(event) => { setDraft(event.target.value); setPreview(null); }} /></label>
      <button disabled={busy || draft === opened.content} onClick={() => void makePreview()}>Änderungsvorschau erstellen</button>
      {preview && <div><p>Prüfe die Änderung für <strong>{opened.path}</strong>. Bisheriger Hash: {preview.before_sha256}. Neuer Hash: {preview.after_sha256}.</p>
        <pre className="markdown-diff">{preview.diff}</pre>
        <button className="primary" disabled={busy} onClick={() => void save()}>Gezeigte Änderung speichern</button></div>}
    </>}
  </section>;
}
