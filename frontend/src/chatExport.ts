import type { Chat } from './api';

function inline(value: string): string {
  return value.replace(/\s+/g, ' ').trim().replace(/[\\`*_{}\[\]()#+.!|<>-]/g, '\\$&');
}

function fenceFor(value: string): string {
  const runs = value.match(/`+/g) || [];
  return '`'.repeat(Math.max(3, ...runs.map((run) => run.length + 1)));
}

export function chatFilename(chat: Chat): string {
  const date = /^\d{4}-\d{2}-\d{2}/.exec(chat.created_at)?.[0] || 'ohne-datum';
  const id = chat.id.replace(/[^a-zA-Z0-9]/g, '').slice(0, 12) || 'gespraech';
  return `gespraech-${date}-${id}.md`;
}

export function formatChatMarkdown(chat: Chat): string {
  const lines = ['# Gespräch', '', `Titel: ${inline(chat.title)}  `,
    `Erstellt: ${inline(chat.created_at)}  `,
    `Status: ${chat.archived_at ? 'archiviert' : 'aktiv'}`, '',
    'Der Export enthält Nachrichtentext und Anhang-Metadaten, keine Anhangdateien.', ''];
  chat.messages.forEach((message, index) => {
    const content = message.content || '(keine Ausgabe)';
    const fence = fenceFor(content);
    lines.push(`## ${index + 1}. ${message.role === 'user' ? 'Du' : 'Jarvis'}`,
      '', `Zeit: ${inline(message.created_at)}  `,
      `Status: ${inline(message.status)}`, '', fence, content, fence, '');
    for (const attachment of message.attachments || []) {
      lines.push(`Anhang: ${inline(attachment.filename)} · SHA-256: ${inline(attachment.sha256)}`);
    }
    if (message.attachments?.length) lines.push('');
  });
  return `${lines.join('\n').trimEnd()}\n`;
}

export function downloadChatMarkdown(chat: Chat): void {
  const blob = new Blob([formatChatMarkdown(chat)], { type: 'text/markdown;charset=utf-8' });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = chatFilename(chat);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 0);
}
