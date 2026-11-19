import { type ExamCard, type ExamGrade } from './exam';
import { csvCell } from './learningCards';

const HEADER = ['Frage', 'Meine Antwort', 'Bewertung', 'Musterantwort',
  'Quellenzitat', 'Quelldatei', 'Seite', 'SHA-256', 'Fundstelle'];
const gradeLabel: Record<ExamGrade, string> = {
  correct: 'Richtig', partial: 'Teilweise richtig', incorrect: 'Falsch',
};

export function examCsv(cards: ExamCard[], answers: Record<string, string>,
  grades: Record<string, ExamGrade | undefined>): string {
  const rows = [HEADER.map(csvCell).join(';')];
  for (const card of cards) {
    const answer = answers[card.id] ?? '';
    const grade = answer.trim() ? grades[card.id] : 'incorrect';
    rows.push([
      card.question, answer,
      grade ? gradeLabel[grade] : 'Nicht bewertet',
      card.reference_answer, card.evidence_quote, card.source.path,
      card.source.page?.toString() ?? '', card.source.sha256,
      String(card.source.passage_id),
    ].map(csvCell).join(';'));
  }
  return `\uFEFF${rows.join('\r\n')}\r\n`;
}
