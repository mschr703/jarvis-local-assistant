import type { ResearchReport } from './researchExport';

export type ResearchCaseSnapshot = {
  format_version: 1;
  evaluation: { report_id: string; verdict: string; source_quality: string;
    reason: string; expected_result: string; version: number; regression_case: number;
    created_at: string; updated_at: string };
  report: ResearchReport;
};

export function formatResearchCasesJsonl(cases: ResearchCaseSnapshot[]): string {
  return cases.map((item) => JSON.stringify(item)).join('\n') + (cases.length ? '\n' : '');
}

export function downloadResearchCases(cases: ResearchCaseSnapshot[]): void {
  if (!cases.length) return;
  const blob = new Blob([formatResearchCasesJsonl(cases)], { type: 'application/x-ndjson;charset=utf-8' });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = `recherche-testfaelle-${new Date().toISOString().slice(0, 10)}.jsonl`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
