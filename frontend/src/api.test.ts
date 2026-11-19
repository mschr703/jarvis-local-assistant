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
  }, null, [], ['datei-a', 'datei-b']);
  expect(events).toEqual(['delta:Guten ', 'delta:Tag', 'done:done']);
  expect((fetchMock.mock.calls[1][1].headers as Headers).get('X-Jarvis-CSRF')).toBe('token');
  expect(JSON.parse(fetchMock.mock.calls[1][1].body).attachment_ids).toEqual(['datei-a', 'datei-b']);
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

test('Suchdokument wird nur mit geprüftem Original als lokaler Chatanhang vorbereitet', async () => {
  const bytes = new TextEncoder().encode('Ein lokaler Quellenbeleg.');
  const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
    (value) => value.toString(16).padStart(2, '0')).join('');
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path: 'C:\\Belege\\quelle.md',
      size: bytes.length, sha256, status: 'indexed', excluded: 0 }), { status: 200 }))
    .mockResolvedValueOnce(new Response(bytes, { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedDocumentForLocalChat } = await import('./api');
  const file = await verifiedDocumentForLocalChat('dokument-a', 'C:\\Belege\\quelle.md', sha256);
  expect(file.name).toBe('quelle.md');
  expect(await file.text()).toBe('Ein lokaler Quellenbeleg.');
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    '/api/session', '/api/documents/dokument-a', '/api/documents/dokument-a/original',
  ]);
  expect((fetchMock.mock.calls[2][1] as RequestInit).cache).toBe('no-store');
});

test('Suchdokument mit geändertem Index oder zu großem Original wird nicht geladen', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path: 'C:\\Belege\\quelle.txt',
      size: 10, sha256: 'neu', status: 'indexed', excluded: 0 }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path: 'C:\\Belege\\quelle.txt',
      size: 512 * 1024 + 1, sha256: 'alt', status: 'indexed', excluded: 0 }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedDocumentForLocalChat } = await import('./api');
  await expect(verifiedDocumentForLocalChat('dokument-a', 'C:\\Belege\\quelle.txt', 'alt')).rejects.toThrow('nicht mehr aktuell');
  await expect(verifiedDocumentForLocalChat('dokument-a', 'C:\\Belege\\quelle.txt', 'alt')).rejects.toThrow('512 KiB');
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test('Suchdokument mit verändertem Original wird trotz HTTP 200 abgelehnt', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path: '/belege/quelle.txt',
      size: 4, sha256: 'alt', status: 'indexed', excluded: 0 }), { status: 200 }))
    .mockResolvedValueOnce(new Response('neu!', { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedDocumentForLocalChat } = await import('./api');
  await expect(verifiedDocumentForLocalChat('dokument-a', '/belege/quelle.txt', 'alt')).rejects.toThrow('stimmt nicht mehr');
});

test('Suchdokument mit anderem Pfad wird auch bei gleichem Hash nicht übernommen', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path: '/belege/andere.txt',
      size: 4, sha256: 'gleich', status: 'indexed', excluded: 0 }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedDocumentForLocalChat } = await import('./api');
  await expect(verifiedDocumentForLocalChat('dokument-a', '/belege/quelle.txt', 'gleich'))
    .rejects.toThrow('nicht mehr aktuell');
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test('Unterlagenpaket lädt Binärdaten mit lokaler Sitzung und expliziter Auswahl', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response('ZIP-BYTES', { status: 200,
      headers: { 'Content-Type': 'application/zip' } }));
  vi.stubGlobal('fetch', fetchMock);
  const { downloadDocumentPackage } = await import('./api');
  expect(await (await downloadDocumentPackage(['a', 'b'])).text()).toBe('ZIP-BYTES');
  expect(fetchMock.mock.calls[1][0]).toBe('/api/documents/package');
  expect(fetchMock.mock.calls[1][1].method).toBe('POST');
  expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ document_ids: ['a', 'b'] });
  expect((fetchMock.mock.calls[1][1].headers as Headers).get('X-Jarvis-CSRF')).toBe('token');
});

