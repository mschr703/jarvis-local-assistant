type MediaType = 'web' | 'pdf' | 'video' | 'github';

export type ResearchReport = {
  id: string; question: string; search_query: string; search_provider: string;
  sources: { id: number; title: string; url: string; text: string; retrieved_at: string;
    published_at?: string | null; found_by_query?: string | null;
    found_by_provider?: string | null; media_type?: MediaType;
    caption_error?: string | null }[];
  claims: { statement: string; evidence: { source_id: number; quote: string }[] }[];
  interpretation: string; uncertainty: string; errors: string[];
  provider: string; model: string; created_at: string;
  parent_report_id?: string | null; open_point?: string | null;
  project_context?: { id: string; project: string; version: number; updated_at: string;
    confirmed: string; open_questions: string; next_step: string; truncated: boolean } | null;
  comparison?: { entries: { source_id: number; title: string; url: string; quote: string;
    retrieved_at: string; published_at?: string | null; found_by_query?: string | null;
    found_by_provider?: string | null; media_type: MediaType }[];
    shared_words: string[]; first_only_words: string[]; second_only_words: string[];
    shared_numbers?: string[]; first_only_numbers?: string[]; second_only_numbers?: string[] } | null;
};

// Escape untrusted source and model text so it cannot create Markdown headings, links or images.
function plain(value: string): string {
  return String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/\\/g, '\\\\').replace(/([`*_{}\[\]()#+.!>|~-])/g, '\\$1')
    .replace(/\r\n?/g, '\n').replace(/\n/g, '  \n');
}

function urlLine(raw: string): string {
  try {
    const url = new URL(raw);
    if (!['https:', 'http:'].includes(url.protocol)) return plain(raw);
    return `[Quelle öffnen](<${url.href.replace(/>/g, '%3E').replace(/</g, '%3C')}>)`;
  } catch {
    return plain(raw);
  }
}

function time(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? plain(value) : date.toLocaleString('de-DE');
}

function publication(value?: string | null): string {
  return value ? ` · Veröffentlichung laut Quelle (ungeprüft): ${plain(value.slice(0, 10).split('-').reverse().join('.'))}` : '';
}

function mediaLabel(value?: MediaType): string {
  return value === 'pdf' ? 'Text-PDF' : value === 'video' ? 'YouTube-Video' :
    value === 'github' ? 'GitHub' : 'Webseite';
}

export function researchFilename(report: ResearchReport): string {
  const date = /^\d{4}-\d{2}-\d{2}/.exec(report.created_at)?.[0] || 'ohne-datum';
  const id = report.id.replace(/[^a-zA-Z0-9]/g, '').slice(0, 12) || 'bericht';
  return `recherche-${date}-${id}.md`;
}

export function formatResearchMarkdown(report: ResearchReport): string {
  const lines = [
    `# Recherchebericht: ${plain(report.question)}`, '',
    `Erstellt: ${time(report.created_at)}  `,
    `Suche: ${plain(report.search_provider)}${report.search_query ? ` · ${plain(report.search_query)}` : ''}  `,
    `Modell: ${plain(report.provider)}/${plain(report.model)}`, '',
  ];
  if (report.parent_report_id) {
    lines.push('## Getrennte Nachrecherche', '',
      `Ursprungsbericht-ID: ${plain(report.parent_report_id)}`, '',
      `Offener Punkt: ${plain(report.open_point || report.question)}`, '',
      'Der Ursprungsbericht wurde durch diesen Nachlauf nicht verändert.', '');
  }
  const project = report.project_context;
  if (project) {
    lines.push('## Lokaler Projektkontext', '',
      `${plain(project.project)} · Arbeitsstand-Version ${project.version} vom ${time(project.updated_at)}.`,
      'Dieser manuell bestätigte Stand ist Kontext, kein öffentlicher Quellenbeleg.', '',
      `Bestätigt: ${plain(project.confirmed || 'Keine Angabe')}`, '',
      `Offene Fragen: ${plain(project.open_questions || 'Keine Angabe')}`, '',
      `Nächster Schritt: ${plain(project.next_step)}`, '');
    if (project.truncated) lines.push('Langer Arbeitsstand für diesen Bericht gekürzt.', '');
  }
  lines.push('## Wörtlich geprüfte Aussagen', '',
    'Die Zitate wurden mit den gelesenen Quelltexten abgeglichen; der Wahrheitsgehalt wurde nicht unabhängig geprüft.', '');
  if (report.claims.length === 0) lines.push('Keine automatisch wortgetreu geprüfte Aussage.', '');
  report.claims.forEach((claim, index) => {
    lines.push(`${index + 1}. ${plain(claim.statement)}`, '');
    claim.evidence.forEach((item) => lines.push(`   - Quelle [${item.source_id}]: „${plain(item.quote)}“`));
    lines.push('');
  });
  if (report.comparison) {
    const comparison = report.comparison;
    lines.push('## Quellen nebeneinander', '',
      'Nur der Wortlaut zweier geprüfter Fundstellen wird verglichen. Gleiche Wörter beweisen keine Übereinstimmung; fehlende Wörter beweisen keinen Widerspruch.', '');
    comparison.entries.forEach((entry) => {
      lines.push(`### [${entry.source_id}] ${plain(entry.title)}`, '',
        `${urlLine(entry.url)} · ${mediaLabel(entry.media_type)} · Abruf: ${time(entry.retrieved_at)}${publication(entry.published_at)}`, '',
        `„${plain(entry.quote)}“`, '');
      if (entry.found_by_query) lines.push(`Gefunden über ${plain(entry.found_by_provider || 'Suche')}: „${plain(entry.found_by_query)}“`, '');
    });
    lines.push(`Wörter in beiden Zitaten: ${plain(comparison.shared_words.join(', ') || 'keine')}`, '',
      `Nur im ersten Zitat: ${plain(comparison.first_only_words.join(', ') || 'keine')}`, '',
      `Nur im zweiten Zitat: ${plain(comparison.second_only_words.join(', ') || 'keine')}`, '');
    if ((comparison.shared_numbers?.length || 0) + (comparison.first_only_numbers?.length || 0) +
        (comparison.second_only_numbers?.length || 0) > 0) {
      lines.push('### Zahlen als Prüfhinweis', '',
        'Die Zahlen stammen nur aus den zwei Zitaten. Verschiedene Zahlen können unterschiedliche Messgrößen oder Zeitpunkte betreffen und sind kein automatisch festgestellter Widerspruch.', '',
        `Zahlen in beiden Zitaten: ${plain(comparison.shared_numbers?.join(', ') || 'keine')}`, '',
        `Nur im ersten Zitat: ${plain(comparison.first_only_numbers?.join(', ') || 'keine')}`, '',
        `Nur im zweiten Zitat: ${plain(comparison.second_only_numbers?.join(', ') || 'keine')}`, '');
    }
  }
  lines.push('## Schlussfolgerung des Modells', '',
    plain(report.interpretation || 'Keine Schlussfolgerung vorhanden.'), '',
    '## Unsicherheit und Grenzen', '',
    plain(report.uncertainty || 'Keine weitere Unsicherheit angegeben; Quellen bitte selbst prüfen.'), '');
  if (report.errors.length) {
    lines.push('## Abruf- und Auswertungsfehler', '');
    report.errors.forEach((error) => lines.push(`- ${plain(error)}`));
    lines.push('');
  }
  lines.push('## Gelesene Quellen', '');
  if (!report.sources.length) lines.push('Keine Quelle erfolgreich gelesen.', '');
  report.sources.forEach((source) => {
    lines.push(`### [${source.id}] ${plain(source.title)}`, '',
      `${urlLine(source.url)} · ${mediaLabel(source.media_type)} · Abruf: ${time(source.retrieved_at)}${publication(source.published_at)}`, '');
    if (source.caption_error) lines.push(`Untertitel nicht verfügbar: ${plain(source.caption_error)}`, '');
    if (source.found_by_query) lines.push(`Gefunden über ${plain(source.found_by_provider || 'Suche')}: „${plain(source.found_by_query)}“`, '');
  });
  return `${lines.join('\n').trimEnd()}\n`;
}

export function downloadResearchMarkdown(report: ResearchReport): void {
  const blob = new Blob([formatResearchMarkdown(report)], { type: 'text/markdown;charset=utf-8' });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = researchFilename(report);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
