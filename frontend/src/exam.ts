export type ExamCard = {
  id: string;
  question: string;
  reference_answer: string;
  evidence_quote: string;
  user_answer: string | null;
  source: { path: string; page: number | null; sha256: string; passage_id: number | string };
};

export type ExamGrade = 'correct' | 'partial' | 'incorrect';

export function selectExamCards(sessions: ExamCard[], requested: number): ExamCard[] {
  const seen = new Set<string>();
  const cards: ExamCard[] = [];
  for (const session of sessions) {
    if (session.user_answer === null) continue;
    const question = session.question.trim().replace(/\s+/gu, ' ').toLocaleLowerCase('de');
    const key = `${question}\0${session.source.sha256}`;
    if (seen.has(key)) continue;
    seen.add(key);
    cards.push(session);
    if (cards.length >= Math.max(1, Math.min(10, requested))) break;
  }
  return cards;
}

export function remainingSeconds(deadline: number, now: number): number {
  return Math.max(0, Math.ceil((deadline - now) / 1000));
}

export function examResults(cards: ExamCard[], answers: Record<string, string>,
  grades: Record<string, ExamGrade | undefined>) {
  const result = { correct: 0, partial: 0, incorrect: 0, unrated: 0,
    repeat: [] as string[] };
  for (const card of cards) {
    const grade = answers[card.id]?.trim() ? grades[card.id] : 'incorrect';
    if (grade) result[grade] += 1;
    else result.unrated += 1;
    if (grade === 'partial' || grade === 'incorrect') result.repeat.push(card.question);
  }
  return result;
}

export function repeatExamCards(cards: ExamCard[], answers: Record<string, string>,
  grades: Record<string, ExamGrade | undefined>): ExamCard[] {
  return cards.filter((card) => {
    if (!answers[card.id]?.trim()) return true;
    return grades[card.id] === 'partial' || grades[card.id] === 'incorrect';
  });
}