test('Unterlagenpaket zeigt einen veralteten Originalbeleg als Fehler', async () => {
  vi.stubGlobal('fetch', vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      detail: 'Originaldatei weicht vom Index ab; zuerst neu indexieren',
    }), { status: 409 })));
  const { downloadDocumentPackage } = await import('./api');
  await expect(downloadDocumentPackage(['a'])).rejects.toThrow('Originaldatei weicht');
});

test('PDF-Indextext wird nur bei aktuellem Original als Download angenommen', async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response('=== Seite 1 ===\nOrion', { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      detail: 'Originaldatei weicht vom Index ab; zuerst neu indexieren',
    }), { status: 409 }));
  vi.stubGlobal('fetch', fetchMock);
  const { downloadPdfIndexText } = await import('./api');
  expect(await (await downloadPdfIndexText('id mit leerzeichen')).text()).toContain('Orion');
  await expect(downloadPdfIndexText('id mit leerzeichen')).rejects.toThrow('neu indexieren');
  expect(fetchMock.mock.calls[1][0]).toBe('/api/documents/id%20mit%20leerzeichen/index-text');
  expect((fetchMock.mock.calls[1][1] as RequestInit).cache).toBe('no-store');
});

test('Aktueller Scan-PDF-Indextext wird mit Seitenbeleg als lokaler TXT-Chatanhang vorbereitet', async () => {
  const path = 'C:\\Belege\\Scan.pdf';
  const sha256 = 'a'.repeat(64);
  const content = `Jarvis Local · geprüfter PDF-Indextext\nOriginaldatei: Scan.pdf\nDokument-ID: scan-1\nOriginal SHA-256: ${sha256}\n\n=== Seite 1 ===\nSaturn Vertrag`;
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      path, sha256, status: 'indexed', excluded: 0,
    }), { status: 200 }))
    .mockResolvedValueOnce(new Response(content, { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedSearchDocumentForLocalChat } = await import('./api');
  const file = await verifiedSearchDocumentForLocalChat('scan-1', path, sha256);
  expect(file.name).toBe('Scan-indextext.txt');
  expect(await file.text()).toContain('=== Seite 1 ===\nSaturn Vertrag');
  expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
    '/api/session', '/api/documents/scan-1', '/api/documents/scan-1/index-text',
  ]);
});

test('Veralteter oder ausgetauschter PDF-Indextext wird nicht in den Chat übernommen', async () => {
  const path = 'C:\\Belege\\Scan.pdf';
  const sha256 = 'a'.repeat(64);
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      path, sha256: 'neu', status: 'indexed', excluded: 0,
    }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      path, sha256, status: 'indexed', excluded: 0,
    }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      detail: 'Originaldatei weicht vom Index ab; zuerst neu indexieren',
    }), { status: 409 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedSearchDocumentForLocalChat } = await import('./api');
  await expect(verifiedSearchDocumentForLocalChat('scan-1', path, sha256))
    .rejects.toThrow('nicht mehr aktuell');
  await expect(verifiedSearchDocumentForLocalChat('scan-1', path, sha256))
    .rejects.toThrow('Originaldatei weicht');
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

test('PDF-Indextext mit fremdem Beleg oder mehr als 8.000 Zeichen wird abgelehnt', async () => {
  const path = 'C:\\Belege\\Scan.pdf';
  const sha256 = 'a'.repeat(64);
  const good = `Jarvis Local · geprüfter PDF-Indextext\nOriginaldatei: Scan.pdf\nDokument-ID: scan-1\nOriginal SHA-256: ${sha256}\n`;
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path, sha256, status: 'indexed', excluded: 0 }), { status: 200 }))
    .mockResolvedValueOnce(new Response(good.replace(sha256, 'b'.repeat(64)), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ path, sha256, status: 'indexed', excluded: 0 }), { status: 200 }))
    .mockResolvedValueOnce(new Response(good + '\n=== Seite 1 ===\n' + 'x'.repeat(8000), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedSearchDocumentForLocalChat } = await import('./api');
  await expect(verifiedSearchDocumentForLocalChat('scan-1', path, sha256))
    .rejects.toThrow('passt nicht mehr');
  await expect(verifiedSearchDocumentForLocalChat('scan-1', path, sha256))
    .rejects.toThrow('8.000 Zeichen');
});

