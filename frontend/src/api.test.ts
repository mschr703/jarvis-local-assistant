import { afterEach, expect, test, vi } from 'vitest';

afterEach(() => { vi.unstubAllGlobals(); vi.resetModules(); });

test('streamChat liefert Deltas und Abschluss in der richtigen Reihenfolge', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(
      'event: delta\ndata: {"text":"Guten "}\n\nevent: delta\ndata: {"text":"Tag"}\n\nevent: done\ndata: {"message":{"content":"Guten Tag"}}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
  vi.stubGlobal('fetch', fetchMock);
  const { streamChat } = await import('./api');
  const events: string[] = [];
  await streamChat('chat-id', 'Hallo', new AbortController().signal, false, (name, payload) => {
    events.push(`${name}:${name === 'delta' ? payload.text : 'done'}`);
  });
  expect(events).toEqual(['delta:Guten ', 'delta:Tag', 'done:done']);
  expect((fetchMock.mock.calls[1][1].headers as Headers).get('X-Jarvis-CSRF')).toBe('token');
});

test('streamChat meldet einen fehlenden Abschluss als Fehler', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response('event: delta\ndata: {"text":"Teil"}\n\n', { status: 200 })));
  const { streamChat } = await import('./api');
  await expect(streamChat('chat-id', 'Hallo', new AbortController().signal, false, () => undefined))
    .rejects.toThrow('ohne Abschluss');
});

test('API erneuert die lokale Sitzung nach Backend-Neustart genau einmal', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'alt' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Lokale Sitzung erforderlich' }), { status: 401 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'neu' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify([]), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { api } = await import('./api');
  expect(await api<unknown[]>('/chats')).toEqual([]);
  expect(fetchMock).toHaveBeenCalledTimes(4);
  expect((fetchMock.mock.calls[3][1].headers as Headers).get('X-Jarvis-CSRF')).toBe('neu');
});

test('API zeigt Validierungsfehler als lesbare Meldung', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: [
      { loc: ['body', 'estimate_minutes'], msg: 'Input should be greater than or equal to 5' },
    ] }), { status: 422 })));
  const { api } = await import('./api');
  await expect(api('/evening-plans/preview', { method: 'POST', body: '{}' }))
    .rejects.toThrow('Ungültige Eingabe: Input should be greater than or equal to 5');
});
