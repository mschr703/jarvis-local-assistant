export function wordCount(text: string): number {
  return (text.match(/[\p{L}\p{N}]+(?:[-’'][\p{L}\p{N}]+)*/gu) || []).length;
}

export function essayOutline(text: string): string[] {
  return text.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

export function validEssayInput(wordTarget: string, outline: string[]): boolean {
  const words = Number(wordTarget);
  return wordTarget.trim() !== '' && Number.isInteger(words) && words >= 300 && words <= 1200 &&
    outline.length >= 2 && outline.length <= (words > 900 ? 6 : 4) &&
    outline.every((item) => item.length >= 3 && item.length <= 100) &&
    new Set(outline.map((item) => item.toLocaleLowerCase('de-DE'))).size === outline.length &&
    (words <= 600 || outline.length >= 3) && (words <= 750 || outline.length >= 4) &&
    (words <= 900 || outline.length >= 5) && (words <= 1050 || outline.length >= 6);
}

export function cleanStyleFeedback(text: string): string {
  return text.trim().replace(/\s+/gu, ' ');
}

export function validStyleFeedback(text: string): boolean {
  const length = cleanStyleFeedback(text).length;
  return length >= 10 && length <= 500;
}
