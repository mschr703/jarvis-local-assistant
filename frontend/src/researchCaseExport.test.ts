import { expect, test } from 'vitest';
import { formatResearchCasesJsonl, type ResearchCaseSnapshot } from './researchCaseExport';

test('JSONL bewahrt Umlaute, Zeilen und die Bewertungsversion je Fall', () => {
  const item = {
    format_version: 1,
    evaluation: { report_id: 'r1', verdict: 'failed', source_quality: 'unsupported',
      reason: 'Zitat fehlt\nprüfen', expected_result: 'Änderung ü', version: 2,
      regression_case: 1, created_at: '2026-10-27T08:00:00Z', updated_at: '2026-10-27T08:01:00Z' },
    report: { id: 'r1', question: 'Äpfel?', search_query: 'Äpfel', search_provider: 'Fixture',
      sources: [{ id: 1, title: 'Quelle', url: 'https://example.com', text: 'Zeile\nZwei',
        retrieved_at: '2026-10-27T08:00:00Z' }], claims: [], interpretation: 'Offen',
      uncertainty: '', errors: [], provider: 'ollama', model: 'fixture',
      created_at: '2026-10-27T08:00:00Z' },
  } satisfies ResearchCaseSnapshot;
  const lines = formatResearchCasesJsonl([item, item]).trimEnd().split('\n');
  expect(lines).toHaveLength(2);
  expect(JSON.parse(lines[0])).toEqual(item);
  expect(lines[0]).toContain('Äpfel');
  expect(lines[0]).toContain('Zitat fehlt\\nprüfen');
  expect(formatResearchCasesJsonl([])).toBe('');
});
