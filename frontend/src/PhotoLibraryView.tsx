import { useEffect, useState } from 'react';
import { api, downloadMemoryAlbum } from './api';

export type TimelineEventOption = { id: string; title: string; date_local: string };
type Folder = { id: string; path: string; created_at: string };
type Photo = { id: string; name: string; size: number; sha256: string; media_type: string;
  event_id: string | null; event_title: string | null; event_date: string | null;
  version: number; indexed_at: string; status: 'current' | 'changed' | 'unavailable' };

function PhotoRow({ photo, events, busy, onChange }: { photo: Photo;
  events: TimelineEventOption[]; busy: boolean;
  onChange: (photo: Photo, eventId: string | null) => Promise<void> }) {
  const [selection, setSelection] = useState(photo.event_id ?? '');
  const [previewFailed, setPreviewFailed] = useState(false);
  useEffect(() => setSelection(photo.event_id ?? ''), [photo.event_id, photo.version]);
  useEffect(() => setPreviewFailed(false), [photo.sha256, photo.version]);
  return <div className="photo-item">
    {photo.status === 'current' && !previewFailed
      ? <img src={`/api/photo-assets/${encodeURIComponent(photo.id)}/preview`}
          alt={`Vorschau von ${photo.name}`} loading="lazy"
          onError={() => setPreviewFailed(true)} />
      : <div className="photo-placeholder" role="status">{previewFailed ? 'Vorschau nicht verfügbar; Quelle prüfen' :
        `Original ${photo.status === 'changed' ? 'geändert' : 'nicht verfügbar'}`}</div>}
    <strong>{photo.name}</strong>
    <small>{photo.status === 'current' ? 'Größe und Zeit unverändert; Vorschau prüft Inhalt' : 'Neu einlesen oder Quelle prüfen'} · {Math.round(photo.size / 1024)} KiB</small>
    <label>Ereignis
      <select aria-label={`Ereignis für ${photo.name}`} value={selection}
        onChange={(event) => setSelection(event.target.value)}>
        <option value="">Nicht zugeordnet</option>
        {photo.event_id && !events.some((event) => event.id === photo.event_id) &&
          <option value={photo.event_id}>{photo.event_date} · {photo.event_title}</option>}
        {events.map((event) => <option key={event.id} value={event.id}>{event.date_local} · {event.title}</option>)}
      </select>
    </label>
    <button disabled={busy || selection === (photo.event_id ?? '') ||
      (Boolean(selection) && photo.status !== 'current')}
      onClick={() => void onChange(photo, selection || null)}>Zuordnung speichern</button>
  </div>;
}

