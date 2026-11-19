import { expect, test } from 'vitest';
import { chatFilename, formatChatMarkdown } from './chatExport';
import type { Chat } from './api';

const chat: Chat = {
  id: 'abc-123', title: 'Plan [privat](https://example.invalid) <b>',
  created_at: '2026-11-18T06:00:00Z', updated_at: '2026-11-18T06:10:00Z',
  archived_at: '2026-11-18T06:11:00Z', messages: [
    { id: 'm1', role: 'user', status: 'complete', created_at: '2026-11-18T06:01:00Z',
      content: 'Bitte prüfe:\n```markdown\n# Quelle\n```',
      attachments: [{ id: 'file1', filename: 'notiz.txt', sha256: 'a'.repeat(64), size: 12 }] },
    { id: 'm2', role: 'assistant', status: 'complete', created_at: '2026-11-18T06:02:00Z',
      content: 'Ich habe die Quelle gelesen.' },
  ],
};

test('Gesprächsexport bewahrt Wortlaut und grenzt Anhänge ab', () => {
  const markdown = formatChatMarkdown(chat);
  expect(chatFilename(chat)).toBe('gespraech-2026-11-18-abc123.md');
  expect(markdown).toContain('Status: archiviert');
  expect(markdown).toContain('keine Anhangdateien');
  expect(markdown).toContain('````\nBitte prüfe:\n```markdown\n# Quelle\n```\n````');
  expect(markdown).toContain('Ich habe die Quelle gelesen.');
  expect(markdown).toContain(`Anhang: notiz\\.txt · SHA-256: ${'a'.repeat(64)}`);
  expect(markdown).not.toContain('Plan [privat](https://example.invalid) <b>');
  expect(markdown).toContain('Plan \\[privat\\]');
});

test('Leeres Gespräch bleibt exportierbar', () => {
  expect(formatChatMarkdown({ ...chat, messages: [] })).toContain('# Gespräch');
});