test('Geprüfte PDF-Fundstelle wird als einzelner lokaler Chatanhang mit Seite vorbereitet', async () => {
  const path = 'C:\\Belege\\Langer Bericht.pdf';
  const sha256 = 'a'.repeat(64);
  const payload = { document_id: 'doc-1', passage_id: 42,
    filename: 'Langer Bericht.pdf', sha256, page: 7, text: 'Orion Vertrag' };
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(payload), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedPdfPassageForLocalChat } = await import('./api');
  const file = await verifiedPdfPassageForLocalChat('doc-1', 42, path, sha256);
  expect(file.name).toBe('Langer Bericht-fundstelle-42.txt');
  expect(await file.text()).toContain('=== Seite 7 ===\nOrion Vertrag');
  expect(await file.text()).not.toContain('C:\\Belege');
  expect(fetchMock.mock.calls[1][0]).toBe(
    `/api/documents/doc-1/passages/42/chat-text?expected_sha256=${sha256}`);
  expect((fetchMock.mock.calls[1][1] as RequestInit).cache).toBe('no-store');
});

test('Fremde oder veraltete PDF-Fundstelle wird vor dem Chat abgewiesen', async () => {
  const path = 'C:\\Belege\\Scan.pdf';
  const sha256 = 'a'.repeat(64);
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ document_id: 'doc-1',
      passage_id: 3, filename: 'Scan.pdf', sha256: 'b'.repeat(64),
      page: 1, text: 'Orion' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      detail: 'Originaldatei weicht vom Index ab; zuerst neu indexieren',
    }), { status: 409 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedPdfPassageForLocalChat } = await import('./api');
  await expect(verifiedPdfPassageForLocalChat('doc-1', 3, path, sha256))
    .rejects.toThrow('passt nicht mehr');
  await expect(verifiedPdfPassageForLocalChat('doc-1', 3, path, sha256))
    .rejects.toThrow('Originaldatei weicht');
  await expect(verifiedPdfPassageForLocalChat('doc-1', 0, path, sha256))
    .rejects.toThrow('nicht geeignet');
  expect(fetchMock).toHaveBeenCalledTimes(3);
});

test('Drei PDF-Fundstellen werden erst nach gemeinsamer Prüfung übergeben', async () => {
  const sha256 = 'a'.repeat(64);
  const path = 'C:\\Belege\\Bericht.pdf';
  const passages = [1, 2, 3].map((passageId) => ({
    documentId: 'doc-1', passageId, path, sha256,
  }));
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }));
  for (const passageId of [1, 2, 3])
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({
      document_id: 'doc-1', passage_id: passageId, filename: 'Bericht.pdf',
      sha256, page: passageId, text: `Orion Seite ${passageId}`,
    }), { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedPdfPassagesForLocalChat } = await import('./api');
  const files = await verifiedPdfPassagesForLocalChat(passages);
  expect(files.map((file) => file.name)).toEqual([
    'Bericht-fundstelle-1.txt', 'Bericht-fundstelle-2.txt', 'Bericht-fundstelle-3.txt',
  ]);
  expect(await files[2].text()).toContain('=== Seite 3 ===\nOrion Seite 3');
  expect(fetchMock).toHaveBeenCalledTimes(4);
  await expect(verifiedPdfPassagesForLocalChat([...passages, passages[0]]))
    .rejects.toThrow('Höchstens drei');
  await expect(verifiedPdfPassagesForLocalChat([passages[0], passages[0]]))
    .rejects.toThrow('verschiedene');
  expect(fetchMock).toHaveBeenCalledTimes(4);
});

test('Fehler bei zweiter PDF-Fundstelle verhindert eine Teilauswahl', async () => {
  const sha256 = 'a'.repeat(64);
  const path = 'C:\\Belege\\Bericht.pdf';
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ csrf: 'token' }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({
      document_id: 'doc-1', passage_id: 1, filename: 'Bericht.pdf',
      sha256, page: 1, text: 'Orion Seite 1',
    }), { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Original geändert' }),
      { status: 409 }));
  vi.stubGlobal('fetch', fetchMock);
  const { verifiedPdfPassagesForLocalChat } = await import('./api');
  await expect(verifiedPdfPassagesForLocalChat([1, 2].map((passageId) => ({
    documentId: 'doc-1', passageId, path, sha256,
  })))).rejects.toThrow('Original geändert');
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
