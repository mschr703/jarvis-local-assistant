import { describe, expect, it } from 'vitest';
import { examCsv } from './examCsv';
import type { ExamCard } from './exam';

const card: ExamCard = {
  id: '1', question: 'Wofür ist Öl nützlich?',
  reference_answer: 'Für Energie; "Wärme".', evidence_quote: 'Öl gibt Wärme.',
  user_answer: 'Frühere Antwort',
  source: { path: 'C:\\Bio;logie.txt', page: 2, sha256: 'abc', passage_id: 7 },
};

describe('exam CSV', () => {
  it('exports source and self grading with UTF-8, separators and quotes', () => {
    const csv = examCsv([card], { '1': 'Wärme\nund Energie' }, { '1': 'partial' });
    expect(csv.startsWith('\uFEFF')).toBe(true);
    expect(csv).toContain('"Wofür ist Öl nützlich?";"Wärme\nund Energie";"Teilweise richtig"');
    expect(csv).toContain('"Für Energie; ""Wärme""."');
    expect(csv).toContain('"C:\\Bio;logie.txt";"2";"abc";"7"');
  });

  it('marks empty answers incorrect and answered but ungraded as ungraded', () => {
    const csv = examCsv([card, { ...card, id: '2' }],
      { '1': '', '2': 'Antwort' }, {});
    expect(csv).toContain('"";"Falsch"');
    expect(csv).toContain('"Antwort";"Nicht bewertet"');
  });

  it('escapes formulas in question, answer, model answer and source metadata', () => {
    const csv = examCsv([{ ...card, question: '=1+1', reference_answer: '@x',
      evidence_quote: '-A', source: { ...card.source, path: ' +SUM(1)',
        sha256: '=hash', passage_id: '-2' } }], { '1': '\t=HYPERLINK("x")' },
    { '1': 'correct' });
    expect(csv).toContain('"\'=1+1"');
    expect(csv).toContain('"\'\t=HYPERLINK(""x"")"');
    expect(csv).toContain('"\'@x"');
    expect(csv).toContain('"\'-A"');
    expect(csv).toContain('"\' +SUM(1)"');
    expect(csv).toContain('"\'=hash"');
    expect(csv).toContain('"\'-2"');
  });
});
