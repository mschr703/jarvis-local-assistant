import { expect, test } from 'vitest';
import { cleanStyleFeedback, essayOutline, validEssayInput, validStyleFeedback, wordCount } from './essay';

test('Essay-Gliederung wird aus Zeilen übernommen und passend zum Wortziel geprüft', () => {
  const two = essayOutline(' Einleitung \r\n Schlussfolgerung \n');
  expect(two).toEqual(['Einleitung', 'Schlussfolgerung']);
  expect(validEssayInput('300', two)).toBe(true);
  expect(validEssayInput('900', two)).toBe(false);
  expect(validEssayInput('', two)).toBe(false);
  expect(validEssayInput('300.5', two)).toBe(false);
  expect(validEssayInput('600', ['Abschnitt', 'abschnitt'])).toBe(false);
  expect(validEssayInput('900', ['Einleitung', 'Analyse', 'Beispiele', 'Fazit'])).toBe(true);
  const five = ['Einleitung', 'Analyse', 'Beispiele', 'Diskussion', 'Fazit'];
  const six = [...five.slice(0, 4), 'Folgen', 'Fazit'];
  expect(validEssayInput('901', five)).toBe(true);
  expect(validEssayInput('901', five.slice(0, 4))).toBe(false);
  expect(validEssayInput('1050', five)).toBe(true);
  expect(validEssayInput('1051', five)).toBe(false);
  expect(validEssayInput('1200', six)).toBe(true);
  expect(validEssayInput('1201', six)).toBe(false);
  expect(validEssayInput('900', six)).toBe(false);
});

test('Wortzahl zählt Umlaute und Bindestrichwörter nach einer Bearbeitung', () => {
  expect(wordCount('Überblick: Europa-Mission, ein Text.')).toBe(4);
  expect(wordCount('')).toBe(0);
  expect(wordCount('Ein Absatz. Noch ein Absatz.')).toBe(5);
});

test('Stilhinweis braucht einen konkreten begrenzten Text', () => {
  expect(cleanStyleFeedback('  Bitte   kurze\nSätze.  ')).toBe('Bitte kurze Sätze.');
  expect(validStyleFeedback('Zu kurz')).toBe(false);
  expect(validStyleFeedback('Bitte kurze Sätze.')).toBe(true);
  expect(validStyleFeedback('x'.repeat(501))).toBe(false);
});
