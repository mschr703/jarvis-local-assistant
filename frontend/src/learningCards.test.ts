import { describe, expect, it } from 'vitest';
import { learningCardsCsv, type LearningCardSession } from './learningCards';

const source = { path: 'C:\\Beispiele\\Bio;logie.txt', page: 2,
  sha256: 'abc123', passage_id: 'stelle-1' };
const session: LearningCardSession = {
  question: 'Was ist Photosynthese?', reference_answer: 'Licht wird genutzt; "Zucker" entsteht.',
  evidence_quote: 'Mithilfe von Sonnenlicht entsteht Zucker.', user_answer: 'Pflanzen nutzen Licht.',
  source,
};

describe('learningCardsCsv', () => {
  it('exports answered source-backed cards as UTF-8 CSV without duplicate questions', () => {
    const result = learningCardsCsv([
      session,
      { ...session, question: ' WAS  ist Photosynthese? ' },
      { ...session, question: 'Noch offen?', user_answer: null },
    ]);
    expect(result).toMatchObject({ count: 1, duplicates: 1 });
    expect(result.csv.startsWith('\uFEFF')).toBe(true);
    expect(result.csv).toContain('"Licht wird genutzt; ""Zucker"" entsteht."');
    expect(result.csv).toContain('"C:\\Beispiele\\Bio;logie.txt";"2";"abc123";"stelle-1"');
    expect(result.csv.split('\r\n')).toHaveLength(3);
  });

  it('escapes spreadsheet formulas in every user-controlled field', () => {
    const result = learningCardsCsv([{ ...session, question: '=HYPERLINK("bad")',
      reference_answer: ' +SUM(1,2)', evidence_quote: '@cmd',
      source: { ...source, path: '\t=cmd', passage_id: '-5', sha256: 'hash' } }]);
    expect(result.count).toBe(1);
    expect(result.csv).toContain('"\'=HYPERLINK(""bad"")"');
    expect(result.csv).toContain('"\' +SUM(1,2)"');
    expect(result.csv).toContain('"\'@cmd"');
    expect(result.csv).toContain('"\'\t=cmd"');
    expect(result.csv).toContain('"\'-5"');
  });

  it('exports a header only when no round has an answer', () => {
    const result = learningCardsCsv([{ ...session, user_answer: null }]);
    expect(result.count).toBe(0);
    expect(result.duplicates).toBe(0);
    expect(result.csv.split('\r\n')).toHaveLength(2);
  });

  it('preserves umlauts and embedded newlines inside quoted cells', () => {
    const result = learningCardsCsv([{ ...session,
      question: 'Wofür ist Öl nützlich?',
      reference_answer: 'Zeile eins\nZeile zwei; mit Trennzeichen',
      source: { ...source, sha256: 'neu' },
    }]);
    expect(result.count).toBe(1);
    expect(result.csv).toContain('"Wofür ist Öl nützlich?"');
    expect(result.csv).toContain('"Zeile eins\nZeile zwei; mit Trennzeichen"');
  });
});
