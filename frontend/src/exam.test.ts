import { describe, expect, it } from 'vitest';
import { examResults, remainingSeconds, repeatExamCards, selectExamCards,
  type ExamCard } from './exam';

const card: ExamCard = {
  id: '1', question: 'Was ist Photosynthese?', reference_answer: 'Ein Prozess.',
  evidence_quote: 'Mit Sonnenlicht entsteht Zucker.', user_answer: 'Antwort',
  source: { path: 'bio.txt', page: null, sha256: 'abc', passage_id: 1 },
};

describe('local exam', () => {
  it('uses answered cards once per normalized question and source hash', () => {
    expect(selectExamCards([
      card, { ...card, id: '2', question: ' WAS ist Photosynthese? ' },
      { ...card, id: '3', user_answer: null },
      { ...card, id: '4', source: { ...card.source, sha256: 'def' } },
    ], 10).map((item) => item.id)).toEqual(['1', '4']);
  });

  it('uses absolute time so a paused tab cannot extend the limit', () => {
    expect(remainingSeconds(60_000, 0)).toBe(60);
    expect(remainingSeconds(60_000, 60_001)).toBe(0);
  });

  it('counts blank answers as incorrect and lists repetition goals', () => {
    const cards = [card, { ...card, id: '2', question: 'Wozu Wasser?' },
      { ...card, id: '3', question: 'Wozu Licht?' }];
    expect(examResults(cards, { '1': 'Gut', '2': '  ', '3': 'Unsicher' },
      { '1': 'correct', '2': 'correct', '3': 'partial' })).toEqual({
      correct: 1, partial: 1, incorrect: 1, unrated: 0,
      repeat: ['Wozu Wasser?', 'Wozu Licht?'],
    });
  });

  it('repeats only partial, incorrect and blank answers', () => {
    const cards = [card, { ...card, id: '2' }, { ...card, id: '3' },
      { ...card, id: '4' }, { ...card, id: '5' }];
    expect(repeatExamCards(cards,
      { '1': 'richtig', '2': 'teilweise', '3': 'falsch', '4': '', '5': 'offen' },
      { '1': 'correct', '2': 'partial', '3': 'incorrect' }).map((item) => item.id))
      .toEqual(['2', '3', '4']);
  });
});
