export type QualityKind = 'chat' | 'plan' | 'tool' | 'research';
export type QualityItem = {
  kind: QualityKind; evidence_id: string; parent_id: string | null; label: string;
  status: string; verdict: string; source_quality?: string; reason: string;
  expected_result: string; regression_case: number; updated_at: string;
  provider: string | null; cost_usd: number | null; cost_kind: string | null;
  duration_seconds: number | null;
};
export type QualityCategory = {
  total: number; reviewed: number; unreviewed: number;
  verdicts: Record<'achieved' | 'partial' | 'failed' | 'unclear', number>;
  unsupported_sources: number; regression_cases: number;
  measured_duration_seconds: number; measured_duration_count: number;
  reported_api_usd: number; estimated_or_reserved_usd: number;
};
export type QualityOverviewData = {
  categories: Record<QualityKind, QualityCategory>;
  recent: QualityItem[]; regression_cases: QualityItem[];
};

const labels: Record<QualityKind, string> = {
  chat: 'Chatantworten', plan: 'Pläne', tool: 'Werkzeugläufe', research: 'Recherchen',
};
const verdicts: Record<string, string> = {
  achieved: 'Ziel erreicht', partial: 'Teilweise erreicht',
  failed: 'Ziel verfehlt', unclear: 'Unklar',
};

export default function QualityOverview({ data, onEvidence }: {
  data: QualityOverviewData | null; onEvidence: (item: QualityItem) => void;
}) {
  return <section className="card" aria-label="Qualitätsübersicht">
    <h3>Qualitätsübersicht</h3>
    <p>Gezählt werden gespeicherte Einträge und deine ausdrücklich gespeicherten Bewertungen. Ein Plan und seine Werkzeugschritte zählen getrennt. Die Übersicht startet keine Modellanfrage.</p>
    {!data ? <p>Lade Bewertungen…</p> : <>
      <div className="item-list">{(Object.keys(labels) as QualityKind[]).map((kind) => {
        const category = data.categories[kind];
        return <div key={kind} className="item-row"><div>
          <strong>{labels[kind]}: {category.reviewed} von {category.total} bewertet</strong>
          <p>{category.verdicts.achieved} erreicht · {category.verdicts.partial} teilweise · {category.verdicts.failed} verfehlt · {category.verdicts.unclear} unklar · {category.unreviewed} unbewertet</p>
          <small>{category.regression_cases} gesicherte Fehlerfälle{kind !== 'tool' ? ` · ${category.unsupported_sources} Bewertungen mit unzureichenden Quellen` : ''}</small>
          <small>Gemessene {kind === 'tool' ? 'Werkzeugdauer' : 'Anfragedauer'} der Bewertungen: {category.measured_duration_count ? `${category.measured_duration_seconds.toFixed(2)} s bei ${category.measured_duration_count} Einträgen` : 'kein Beleg'}</small>
          {(kind === 'chat' || kind === 'plan') && <small>OpenRouter zugeordnet: {category.reported_api_usd.toFixed(5)} USD gemeldet · {category.estimated_or_reserved_usd.toFixed(5)} USD geschätzt oder reserviert. Fehlende Kostenbelege sind nicht als 0 gezählt.</small>}
        </div></div>;
      })}</div>
      <h4>Gesicherte Fehlerfälle</h4>
      {data.regression_cases.length ? <div className="item-list">{data.regression_cases.map((item) => <div className="item-row" key={`${item.kind}-${item.evidence_id}`}><div>
        <strong>{labels[item.kind]} · {verdicts[item.verdict] || item.verdict}</strong>
        <p>{item.label}</p><small>Beobachtet: {item.reason}</small>
        <small>Erwartet: {item.expected_result}</small>
        <button type="button" onClick={() => onEvidence(item)}>Originalbeleg öffnen</button>
      </div></div>)}</div> : <p>Keine Fehlerfälle gespeichert.</p>}
      <details><summary>Letzte Bewertungen ({data.recent.length})</summary>
        <div className="item-list">{data.recent.map((item) => <div className="item-row" key={`recent-${item.kind}-${item.evidence_id}`}><div>
          <strong>{labels[item.kind]} · {verdicts[item.verdict] || item.verdict}</strong>
          <p>{item.label}</p><small>{item.reason} · {new Date(item.updated_at).toLocaleString('de-DE')}</small>
          <button type="button" onClick={() => onEvidence(item)}>Originalbeleg öffnen</button>
        </div></div>)}</div>
      </details>
    </>}
  </section>;
}
