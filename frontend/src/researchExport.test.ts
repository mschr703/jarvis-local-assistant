import { expect, test } from 'vitest';
import { formatResearchMarkdown, researchFilename, type ResearchReport } from './researchExport';

const report: ResearchReport = {
  id: 'abc-123', question: 'Was zeigt #Atlas [hier](https://evil.example)?',
  search_query: 'Europa Clipper', search_provider: 'Direkte URLs',
  provider: 'ollama', model: 'qwen3:4b-instruct', created_at: '2026-10-27T07:00:00Z',
  project_context: { id: 'p1', project: 'Atlas', version: 2,
    updated_at: '2026-10-26T20:00:00Z', confirmed: 'Probe *bereit*',
    open_questions: 'Start?', next_step: 'PDF lesen', truncated: false },
  sources: [
    { id: 1, title: '# Bericht', url: 'https://example.com/report.pdf',
      text: 'Europa Clipper untersucht die Eisoberflaeche.',
      retrieved_at: '2026-10-27T06:59:00Z', found_by_query: 'Eisoberfläche?',
      found_by_provider: 'DuckDuckGo HTML', media_type: 'pdf' },
    { id: 2, title: 'Überblick', url: 'https://example.com/overview',
      text: 'Europa Clipper beobachtet den Jupitermond.',
      retrieved_at: '2026-10-27T06:59:30Z', published_at: '2025-06-14', media_type: 'web' },
  ],
  claims: [{ statement: 'Europa Clipper untersucht Eis.',
    evidence: [{ source_id: 1, quote: 'Europa Clipper untersucht die Eisoberflaeche.' }] }],
  comparison: { entries: [
    { source_id: 1, title: '# Bericht', url: 'https://example.com/report.pdf',
      quote: 'Europa Clipper untersucht die Eisoberflaeche.',
      retrieved_at: '2026-10-27T06:59:00Z', found_by_query: 'Eisoberfläche?',
      found_by_provider: 'DuckDuckGo HTML', media_type: 'pdf' },
    { source_id: 2, title: 'Überblick', url: 'https://example.com/overview',
      quote: 'Europa Clipper beobachtet den Jupitermond.',
      retrieved_at: '2026-10-27T06:59:30Z', published_at: '2025-06-14', media_type: 'web' },
  ], shared_words: ['europa', 'clipper'], first_only_words: ['eisoberflaeche'],
    second_only_words: ['jupitermond'], shared_numbers: ['2024'],
    first_only_numbers: ['12,5 %'], second_only_numbers: ['14 %'] },
  interpretation: 'Die Quellen nennen verschiedene Aspekte. [Falscher Link](https://evil.example) <img src=x onerror=alert(1)>',
  uncertainty: 'Kein unabhängiger Wahrheitscheck.', errors: ['Quelle 3 nicht lesbar.'],
};

test('Rechercheexport enthält Herkunft, Trennung und vollständige Belege', () => {
  const markdown = formatResearchMarkdown(report);
  expect(researchFilename(report)).toBe('recherche-2026-10-27-abc123.md');
  expect(markdown).toContain('Arbeitsstand-Version 2');
  expect(markdown).toContain('kein öffentlicher Quellenbeleg');
  expect(markdown).toContain('Europa Clipper untersucht die Eisoberflaeche');
  expect(markdown).toContain('Wörter in beiden Zitaten: europa, clipper');
  expect(markdown).toContain('fehlende Wörter beweisen keinen Widerspruch');
  expect(markdown).toContain('### Zahlen als Prüfhinweis');
  expect(markdown).toContain('Zahlen in beiden Zitaten: 2024');
  expect(markdown).toContain('12,5 %');
  expect(markdown).toContain('14 %');
  expect(markdown).toContain('[Quelle öffnen](<https://example.com/report.pdf>)');
  expect(markdown).toContain('Gefunden über DuckDuckGo HTML: „Eisoberfläche?');
  expect(markdown).toContain('[Quelle öffnen](<https://example.com/overview>)');
  expect(markdown).toContain('Veröffentlichung laut Quelle (ungeprüft): 14\\.06\\.2025');
  expect(markdown).toContain('## Unsicherheit und Grenzen');
  expect(markdown).toContain('Quelle 3 nicht lesbar');
  expect(markdown).not.toContain('[Falscher Link](https://evil.example)');
  expect(markdown).toContain('\\[Falscher Link\\]\\(https://evil\\.example\\)');
  expect(markdown).not.toContain('<img');
  expect(markdown).toContain('&lt;img');
  expect(markdown).toContain('# Recherchebericht: Was zeigt \\#Atlas');
  expect(markdown).toContain('### [1] \\# Bericht');
});

test('Auch ein partieller Bericht ohne Quellen bleibt exportierbar', () => {
  const markdown = formatResearchMarkdown({ ...report, project_context: null, comparison: null,
    sources: [], claims: [], interpretation: '', uncertainty: '', errors: [] });
  expect(markdown).toContain('Keine Quelle erfolgreich gelesen.');
  expect(markdown).toContain('Keine automatisch wortgetreu geprüfte Aussage.');
  expect(markdown).not.toContain('## Quellen nebeneinander');
  expect(markdown).toContain('Keine Schlussfolgerung vorhanden\\.');
  expect(markdown.endsWith('\n')).toBe(true);
});

test('Nachrechercheexport nennt Ursprung und maskiert den offenen Punkt', () => {
  const markdown = formatResearchMarkdown({ ...report, parent_report_id: 'alt-123',
    open_point: 'Welche #Kamera [hier](https://evil.example)?' });
  expect(markdown).toContain('Ursprungsbericht-ID: alt\\-123');
  expect(markdown).toContain('Offener Punkt: Welche \\#Kamera');
  expect(markdown).not.toContain('[hier](https://evil.example)');
});

test('Video- und GitHub-Quellen behalten Typ und Untertitelfehler im Export', () => {
  const markdown = formatResearchMarkdown({ ...report, comparison: null,
    sources: [
      { id: 1, title: 'Video', url: 'https://www.youtube.com/watch?v=aircAruvnKk',
        text: 'Beschreibung ohne Untertitel', retrieved_at: '2026-10-27T06:59:00Z',
        media_type: 'video', caption_error: 'YouTube-Untertitel: nicht verfügbar' },
      { id: 2, title: 'Projekt', url: 'https://github.com/owner/repo',
        text: 'Öffentlicher README-Text', retrieved_at: '2026-10-27T06:59:30Z',
        media_type: 'github' },
    ] });
  expect(markdown).toContain('YouTube-Video');
  expect(markdown).toContain('Untertitel nicht verfügbar:');
  expect(markdown).toContain('YouTube\\-Untertitel: nicht verfügbar');
  expect(markdown).toContain('GitHub');
  expect(markdown).toContain('https://github.com/owner/repo');
});
