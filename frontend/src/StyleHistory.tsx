type Policy = 'local_only' | 'cloud_allowed';

export type ProfileVersion = {
  revision: number; description: string; policy: Policy;
  restored_from: number | null; created_at: string;
};

export type ProfileHistory = {
  scope: string; revision: number; versions: ProfileVersion[];
};

export default function StyleHistory({ history, scopeLabel, currentRevision, touched,
  saving, onRestore }: {
  history: ProfileHistory | null; scopeLabel: string; currentRevision: number;
  touched: boolean; saving: boolean; onRestore: (revision: number) => void;
}) {
  return <details className="memory-form">
    <summary>{scopeLabel}: Fassungen und Rückkehr ({history?.versions.length ?? 0})</summary>
    <p className="muted">Aktuelle Revision {currentRevision}. Eine Rückkehr legt eine neue Fassung an und übernimmt auch die damals gewählte Cloud-Freigabe. Ungespeicherte Eingaben vorher speichern oder neu laden.</p>
    <div className="item-list">{history?.versions.map((version) => <div className="item-row" key={version.revision}>
      <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
        <strong>Fassung {version.revision}{version.revision === currentRevision ? ' · aktuell' : ''}</strong>
        <small>{new Date(version.created_at).toLocaleString('de-DE')} · {version.policy === 'local_only' ? 'nur lokal' : 'Cloud erlaubt'}{version.restored_from ? ` · aus Fassung ${version.restored_from} wiederhergestellt` : ''}</small>
        <p>{version.description || 'Keine eigene Vorgabe; allgemeines Profil gilt.'}</p>
      </div>
      {version.revision !== currentRevision && <button disabled={saving || touched}
        onClick={() => onRestore(version.revision)}>{scopeLabel} Fassung {version.revision} wiederherstellen</button>}
    </div>)}</div>
    {history?.versions.length === 0 && <p className="muted">Noch keine gespeicherte Fassung.</p>}
  </details>;
}
