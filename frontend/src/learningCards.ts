export type LearningCardSession = {
  question: string;
  reference_answer: string;
  evidence_quote: string;
  user_answer: string | null;
  source: { path: string; page: number | null; sha256: string; passage_id: number | string };
};

const HEADER = ['Frage', 'Musterantwort', 'Quellenzitat', 'Quelldatei', 'Seite', 'SHA-256', 'Fundstelle'];

function csvCell(value: string): string {
  const safe = /^\s*[=+\-@]/u.test(value) || /^[\t\r\n]/u.test(value) ? `'${value}` : value;
  return `"${safe.replaceAll('"', '""')}"`;
}

export function learningCardsCsv(sessions: LearningCardSession[]): {
  csv: string; count: number; duplicates: number;
} {
  const seen = new Set<string>();
  const rows = [HEADER.map(csvCell).join(';')];
  let duplicates = 0;
  for (const session of sessions) {
    if (session.user_answer === null) continue;
    const question = session.question.trim().replace(/\s+/gu, ' ').toLocaleLowerCase('de');
    const key = `${question}\0${session.source.sha256}`;
    if (seen.has(key)) { duplicates += 1; continue; }
    seen.add(key);
    rows.push([
      session.question, session.reference_answer, session.evidence_quote,
      session.source.path, session.source.page?.toString() ?? '',
      session.source.sha256, String(session.source.passage_id),
    ].map(csvCell).join(';'));
  }
  return { csv: `\uFEFF${rows.join('\r\n')}\r\n`, count: rows.length - 1, duplicates };
}
