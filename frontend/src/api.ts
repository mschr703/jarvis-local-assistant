export type Health = { status: string; provider: string; model: string | null; configured: boolean; message: string };
export type ChatSummary = { id: string; title: string; created_at: string; updated_at: string;
  archived_at: string | null; match_preview?: string | null };
export type Message = {
  id: string; role: 'user' | 'assistant'; content: string; status: string; created_at: string;
  provider?: string | null; model?: string | null; input_tokens?: number | null;
  output_tokens?: number | null; cost_usd?: number | null; cost_kind?: string | null;
  compaction_version?: number | null;
  context_estimated_tokens?: number | null; context_token_budget?: number | null;
  context_omitted_count?: number | null; context_latest_omitted_id?: string | null;
  rule_version?: number | null;
  skill_id?: string | null; skill_name?: string | null;
  skill_version?: number | null; skill_hash?: string | null;
  usage_id?: string | null; usage_duration_seconds?: number | null;
  memory_context?: { proposal_id: string; memory_id: string; text_snapshot: string;
    source_quote: string; used_at: string }[];
  evaluation?: { message_id: string; verdict: string; source_quality: string; reason: string;
    regression_case: number; expected_result: string; version: number; updated_at: string } | null;
};
export type Chat = ChatSummary & { messages: Message[] };
export type ChatCompaction = { chat_id: string; version: number;
  covered_message_ids: string[]; entries: { kind: 'goal' | 'decision' | 'open_question' | 'source';
    text: string; message_ids: string[] }[];
  provider: string; model: string;
  created_by: 'ollama' | 'user_remove' | 'user_reset'; created_at: string };

let csrf: string | null = null;

async function ensureSession(): Promise<string> {
  if (csrf) return csrf;
  const response = await fetch('/api/session', { credentials: 'same-origin' });
  if (!response.ok) throw new Error('Lokale Sitzung konnte nicht gestartet werden');
  csrf = (await response.json() as { csrf: string }).csrf;
  return csrf;
}

async function requestWithSession(path: string, options?: RequestInit): Promise<Response> {
  let token = await ensureSession();
  const send = (value: string) => {
    const headers = new Headers(options?.headers);
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    headers.set('X-Jarvis-CSRF', value);
    return fetch(`/api${path}`, { ...options, headers, credentials: 'same-origin' });
  };
  let response = await send(token);
  if (response.status === 401) {
    csrf = null;
    token = await ensureSession();
    response = await send(token);
  }
  return response;
}

async function responseError(response: Response): Promise<Error> {
  const body = await response.json().catch(() => ({})) as { detail?: unknown };
  if (typeof body.detail === 'string' && body.detail.trim()) return new Error(body.detail);
  if (Array.isArray(body.detail)) {
    const messages = body.detail.slice(0, 3).map((item: unknown) => {
      if (!item || typeof item !== 'object') return '';
      const message = (item as { msg?: unknown }).msg;
      return typeof message === 'string' ? message : '';
    }).filter(Boolean);
    if (messages.length) return new Error(`Ungültige Eingabe: ${messages.join('; ')}`);
  }
  return new Error(`HTTP ${response.status}`);
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await requestWithSession(path, options);
  if (!response.ok) throw await responseError(response);
  return response.json() as Promise<T>;
}

export async function transcribeAudio(audio: Blob, signal: AbortSignal): Promise<{
  text: string; audio_seconds: number; stt_seconds: number; model: string;
}> {
  const response = await requestWithSession('/voice/transcribe', {
    method: 'POST', body: audio, signal, headers: { 'Content-Type': audio.type },
  });
  if (!response.ok) throw await responseError(response);
  return response.json();
}

export async function streamChat(
  chatId: string, content: string, signal: AbortSignal, allowCloud: boolean,
  onEvent: (name: string, payload: Record<string, unknown>) => void, skillId: string | null = null,
  memoryProposalIds: string[] = [],
): Promise<void> {
  const response = await requestWithSession(`/chats/${chatId}/stream`, {
    method: 'POST', body: JSON.stringify({ content, allow_cloud: allowCloud, skill_id: skillId,
      memory_proposal_ids: memoryProposalIds }), signal, credentials: 'same-origin',
  });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new Error('Antwortstream fehlt');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = false;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let boundary: number;
    while ((boundary = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const name = block.match(/^event: (.+)$/m)?.[1];
      const data = block.match(/^data: (.+)$/m)?.[1];
      if (!name || !data) continue;
      const payload = JSON.parse(data) as Record<string, unknown>;
      onEvent(name, payload);
      if (name === 'done') completed = true;
      if (name === 'error') throw new Error(String(payload.message || 'Modellfehler'));
    }
  }
  if (!completed) throw new Error('Antwortstream wurde ohne Abschluss beendet');
}
