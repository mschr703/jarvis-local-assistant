const API = 'http://127.0.0.1:8765';
const $ = (id) => document.getElementById(id);
let token = null;
let tabId = null;
let source = null;
let pageText = '';
let requestId = crypto.randomUUID();

function message(text) { $('result').textContent = text; }
function resetRequest() { requestId = crypto.randomUUID(); }

async function post(path, body, bearer) {
  let response;
  try {
    response = await fetch(`${API}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}) },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error('Jarvis ist nicht erreichbar. Starte Jarvis auf diesem Laptop.');
  }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof result.detail === 'string' ? result.detail : `Jarvis meldet HTTP ${response.status}`);
  return result;
}

async function readSelection() {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => window.getSelection()?.toString().slice(0, 5000) || '',
  });
  return result?.result || '';
}

async function readPageText() {
  const [result] = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => (document.body?.innerText || '').slice(0, 20000),
  });
  return result?.result || '';
}

async function prepareCapture() {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  const tabs = await chrome.tabs.query({});
  const tab = /^https?:\/\//i.test(active?.url || '') ? active :
    tabs.filter((item) => /^https?:\/\//i.test(item.url || ''))
      .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))[0];
  if (!tab?.id || !/^https?:\/\//i.test(tab.url || '')) {
    $('status').textContent = 'Dieser Tab ist keine HTTP(S)-Webseite.';
    return;
  }
  tabId = tab.id;
  source = { url: tab.url, source_title: (tab.title || '').slice(0, 300), selection: await readSelection() };
  $('source-title').textContent = source.source_title || 'Webseite';
  $('source-url').textContent = source.url;
  $('selection').value = source.selection;
  $('title').value = `Prüfe: ${source.source_title || 'diese Webseite'}`.slice(0, 300);
  $('capture').hidden = false;
  $('status').textContent = 'Tabdaten werden erst mit „Als Aufgabe speichern“ übertragen.';
}

async function showReady() {
  $('pairing').hidden = true;
  $('capture').hidden = true;
  try { await prepareCapture(); }
  catch { $('status').textContent = 'Aus diesem Tab kann kein Text gelesen werden. Wähle eine normale Webseite.'; }
}

$('pair').addEventListener('click', async () => {
  $('pair').disabled = true;
  message('');
  try {
    const result = await post('/extension/pair', { code: $('code').value.trim() });
    token = result.token;
    await chrome.storage.local.set({ jarvisExtensionToken: token });
    $('code').value = '';
    await showReady();
  } catch (error) { message(error.message); }
  finally { $('pair').disabled = false; }
});

$('include-page').addEventListener('change', async () => {
  resetRequest();
  pageText = '';
  $('page-preview').hidden = true;
  if (!$('include-page').checked) return;
  try {
    pageText = await readPageText();
    $('page-preview').textContent = `${pageText.length} Zeichen: ${pageText.slice(0, 800)}${pageText.length > 800 ? '…' : ''}`;
    $('page-preview').hidden = false;
  } catch {
    $('include-page').checked = false;
    message('Seitentext konnte nicht gelesen werden. Die Tabvorschau bleibt verfügbar.');
  }
});
$('title').addEventListener('input', resetRequest);

$('send').addEventListener('click', async () => {
  if (!source || !$('title').value.trim()) { message('Bitte eine konkrete Aufgabe eingeben.'); return; }
  $('send').disabled = true;
  message('Speichere Aufgabe…');
  try {
    const result = await post('/extension/capture', {
      request_id: requestId, title: $('title').value.trim(), ...source,
      page_text: $('include-page').checked ? pageText : '',
    }, token);
    message(result.created ? 'Aufgabe mit Quellenlink gespeichert.' : 'Diese Aufgabe war bereits gespeichert.');
  } catch (error) {
    message(error.message);
    if (error.message.includes('nicht gekoppelt')) {
      token = null;
      await chrome.storage.local.remove('jarvisExtensionToken');
      $('capture').hidden = true;
      $('pairing').hidden = false;
    }
  } finally { $('send').disabled = false; }
});

(async () => {
  const stored = await chrome.storage.local.get('jarvisExtensionToken');
  token = stored.jarvisExtensionToken || null;
  if (token) await showReady();
  else {
    $('status').textContent = 'Erweiterung noch nicht gekoppelt.';
    $('pairing').hidden = false;
  }
})().catch(() => { $('status').textContent = 'Erweiterung konnte nicht gestartet werden.'; });