export default function PhotoLibraryView({ events }: { events: TimelineEventOption[] }) {
  const [folder, setFolder] = useState<Folder | null>(null);
  const [photos, setPhotos] = useState<Photo[]>([]);
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');

  const refresh = async () => {
    const [nextFolder, nextPhotos] = await Promise.all([
      api<Folder | null>('/photo-folder'), api<Photo[]>('/photo-assets'),
    ]);
    setFolder(nextFolder); setPhotos(nextPhotos);
  };
  useEffect(() => { void refresh().catch((error: Error) => setNotice(error.message)); }, [events]);

  const run = async (action: () => Promise<string>) => {
    if (busy) return;
    setBusy(true); setNotice('');
    try { const message = await action(); await refresh(); setNotice(message); }
    catch (error) { setNotice((error as Error).message); await refresh().catch(() => undefined); }
    finally { setBusy(false); }
  };
  const addFolder = () => run(async () => {
    await api('/photo-folder', { method: 'POST', body: JSON.stringify({ path: path.trim() }) });
    setPath('');
    return 'Fotoordner freigegeben. Wähle jetzt „Fotos einlesen“.';
  });
  const index = () => run(async () => {
    if (!folder) return '';
    const result = await api<{ indexed: number; unchanged: number; skipped: number;
      limited: boolean }>(`/photo-folder/${folder.id}/index`, { method: 'POST' });
    return `${result.indexed} Fotos eingelesen, ${result.unchanged} unverändert, ${result.skipped} übersprungen.${result.limited ? ' Grenze von 300 Fotos erreicht.' : ''}`;
  });
  const remove = () => {
    if (!folder || !window.confirm('Fotoordnerfreigabe und Zuordnungen entfernen? Die Originaldateien bleiben erhalten.')) return;
    void run(async () => {
      await api(`/photo-folder/${folder.id}`, { method: 'DELETE' });
      return 'Fotoordnerfreigabe entfernt. Originaldateien unverändert.';
    });
  };
  const link = (photo: Photo, eventId: string | null) => run(async () => {
    await api(`/photo-assets/${photo.id}/event`, { method: 'PUT',
      body: JSON.stringify({ event_id: eventId, expected_version: photo.version }) });
    return eventId ? 'Foto dem Ereignis zugeordnet.' : 'Fotozuordnung gelöst.';
  });
  const exportAlbum = (eventId: string) => run(async () => {
    const blob = await downloadMemoryAlbum(eventId);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url; link.download = 'lebensalbum.zip';
    document.body.appendChild(link); link.click(); link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return 'Lebensalbum mit Originalfotos heruntergeladen.';
  });

  const linked = photos.filter((photo) => photo.event_id);
  const unlinked = photos.filter((photo) => !photo.event_id);
  const albums = Array.from(new Map(linked.map((photo) => [photo.event_id,
    { id: photo.event_id!, title: photo.event_title, date: photo.event_date }])).values());
  return <section className="card photo-library">
    <h3>Ausgewählte Fotos</h3>
    <p>Gib einen Fotoordner auf diesem Laptop ausdrücklich frei. Jarvis liest höchstens 300 JPEG-, PNG- oder WebP-Dateien direkt aus diesem Ordner, jeweils bis 15 MiB. Originale bleiben unverändert; du ordnest Fotos einzeln einem Ereignis zu.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    {folder ? <div className="memory-form"><strong>{folder.path}</strong>
      <div className="item-actions"><button disabled={busy} onClick={() => void index()}>Fotos einlesen</button>
        <button disabled={busy} onClick={() => void refresh().catch((error: Error) => setNotice(error.message))}>Status aktualisieren</button>
        <button disabled={busy} onClick={remove}>Fotoordnerfreigabe entfernen</button></div>
      <small>Eine geänderte Datei wird vor der Vorschau oder Zuordnung abgewiesen. Nach erneutem Einlesen musst du eine geänderte Datei bewusst neu zuordnen.</small>
    </div> : <div className="memory-form">
      <input aria-label="Absoluter Fotoordnerpfad" value={path}
        onChange={(event) => setPath(event.target.value)} placeholder="C:\\Pfad\\zu\\Fotos" />
      <button className="primary" disabled={busy || !path.trim()} onClick={() => void addFolder()}>Fotoordner freigeben</button>
    </div>}
    {linked.length > 0 && <><h4>Alben nach Ereignis</h4>
      {albums.map((event) =>
        <div key={event.id}><h5>{event.date} · {event.title}</h5>
          <button disabled={busy} onClick={() => void exportAlbum(event.id)}>Album als ZIP herunterladen</button>
          <div className="photo-grid">{linked.filter((photo) => photo.event_id === event.id)
            .map((photo) => <PhotoRow key={photo.id} photo={photo} events={events}
              busy={busy} onChange={link} />)}</div></div>)}
    </>}
    {unlinked.length > 0 && <><h4>Noch nicht zugeordnet</h4><div className="photo-grid">
      {unlinked.map((photo) => <PhotoRow key={photo.id} photo={photo} events={events}
        busy={busy} onChange={link} />)}</div></>}
    {folder && !photos.length && <p>Noch keine Fotos eingelesen.</p>}
  </section>;
}
