import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { api, streamChat, transcribeAudio, uploadChatAttachment, type Chat, type ChatAttachment,
  type ChatCompaction, type ChatSummary, type Message } from './api';
import type { Skill } from './SkillView';

type Props = {
  configured: boolean; setupMessage: string; provider: string;
  draft: string; setDraft: Dispatch<SetStateAction<string>>;
  focusOnCapture?: number;
  selectedSkillId: string; setSelectedSkillId: (id: string) => void; onSkills: () => void;
  onPrepareTask: (title: string) => void; onPrepareResearch: (question: string) => void;
  requestedChatSource?: { chatId: string; messageId: string } | null;
  onSourceOpened?: () => void;
};
type FollowUpSuggestion = { kind: 'task' | 'research'; text: string };
type MemoryChoice = { id: string; status: string; decided_text: string | null;
  current_memory_text: string | null; source_quote: string; validity_state: string };
type AnswerCase = { message_id: string; chat_id: string; question: string | null; answer: string;
  expected_result: string; reason: string; verdict: string; source_quality: string;
  provider: string | null; model: string | null;
  rule_version: number | null; cost_usd: number | null; cost_kind: string | null;
  skill_id: string | null; skill_name: string | null;
  skill_version: number | null; skill_hash: string | null };
const verdictLabels: Record<string, string> = { achieved: 'Ziel erreicht', partial: 'Teilweise erreicht', failed: 'Ziel verfehlt', unclear: 'Unklar' };
const sourceLabels: Record<string, string> = { supported: 'Quellen gestützt', unsupported: 'Quellen unzureichend', not_applicable: 'Keine Quelle nötig', unclear: 'Quellen unklar' };
const isImageFile = (filename: string) => /\.(png|jpe?g|webp)$/i.test(filename);
const compactionLabels: Record<string, string> = { goal: 'Ziel', decision: 'Entscheidung', open_question: 'Offener Punkt', source: 'Quelle' };

function Usage({ message }: { message: Message }) {
  if (message.role !== 'assistant') return null;
  const tokens = message.input_tokens != null || message.output_tokens != null;
  return <small className="usage">
    {message.model || 'Modell'} · {message.status}
    {message.rule_version != null ? ` · Chat-Regel v${message.rule_version}` : ' · alte Antwort ohne Regelbeleg'}
    {message.compaction_version != null ? ` · Gesprächszustand v${message.compaction_version}` : ' · ohne Gesprächszustand'}
    {message.context_estimated_tokens != null && ` · Kontext geschätzt ${message.context_estimated_tokens}/${message.context_token_budget} Token`}
    {(message.context_omitted_count ?? 0) > 0 && <> · {message.context_omitted_count} ältere Rohbeiträge ausgelassen{message.context_latest_omitted_id && <> · <button type="button" onClick={() => document.getElementById(`chat-message-${message.context_latest_omitted_id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>Letzten ausgelassenen Beitrag zeigen</button></>}</>}
    {message.skill_id ? ` · Skill v${message.skill_version} · ${message.skill_name || message.skill_id} (${message.skill_hash?.slice(0, 12)}…)` : ' · kein Skill'}
    {tokens && ` · ${message.input_tokens ?? '?'} Eingabe / ${message.output_tokens ?? '?'} Ausgabe`}
    {message.usage_duration_seconds != null ? ` · Anfrage ${message.usage_duration_seconds.toFixed(2)} s` : ' · Anfragedauer ohne Beleg'}
    {message.cost_kind === 'local_no_api_charge' ? ' · keine API-Gebühr (lokal)' :
      message.cost_usd != null ? ` · ${message.cost_usd.toFixed(5)} USD (${message.cost_kind || 'gemeldet'})` :
        ' · Kosten unbekannt'}
  </small>;
}

function CompactionState({ snapshot, onRemove }: { snapshot: ChatCompaction;
  onRemove?: (index: number) => void }) {
  const origin = snapshot.created_by === 'user_remove' ? 'von dir korrigiert' :
    snapshot.created_by === 'user_reset' ? 'von dir zurückgesetzt' : `Ollama · ${snapshot.model}`;
  return <div className="compaction-state"><small>Fassung {snapshot.version} · {snapshot.covered_message_ids.length} ältere Nachrichten · {origin} · {new Date(snapshot.created_at).toLocaleString('de-DE')}</small>
    {snapshot.entries.length === 0 && <p>Dieser Zustand wurde zurückgesetzt. Beim nächsten lokalen Chat werden wieder nur rohe Beiträge verwendet.</p>}
    <div className="item-list">{snapshot.entries.map((entry, index) =>
      <div className="item-row" key={`${entry.kind}-${index}`}><div><strong>{compactionLabels[entry.kind] || entry.kind}</strong><p>{entry.text}</p>
        <div className="item-actions">{entry.message_ids.map((id) => <button key={id} onClick={() => document.getElementById(`chat-message-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>Quelle {id.slice(0, 8)} anzeigen</button>)}
          {onRemove && snapshot.entries.length > 1 && <button onClick={() => onRemove(index)}>Diesen Punkt entfernen</button>}</div>
      </div></div>)}</div></div>;
}

export default function ChatView({ configured, setupMessage, provider, draft, setDraft,
  focusOnCapture = 0, selectedSkillId, setSelectedSkillId, onSkills,
  onPrepareTask, onPrepareResearch, requestedChatSource, onSourceOpened }: Props) {
  const [chats, setChats] = useState<ChatSummary[]>([]);
  const [searchText, setSearchText] = useState('');
  const [appliedSearch, setAppliedSearch] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [managingChat, setManagingChat] = useState('');
  const [renamingChat, setRenamingChat] = useState('');
  const [renameText, setRenameText] = useState('');
  const [deleteCandidate, setDeleteCandidate] = useState<ChatSummary | null>(null);
  const [suggestions, setSuggestions] = useState<FollowUpSuggestion[]>([]);
  const [suggestionMessageId, setSuggestionMessageId] = useState('');
  const [suggestionBusy, setSuggestionBusy] = useState(false);
  const [suggestionNotice, setSuggestionNotice] = useState('');
  const [memoryChoices, setMemoryChoices] = useState<MemoryChoice[]>([]);
  const [selectedMemoryIds, setSelectedMemoryIds] = useState<string[]>([]);
  const [compactions, setCompactions] = useState<ChatCompaction[]>([]);
  const [compactionBusy, setCompactionBusy] = useState(false);
  const [compactionNotice, setCompactionNotice] = useState('');
  const [resetCandidate, setResetCandidate] = useState(false);
  const [active, setActive] = useState<Chat | null>(null);
  const [streamed, setStreamed] = useState('');
  const [busy, setBusy] = useState(false);
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [imagePreview, setImagePreview] = useState('');
  const selectedImage = selectedFiles.find((file) => isImageFile(file.name));
  useEffect(() => {
    if (!selectedImage) { setImagePreview(''); return; }
    const url = URL.createObjectURL(selectedImage);
    setImagePreview(url);
    return () => URL.revokeObjectURL(url);
  }, [selectedImage]);
  const [navigating, setNavigating] = useState(false);
  const [error, setError] = useState('');
  const [speechBusyId, setSpeechBusyId] = useState('');
  const [speechActiveId, setSpeechActiveId] = useState('');
  const [speechUrl, setSpeechUrl] = useState('');
  const [speechVoice, setSpeechVoice] = useState('');
  const [speechError, setSpeechError] = useState('');
  const [speechErrorId, setSpeechErrorId] = useState('');
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const speechRequest = useRef(0);
  const speechAbort = useRef<AbortController | null>(null);
  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState('Aus');
  const [voiceTranscript, setVoiceTranscript] = useState('');
  const [voiceError, setVoiceError] = useState('');
  const [voiceLatency, setVoiceLatency] = useState('');
  const voiceRecorder = useRef<MediaRecorder | null>(null);
  const voiceRecordTimeout = useRef<number | null>(null);
  const voiceStream = useRef<MediaStream | null>(null);
  const voiceAbort = useRef<AbortController | null>(null);
  const voiceEpoch = useRef(0);
  const [allowCloud, setAllowCloud] = useState(false);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [selectedAnswerId, setSelectedAnswerId] = useState('');
  const [verdict, setVerdict] = useState('unclear');
  const [sourceQuality, setSourceQuality] = useState('unclear');
  const [reason, setReason] = useState('');
  const [regressionCase, setRegressionCase] = useState(false);
  const [expectedResult, setExpectedResult] = useState('');
  const [evaluationVersion, setEvaluationVersion] = useState(0);
  const [evaluationBusy, setEvaluationBusy] = useState(false);
  const [evaluationNotice, setEvaluationNotice] = useState('');
  const [cases, setCases] = useState<AnswerCase[]>([]);
  const controller = useRef<AbortController | null>(null);
  const navigationRef = useRef(false);
  const evaluationAction = useRef(false);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const latestMessage = active?.messages[active.messages.length - 1];
  const hasChatAttachments = active?.messages.some((message) =>
    (message.attachments?.length ?? 0) > 0) || false;
  const latestAnswer = latestMessage?.role === 'assistant' && latestMessage.status === 'complete'
    ? latestMessage : null;
  useEffect(() => {
    ++speechRequest.current;
    audioRef.current?.pause(); setSpeechActiveId(''); setSpeechUrl('');
    setSpeechError(''); setSpeechErrorId(''); setSpeechBusyId('');
  }, [active?.id]);

  const readAnswer = async (message: Message, chatId = active?.id) => {
    if (!chatId) return false;
    if (speechActiveId === message.id && speechUrl) {
      try { await audioRef.current?.play(); return true; }
      catch { setSpeechError('Wiedergabe blockiert. Bitte den Play-Knopf im Audioplayer wählen.');
        return false; }
    }
    audioRef.current?.pause(); speechAbort.current?.abort();
    const requestId = ++speechRequest.current;
    const abort = new AbortController(); speechAbort.current = abort;
    setSpeechBusyId(message.id); setSpeechError(''); setSpeechActiveId(''); setSpeechUrl('');
    try {
      const result = await api<{ audio_url: string; voice: string; model: string }>(
        `/chats/${chatId}/messages/${message.id}/speech`, { method: 'POST', signal: abort.signal });
      if (requestId !== speechRequest.current) return;
      setSpeechVoice(`${result.voice} · ${result.model}`);
      setSpeechActiveId(message.id); setSpeechUrl(`/api${result.audio_url}`);
      return true;
    } catch (cause) {
      if (requestId === speechRequest.current && !abort.signal.aborted) {
        setSpeechError((cause as Error).message); setSpeechErrorId(message.id);
      }
      return false;
    } finally { if (requestId === speechRequest.current) setSpeechBusyId('');
      if (speechAbort.current === abort) speechAbort.current = null; }
  };

  const stopSpeech = () => {
    ++speechRequest.current; speechAbort.current?.abort(); speechAbort.current = null;
    if (audioRef.current) { audioRef.current.pause(); audioRef.current.currentTime = 0; }
    setSpeechBusyId(''); setSpeechActiveId(''); setSpeechUrl('');
  };

  const stopVoiceSession = () => {
    ++voiceEpoch.current;
    if (voiceRecordTimeout.current !== null) window.clearTimeout(voiceRecordTimeout.current);
    voiceRecordTimeout.current = null;
    voiceAbort.current?.abort(); voiceAbort.current = null;
    if (voiceRecorder.current?.state === 'recording') voiceRecorder.current.stop();
    voiceRecorder.current = null;
    voiceStream.current?.getTracks().forEach((track) => track.stop()); voiceStream.current = null;
    controller.current?.abort(); stopSpeech();
    setVoiceEnabled(false); setVoiceStatus('Aus'); setVoiceTranscript(''); setVoiceLatency('');
  };

  const enableVoiceSession = async () => {
    setVoiceError(''); setVoiceStatus('Prüfe lokale Spracherkennung…');
    try {
      const status = await api<{ stt_ready: boolean }>('/voice/status');
      if (!status.stt_ready) throw new Error('Lokales Whisper-Modell fehlt. Einrichtung im README unter Sprachmodus.');
      setVoiceEnabled(true); setVoiceStatus('Bereit'); setVoiceTranscript('');
    } catch (cause) {
      setVoiceError((cause as Error).message); setVoiceStatus('Aus');
    }
  };

  const startVoiceRecording = async () => {
    if (!voiceEnabled || voiceRecorder.current || voiceAbort.current || !configured) return;
    const epoch = ++voiceEpoch.current;
    stopSpeech(); controller.current?.abort(); setVoiceError(''); setVoiceTranscript(''); setVoiceLatency('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true,
        noiseSuppression: true }, video: false });
      if (epoch !== voiceEpoch.current) { stream.getTracks().forEach((track) => track.stop()); return; }
      voiceStream.current = stream;
      const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
        .find((type) => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error('Dieser Browser unterstützt keine lokale Sprachaufnahme.');
      const recorder = new MediaRecorder(stream, { mimeType: mime });
      const chunks: Blob[] = [];
      voiceRecorder.current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        if (voiceRecordTimeout.current !== null) window.clearTimeout(voiceRecordTimeout.current);
        voiceRecordTimeout.current = null;
        stream.getTracks().forEach((track) => track.stop()); voiceStream.current = null;
        voiceRecorder.current = null;
        if (epoch !== voiceEpoch.current) return;
        const audio = new Blob(chunks, { type: mime.split(';')[0] });
        if (!audio.size) { setVoiceError('Mikrofon hat kein Audio geliefert.'); setVoiceStatus('Bereit'); return; }
        const abort = new AbortController(); voiceAbort.current = abort;
        setVoiceStatus('Erkenne Sprache lokal…');
        void transcribeAudio(audio, abort.signal).then((result) => {
          if (epoch !== voiceEpoch.current || abort.signal.aborted) return;
          setVoiceTranscript(result.text); setDraft(result.text);
          setVoiceLatency(`Aufnahme ${result.audio_seconds} s · Erkennung ${result.stt_seconds} s`);
          setVoiceStatus('Transkript prüfen, dann senden');
        }).catch((cause: Error) => {
          if (epoch !== voiceEpoch.current || abort.signal.aborted) return;
          setVoiceError(cause.message); setVoiceStatus('Bereit');
        }).finally(() => { if (voiceAbort.current === abort) voiceAbort.current = null; });
      };
      recorder.start(); setVoiceStatus('Mikrofon aktiv');
      voiceRecordTimeout.current = window.setTimeout(() => {
        if (recorder.state === 'recording') recorder.stop();
      }, 30_000);
    } catch (cause) {
      voiceStream.current?.getTracks().forEach((track) => track.stop()); voiceStream.current = null;
      if (epoch === voiceEpoch.current) { setVoiceError((cause as Error).message); setVoiceStatus('Bereit'); }
    }
  };

  const finishVoiceRecording = () => {
    if (voiceRecorder.current?.state === 'recording') voiceRecorder.current.stop();
  };
  const coveredCount = compactions[0]?.covered_message_ids.length || 0;
  const completeCount = active?.messages.filter((message) => message.status === 'complete').length || 0;
  const canCompact = completeCount - coveredCount >= 12;

  const refreshList = async (query = appliedSearch, archived = showArchived) =>
    setChats(await api<ChatSummary[]>(`/chats?q=${encodeURIComponent(query)}&archived=${archived}`));
  const refreshCases = async () => setCases(await api<AnswerCase[]>('/chat-evaluation-cases'));
  const load = async (id: string) => {
    if (navigationRef.current || controller.current || evaluationAction.current || suggestionBusy || compactionBusy) return;
    if (voiceEnabled && active?.id !== id) stopVoiceSession();
    navigationRef.current = true; setNavigating(true); setError('');
    try { const [chat, states] = await Promise.all([api<Chat>(`/chats/${id}`),
      api<ChatCompaction[]>(`/chats/${id}/compactions`)]);
      setActive(chat); setSelectedFiles([]); setCompactions(states); setCompactionNotice(''); setResetCandidate(false); setSelectedAnswerId('');
      setSuggestions([]); setSuggestionMessageId(''); setSuggestionNotice(''); }
    catch (cause) { setError((cause as Error).message); }
    finally { navigationRef.current = false; setNavigating(false); }
  };

  useEffect(() => {
    if (!requestedChatSource) return;
    let cancelled = false;
    void load(requestedChatSource.chatId).then(() => {
      if (cancelled) return;
      requestAnimationFrame(() => document.getElementById(
        `chat-message-${requestedChatSource.messageId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }));
      onSourceOpened?.();
    });
    return () => { cancelled = true; };
  }, [requestedChatSource?.chatId, requestedChatSource?.messageId]);

  useEffect(() => {
    let cancelled = false;
    void api<ChatSummary[]>(`/chats?q=${encodeURIComponent(appliedSearch)}&archived=${showArchived}`)
      .then((items) => { if (!cancelled) setChats(items); })
      .catch((cause: Error) => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; };
  }, [appliedSearch, showArchived]);
  useEffect(() => { void api<Skill[]>('/skills').then(setSkills).catch((cause: Error) => setError(cause.message)); }, []);
  useEffect(() => { void api<MemoryChoice[]>('/memory-proposals').then(setMemoryChoices)
    .catch((cause: Error) => setError(cause.message)); }, []);
  useEffect(() => { void refreshCases().catch((cause: Error) => setEvaluationNotice(cause.message)); }, []);
  useEffect(() => () => { ++voiceEpoch.current; controller.current?.abort(); voiceAbort.current?.abort();
    if (voiceRecordTimeout.current !== null) window.clearTimeout(voiceRecordTimeout.current);
    if (voiceRecorder.current?.state === 'recording') voiceRecorder.current.stop();
    voiceStream.current?.getTracks().forEach((track) => track.stop());
    speechAbort.current?.abort(); audioRef.current?.pause(); }, []);
  useEffect(() => { if (focusOnCapture && configured) composerRef.current?.focus(); }, [focusOnCapture, configured]);

  const create = async () => {
    if (navigationRef.current || controller.current || evaluationAction.current || suggestionBusy || compactionBusy) return;
    if (voiceEnabled) stopVoiceSession();
    navigationRef.current = true; setNavigating(true); setError('');
    try {
      const chat = await api<ChatSummary>('/chats', { method: 'POST' });
      setActive({ ...chat, messages: [] }); setSelectedFiles([]); setSelectedAnswerId('');
      setCompactions([]); setCompactionNotice(''); setResetCandidate(false);
      setSuggestions([]); setSuggestionMessageId(''); setSuggestionNotice('');
      setShowArchived(false); setSearchText(''); setAppliedSearch('');
      await refreshList('', false);
    } catch (cause) { setError((cause as Error).message); }
    finally { navigationRef.current = false; setNavigating(false); }
  };
  const changeArchive = async (item: ChatSummary) => {
    if (busy || navigating || managingChat || controller.current || suggestionBusy || compactionBusy) return;
    if (voiceEnabled && active?.id === item.id) stopVoiceSession();
    setManagingChat(item.id); setError(''); setDeleteCandidate(null);
    try {
      await api(`/chats/${item.id}/archive`, {
        method: 'PATCH', body: JSON.stringify({ archived: !item.archived_at }),
      });
      if (active?.id === item.id) { setActive(null); setSelectedAnswerId('');
        setSuggestions([]); setSuggestionMessageId(''); setCompactions([]); setResetCandidate(false); }
      await refreshList();
    } catch (cause) { setError((cause as Error).message); }
    finally { setManagingChat(''); }
  };
  const renameChat = async (id: string) => {
    if (busy || navigating || managingChat || !renameText.trim() || renameText.trim().length > 80) return;
    setManagingChat(id); setError('');
    try {
      const updated = await api<ChatSummary>(`/chats/${id}/title`, {
        method: 'PATCH', body: JSON.stringify({ title: renameText.trim() }),
      });
      setActive((current) => current?.id === id ? { ...current, title: updated.title } : current);
      setRenamingChat(''); setRenameText('');
      await refreshList();
    } catch (cause) { setError((cause as Error).message); }
    finally { setManagingChat(''); }
  };
  const deleteChat = async () => {
    if (!deleteCandidate || busy || navigating || managingChat || controller.current || suggestionBusy || compactionBusy) return;
    if (voiceEnabled && active?.id === deleteCandidate.id) stopVoiceSession();
    const id = deleteCandidate.id;
    setManagingChat(id); setError('');
    try {
      await api(`/chats/${id}`, { method: 'DELETE' });
      if (active?.id === id) { setActive(null); setSelectedAnswerId('');
        setSuggestions([]); setSuggestionMessageId(''); setCompactions([]); setResetCandidate(false); }
      setDeleteCandidate(null);
      await refreshList();
      await refreshCases();
    } catch (cause) { setError((cause as Error).message); }
    finally { setManagingChat(''); }
  };
  const suggestNext = async () => {
    if (!active || !latestAnswer || suggestionBusy || busy || navigating || provider !== 'ollama') return;
    setSuggestionBusy(true); setSuggestionNotice(''); setSuggestions([]);
    try {
      const result = await api<{ message_id: string; suggestions: FollowUpSuggestion[] }>(
        `/chats/${active.id}/suggestions`, {
          method: 'POST', body: JSON.stringify({ message_id: latestAnswer.id }),
        });
      setSuggestionMessageId(result.message_id);
      setSuggestions(result.suggestions);
      if (!result.suggestions.length) setSuggestionNotice('Keine passende Folgeaktion gefunden.');
    } catch (cause) { setSuggestionNotice((cause as Error).message); }
    finally { setSuggestionBusy(false); }
  };
  const compactChat = async () => {
    if (!active || !latestMessage || !canCompact || compactionBusy || busy || suggestionBusy
      || navigating || provider !== 'ollama') return;
    setCompactionBusy(true); setCompactionNotice('');
    try {
      await api<ChatCompaction>(`/chats/${active.id}/compactions`, { method: 'POST',
        body: JSON.stringify({ expected_version: compactions[0]?.version || 0,
          last_message_id: latestMessage.id }) });
      setCompactions(await api<ChatCompaction[]>(`/chats/${active.id}/compactions`));
      setCompactionNotice('Ältere Beiträge lokal zusammengefasst. Prüfe jeden Punkt und seine Quelle.');
      setResetCandidate(false);
    } catch (cause) { setCompactionNotice((cause as Error).message); }
    finally { setCompactionBusy(false); }
  };
  const reviseCompaction = async (action: 'remove' | 'reset', entryIndex?: number) => {
    if (!active || !latestMessage || !compactions[0] || compactionBusy || busy || suggestionBusy) return;
    setCompactionBusy(true); setCompactionNotice('');
    try {
      await api<ChatCompaction>(`/chats/${active.id}/compactions/revise`, { method: 'POST',
        body: JSON.stringify({ expected_version: compactions[0].version,
          last_message_id: latestMessage.id, action,
          ...(action === 'remove' ? { entry_index: entryIndex } : {}) }) });
      setCompactions(await api<ChatCompaction[]>(`/chats/${active.id}/compactions`));
      setCompactionNotice(action === 'reset' ? 'Aktueller Zustand zurückgesetzt. Ältere Fassungen bleiben lesbar.' :
        'Punkt aus dem künftigen Gesprächszustand entfernt. Ältere Fassungen bleiben lesbar.');
      setResetCandidate(false);
    } catch (cause) { setCompactionNotice((cause as Error).message); }
    finally { setCompactionBusy(false); }
  };
  const selectEvaluation = (message: Message) => {
    setSelectedAnswerId(message.id);
    setVerdict(message.evaluation?.verdict || 'unclear');
    setSourceQuality(message.evaluation?.source_quality || 'unclear');
    setReason(message.evaluation?.reason || '');
    setRegressionCase(Boolean(message.evaluation?.regression_case));
    setExpectedResult(message.evaluation?.expected_result || '');
    setEvaluationVersion(message.evaluation?.version || 0);
    setEvaluationNotice('');
  };
  const saveEvaluation = async () => {
    if (!active || !selectedAnswerId || evaluationAction.current) return;
    evaluationAction.current = true; setEvaluationBusy(true); setEvaluationNotice('');
    try {
      const saved = await api<{ version: number }>(`/chats/${active.id}/messages/${selectedAnswerId}/evaluation`, {
        method: 'PUT', body: JSON.stringify({ verdict, source_quality: sourceQuality, reason,
          regression_case: regressionCase, expected_result: regressionCase ? expectedResult : '',
          version: evaluationVersion }),
      });
      setEvaluationVersion(saved.version);
      setActive(await api<Chat>(`/chats/${active.id}`));
      await refreshCases();
      setEvaluationNotice('Antwortbewertung gespeichert. Antwort und Modellbeleg bleiben unverändert.');
    } catch (cause) { setEvaluationNotice((cause as Error).message); }
    finally { evaluationAction.current = false; setEvaluationBusy(false); }
  };

  const send = async (fromVoice = false) => {
    if (!draft.trim() || busy || suggestionBusy || compactionBusy || navigating || navigationRef.current || controller.current || evaluationAction.current || !configured || active?.archived_at || (provider === 'openrouter' && (!allowCloud || hasChatAttachments)) || (selectedFiles.length > 0 && provider !== 'ollama')) return;
    const content = draft.trim();
    const voiceGenerationEpoch = voiceEpoch.current;
    const memoryIds = [...selectedMemoryIds];
    const previousMessageIds = new Set(active?.messages.map((message) => message.id) || []);
    setDraft(''); setStreamed(''); setError(''); setBusy(true);
    setSelectedMemoryIds([]);
    setSuggestions([]); setSuggestionMessageId(''); setSuggestionNotice('');
    const abort = new AbortController(); controller.current = abort;
    let chat = active;
    let streamCompleted = false;
    const uploadedAttachmentIds: string[] = [];
    try {
      if (!chat) {
        const item = await api<ChatSummary>('/chats', { method: 'POST' });
        chat = { ...item, messages: [] }; setActive(chat);
      }
      const uploaded: ChatAttachment[] = [];
      for (const file of selectedFiles) {
        const item = await uploadChatAttachment(chat.id, file, abort.signal);
        uploaded.push(item); uploadedAttachmentIds.push(item.id);
      }
      setActive({ ...chat, messages: [...chat.messages, {
        id: 'pending-user', role: 'user', content, status: 'complete', created_at: new Date().toISOString(),
        attachments: uploaded,
      }] });
      await streamChat(chat.id, content, abort.signal, allowCloud, (name, payload) => {
        if (name === 'delta') setStreamed((previous) => previous + String(payload.text || ''));
      }, selectedSkillId || null, memoryIds, uploadedAttachmentIds);
      streamCompleted = true;
      setSelectedFiles([]);
      const updated = await api<Chat>(`/chats/${chat.id}`);
      setActive(updated);
      await refreshList();
      if (fromVoice && voiceEnabled && voiceGenerationEpoch === voiceEpoch.current) {
        const answer = updated.messages.at(-1);
        if (answer?.role === 'assistant' && answer.status === 'complete' && answer.content.trim()) {
          setVoiceStatus('Erzeuge hörbare Antwort…');
          const audioReady = await readAnswer(answer, chat.id);
          if (voiceGenerationEpoch === voiceEpoch.current) setVoiceStatus(audioReady
            ? 'Bereit · Antwort im Player' : 'Bereit · Audio konnte nicht erzeugt werden');
        }
      }
    } catch (cause) {
      if (fromVoice && voiceGenerationEpoch === voiceEpoch.current) setVoiceStatus('Bereit · Antwort fehlgeschlagen');
      if (abort.signal.aborted) setError('Antwort abgebrochen. Der bisherige Verlauf bleibt erhalten.');
      else setError((cause as Error).message);
      let userMessageSaved = streamCompleted;
      if (chat) {
        try {
          const latest = await api<Chat>(`/chats/${chat.id}`);
          setActive(latest);
          userMessageSaved ||= latest.messages.some((message) => message.role === 'user'
            && message.content === content && !previousMessageIds.has(message.id));
        } catch { /* Verbindung kann gerade ausgefallen sein. */ }
      }
      if (!userMessageSaved) { setDraft((current) => current || content); setSelectedMemoryIds(memoryIds); }
      else setSelectedFiles([]);
      if (!userMessageSaved && chat) {
        for (const id of uploadedAttachmentIds) {
          try { await api(`/chats/${chat.id}/attachments/${id}`, { method: 'DELETE' }); }
          catch { /* Ein ungesendeter Anhang bleibt im lokalen Chat, bis der Chat gelöscht wird. */ }
        }
      }
    } finally { setBusy(false); setStreamed(''); controller.current = null; }
  };

  return <div className="chat-layout">
    <div className="chat-list"><button onClick={() => void create()} className="primary" disabled={busy || suggestionBusy || compactionBusy || navigating || evaluationBusy || !!managingChat}>Neues Gespräch</button>
      <div className="chat-search"><input aria-label="Gespräche suchen" value={searchText} maxLength={200}
        onChange={(event) => setSearchText(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Enter') setAppliedSearch(searchText.trim()); }}
        placeholder="Titel oder Nachricht" disabled={busy || !!managingChat} />
        <div className="item-actions"><button onClick={() => setAppliedSearch(searchText.trim())}
          disabled={busy || !!managingChat}>Suchen</button><button onClick={() => { setSearchText(''); setAppliedSearch(''); }}
          disabled={busy || !!managingChat}>Alle</button></div>
        <label><input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)}
          disabled={busy || !!managingChat} /> Archiv anzeigen</label></div>
      {chats.map((item) => <div className="chat-list-item" key={item.id}>
        {renamingChat === item.id ? <div className="chat-rename"><input aria-label="Gesprächstitel" value={renameText} maxLength={80}
          onChange={(event) => setRenameText(event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); void renameChat(item.id); } }}
          disabled={!!managingChat} /><div className="item-actions"><button onClick={() => void renameChat(item.id)}
            disabled={!!managingChat || !renameText.trim()}>Speichern</button><button onClick={() => { setRenamingChat(''); setRenameText(''); }}
            disabled={!!managingChat}>Abbrechen</button></div></div> :
          <button className={active?.id === item.id ? 'selected' : ''} disabled={busy || suggestionBusy || compactionBusy || navigating || evaluationBusy || !!managingChat}
            onClick={() => void load(item.id)}>{item.title}</button>}
        {item.match_preview && <small>Treffer: {item.match_preview}</small>}
        <div className="item-actions"><button className="chat-action" disabled={busy || suggestionBusy || compactionBusy || navigating || evaluationBusy || !!managingChat || !!renamingChat}
          onClick={() => { setDeleteCandidate(null); setRenamingChat(item.id); setRenameText(item.title); }}>Umbenennen</button>
          <button className="chat-action" disabled={busy || suggestionBusy || compactionBusy || navigating || evaluationBusy || !!managingChat || !!renamingChat}
          onClick={() => void changeArchive(item)}>{item.archived_at ? 'Wiederherstellen' : 'Archivieren'}</button>
          <button className="chat-action" disabled={busy || suggestionBusy || compactionBusy || navigating || evaluationBusy || !!managingChat || !!renamingChat}
            onClick={() => setDeleteCandidate(item)}>Löschen</button></div>
      </div>)}
      {chats.length === 0 && <small>Keine {showArchived ? 'archivierten ' : ''}Gespräche gefunden.</small>}
      {deleteCandidate && <div className="chat-delete-confirm" role="alertdialog" aria-label="Gespräch löschen">
        <strong>„{deleteCandidate.title}“ dauerhaft löschen?</strong>
        <p>Nachrichten und zugehörige Antwortbewertungen werden entfernt. Andere Gespräche bleiben erhalten.</p>
        <div className="item-actions"><button className="chat-action" onClick={() => setDeleteCandidate(null)}
          disabled={!!managingChat}>Abbrechen</button><button className="chat-action" onClick={() => void deleteChat()}
          disabled={!!managingChat}>Dauerhaft löschen</button></div>
      </div>}
    </div>
    <section className="chat-main">
      <h2>Chat</h2>
      {active?.archived_at && <p className="notice">Dieses Gespräch ist archiviert. Zum Fortsetzen zuerst in der Archivliste wiederherstellen.</p>}
      {!configured && <div className="notice">{setupMessage}. Der Chat ist bis dahin deaktiviert.</div>}
      <div className="messages" aria-live="polite">
        {!active?.messages.length && <p className="empty">Beginne ein Gespräch. Der Verlauf wird lokal gespeichert.</p>}
        {active?.messages.map((message) => <article id={`chat-message-${message.id}`} key={message.id} className={`message ${message.role}`}><strong>{message.role === 'user' ? 'Du' : 'Jarvis'}</strong><p>{message.content || (message.status !== 'complete' ? '(keine Ausgabe)' : '')}</p><Usage message={message} />{(message.memory_context?.length ?? 0) > 0 && <details><summary>Bewusst verwendete Erinnerungen ({message.memory_context?.length})</summary><ul>{message.memory_context?.map((item) => <li key={item.proposal_id}>{item.text_snapshot} · Originalstelle: „{item.source_quote}“</li>)}</ul></details>}{message.role === 'assistant' && <><div className="item-actions"><button onClick={() => selectEvaluation(message)}>Antwort bewerten</button>{message.status === 'complete' && message.content.trim() && <button onClick={() => void readAnswer(message)} disabled={!!speechBusyId}>{speechBusyId === message.id ? 'VoiceStudio erzeugt Audio…' : speechActiveId === message.id ? 'Wiedergeben' : 'Vorlesen'}</button>}{message.evaluation && <small>Bewertung: {verdictLabels[message.evaluation.verdict]} · {sourceLabels[message.evaluation.source_quality]}</small>}</div>{speechActiveId === message.id && speechUrl && <div className="speech-player"><small>Stimme: {speechVoice}</small><audio ref={audioRef} controls autoPlay src={speechUrl} aria-label="Jarvis-Antwort als Audio" /><button onClick={stopSpeech}>Stoppen</button></div>}{speechBusyId === message.id && <small role="status">Lokale Audioerzeugung läuft. Der Antworttext bleibt erhalten.</small>}</>}{speechError && message.id === speechErrorId && <p className="notice error" role="alert">{speechError}</p>}</article>)}
        {busy && <article className="message assistant"><strong>Jarvis · antwortet</strong><p>{streamed || 'Warte auf Modell…'}</p></article>}
      </div>
      {active?.messages.some((message) => (message.attachments?.length ?? 0) > 0) &&
        <section className="card" aria-label="Dateianhänge im Gespräch"><h3>Dateianhänge im Gespräch</h3>
          <div className="item-list">{active.messages.flatMap((message) =>
            (message.attachments || []).map((attachment) => <div className="item-row" key={attachment.id}>
              <div style={{ minWidth: 0, overflowWrap: 'anywhere' }}><strong>{attachment.filename}</strong>
                {isImageFile(attachment.filename) && message.id !== 'pending-user' && <img alt={`Bildanhang ${attachment.filename}`}
                  src={`/api/chats/${encodeURIComponent(active.id)}/attachments/${encodeURIComponent(attachment.id)}/preview`}
                  style={{ display: 'block', maxWidth: '100%', maxHeight: 300, marginBlock: 8 }} />}
                <small>Nachricht: {message.content.slice(0, 90)} · {attachment.size} Bytes · SHA-256 {attachment.sha256}</small>
                {message.id === 'pending-user' ? <small>Wird mit der Nachricht gespeichert…</small> :
                  <a href={`/api/chats/${encodeURIComponent(active.id)}/attachments/${encodeURIComponent(attachment.id)}`}
                    download={attachment.filename}>Unveränderte Kopie herunterladen</a>}</div>
            </div>))}</div>
        </section>}
      {active && (canCompact || compactions.length > 0) && <section className="card chat-compactions">
        <h3>Ältere Beiträge im Kontext behalten</h3><p>Ein Klick lässt lokales Ollama einen begrenzten alten Abschnitt zusammenfassen. Die letzten acht fertigen Nachrichten bleiben roh. Jeder Punkt nennt die ursprünglichen Nachrichten; prüfe die Angaben vor weiterer Nutzung. Die vollständige Historie bleibt erhalten.</p>
        {!active.archived_at && provider === 'ollama' && <button onClick={() => void compactChat()} disabled={!canCompact || compactionBusy || suggestionBusy || busy || navigating || !configured}>{compactionBusy ? 'Ollama fasst zusammen…' : 'Ältere Beiträge zusammenfassen'}</button>}
        {!canCompact && compactions.length > 0 && <small>Derzeit kein weiterer alter Abschnitt verfügbar.</small>}
        {compactionNotice && <p className="notice" role="status">{compactionNotice}</p>}
        {compactions[0] && <CompactionState snapshot={compactions[0]}
          onRemove={!active.archived_at && !compactionBusy && !busy && !suggestionBusy ?
            (index) => void reviseCompaction('remove', index) : undefined} />}
        {compactions[0]?.entries.length > 0 && !active.archived_at && <div className="item-actions"><button onClick={() => setResetCandidate(true)} disabled={compactionBusy || busy || suggestionBusy}>Aktuellen Zustand zurücksetzen</button></div>}
        {resetCandidate && <div className="chat-delete-confirm" role="alertdialog" aria-label="Gesprächszustand zurücksetzen"><strong>Aktuellen Gesprächszustand zurücksetzen?</strong><p>Die nächste Ollama-Antwort verwendet dann wieder nur rohe Beiträge. Alte Fassungen und alle Nachrichten bleiben lesbar.</p><div className="item-actions"><button onClick={() => setResetCandidate(false)} disabled={compactionBusy}>Abbrechen</button><button onClick={() => void reviseCompaction('reset')} disabled={compactionBusy || busy || suggestionBusy}>Zustand wirklich zurücksetzen</button></div></div>}
        {compactions.length > 1 && <details><summary>Frühere Fassungen ({compactions.length - 1})</summary>{compactions.slice(1).map((snapshot) => <CompactionState key={snapshot.version} snapshot={snapshot} />)}</details>}
      </section>}
      {latestAnswer && !active?.archived_at && provider === 'ollama' && <section className="card chat-suggestions">
        <h3>Nächste Schritte</h3><p>Ollama kann zur letzten Antwort höchstens zwei Vorschläge machen. Prüfe den Text. Die Auswahl füllt nur ein vorhandenes Formular; erst dort wird eine Aufgabe gespeichert oder eine Recherche gestartet.</p>
        <button onClick={() => void suggestNext()} disabled={suggestionBusy || compactionBusy || busy || navigating || !!managingChat || !configured}>{suggestionBusy ? 'Ollama schlägt vor…' : 'Nächste Schritte vorschlagen'}</button>
        {suggestionNotice && <p className="notice" role="status">{suggestionNotice}</p>}
        {suggestionMessageId === latestAnswer.id && suggestions.length > 0 && <div className="item-list">{suggestions.map((suggestion, index) =>
          <div className="item-row" key={`${suggestion.kind}-${index}`}><div><strong>{suggestion.kind === 'task' ? 'Aufgabe' : 'Recherche'}</strong><p>{suggestion.text}</p></div>
            <div className="item-actions"><button onClick={() => suggestion.kind === 'task' ? onPrepareTask(suggestion.text) : onPrepareResearch(suggestion.text)}>{suggestion.kind === 'task' ? 'Aufgabe vorbereiten' : 'Recherche vorbereiten'}</button>
              <button onClick={() => setSuggestions((items) => items.filter((_, itemIndex) => itemIndex !== index))}>Ausblenden</button></div></div>)}</div>}
      </section>}
      {error && <p className="notice error" role="alert">{error}</p>}
      <div className="item-actions skill-selector"><label>Chat-Skill <select aria-label="Chat-Skill" value={selectedSkillId} onChange={(event) => setSelectedSkillId(event.target.value)} disabled={busy || navigating}>
        <option value="">Kein Skill</option>{skills.filter((skill) => skill.active_version != null).map((skill) => <option key={skill.id} value={skill.id}>{skill.name} · v{skill.active_version}</option>)}
      </select></label><button onClick={onSkills} disabled={busy}>Skills verwalten</button></div>
      {selectedSkillId && !skills.some((skill) => skill.id === selectedSkillId && skill.active_version != null) && <p className="notice">Der zuvor ausgewählte Skill ist nicht mehr aktiv. Wähle „Kein Skill“ oder einen aktiven Skill.</p>}
      {provider === 'ollama' && memoryChoices.some((item) => item.status === 'accepted' && item.current_memory_text === item.decided_text && item.validity_state === 'current') && <section className="card chat-memory-choice"><h3>Erinnerungen für diese Antwort</h3><p>Wähle höchstens drei bestätigte Erinnerungen. Sie gehen nur in die nächste lokale Ollama-Anfrage und werden danach abgewählt.</p><div className="item-list">{memoryChoices.filter((item) => item.status === 'accepted' && item.current_memory_text === item.decided_text && item.validity_state === 'current').map((item) => <label key={item.id}><input type="checkbox" checked={selectedMemoryIds.includes(item.id)} disabled={busy || (selectedMemoryIds.length >= 3 && !selectedMemoryIds.includes(item.id))} onChange={(event) => setSelectedMemoryIds((current) => event.target.checked ? [...current, item.id] : current.filter((id) => id !== item.id))} /> {item.current_memory_text}</label>)}</div></section>}
      {provider === 'openrouter' && <label><input type="checkbox" checked={allowCloud} onChange={(event) => setAllowCloud(event.target.checked)} /> Diese Nachricht und den bisherigen Chatverlauf an OpenRouter senden</label>}
      <section className="card voice-session" aria-label="Sprachmodus">
        <div className="item-actions"><strong>Sprachmodus</strong>
          {!voiceEnabled ? <button onClick={() => void enableVoiceSession()} disabled={!configured || !!active?.archived_at || voiceStatus.startsWith('Prüfe')}>Sprachmodus starten</button>
            : <button onClick={stopVoiceSession}>Sprachmodus beenden</button>}
          {voiceEnabled && (voiceRecorder.current?.state === 'recording'
            ? <button onClick={finishVoiceRecording}>Aufnahme beenden</button>
            : <button onClick={() => void startVoiceRecording()} disabled={!!voiceAbort.current}>Mikrofon starten / Antwort unterbrechen</button>)}
          {voiceEnabled && voiceTranscript && <button className="primary" onClick={() => void send(true)} disabled={busy || !draft.trim() || !!voiceAbort.current || !!active?.archived_at || !configured || (provider === 'openrouter' && (!allowCloud || hasChatAttachments))}>Transkript senden und vorlesen</button>}
        </div>
        <small role="status">{voiceStatus}. Mikrofon wird nur während einer Aufnahme verwendet.</small>
        {voiceTranscript && <p>Erkannt: „{voiceTranscript}“ · Vor dem Senden im Nachrichtenfeld korrigierbar.</p>}
        {voiceLatency && <small>{voiceLatency}</small>}
        {voiceError && <p className="notice error" role="alert">{voiceError}</p>}
      </section>
      <section className="card"><label>Bis zu drei Dateien für diese Nachricht (TXT, Markdown, DOCX, Text-PDF oder PNG/JPEG/WebP; je höchstens 512 KiB, höchstens ein Bild)
        <input aria-label="Datei für Chat" type="file" multiple accept=".txt,.md,.markdown,.docx,.pdf,.png,.jpg,.jpeg,.webp"
          style={{ display: 'block', maxWidth: '100%', marginTop: 8 }}
          disabled={!configured || busy || provider !== 'ollama' || !!active?.archived_at}
          onChange={(event) => {
            const files = Array.from(event.target.files || []);
            event.currentTarget.value = '';
            if (files.length > 3) { setError('Höchstens drei Dateien je Nachricht wählen'); setSelectedFiles([]); }
            else if (files.filter((file) => isImageFile(file.name)).length > 1) {
              setError('Höchstens ein Bild je Nachricht wählen'); setSelectedFiles([]);
            }
            else if (files.some((file) => file.size > 512 * 1024)) {
              setError('Ein Chatanhang überschreitet 512 KiB'); setSelectedFiles([]);
            }
            else { setError(''); setSelectedFiles(files); }
          }} /></label>
        {selectedFiles.map((file, index) => <p key={`${file.name}-${index}`} style={{ overflowWrap: 'anywhere' }}>
          {index + 1}. {file.name} · {file.size} Bytes <button type="button"
            onClick={() => setSelectedFiles((current) => current.filter((_, position) => position !== index))}
            disabled={busy}>Datei entfernen</button></p>)}
        {imagePreview && <img src={imagePreview} alt="Vorschau des gewählten Bildes"
          style={{ display: 'block', maxWidth: '100%', maxHeight: 300 }} />}
        {selectedImage && <p>Dieses Bild wird nur für die nächste Antwort an das lokale Bildmodell gesendet. Für weitere Bildfragen erneut anhängen.</p>}
        {selectedFiles.length > 0 && provider !== 'ollama' && <p>Chatanhänge können nur an lokales Ollama gesendet werden.</p>}
        {hasChatAttachments && provider === 'openrouter' && <p>Dieses Gespräch enthält lokale Dateianhänge. Für OpenRouter ein neues Gespräch ohne Anhang beginnen.</p>}
      </section>
      <div className="composer"><textarea ref={composerRef} aria-label="Nachricht" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send(); } }} disabled={!configured || busy || suggestionBusy || compactionBusy || navigating || !!active?.archived_at} placeholder="Schreibe eine Nachricht…" />
        {busy ? <button onClick={() => controller.current?.abort()}>Stoppen</button> : <button className="primary" onClick={() => void send()} disabled={!draft.trim() || !configured || suggestionBusy || compactionBusy || navigating || !!active?.archived_at || (provider === 'openrouter' && (!allowCloud || hasChatAttachments)) || (selectedFiles.length > 0 && provider !== 'ollama')}>Senden</button>}
      </div>
      {selectedAnswerId && <section className="card"><h3>Antwort bewerten</h3><p>Prüfe den Antworttext und mögliche Quellen selbst. Die Quellenbewertung ist deine Einschätzung.</p>
        <div className="memory-form"><label>Aufgabenerfolg <select aria-label="Erfolg der Antwort" value={verdict} onChange={(event) => setVerdict(event.target.value)}><option value="achieved">Ziel erreicht</option><option value="partial">Teilweise erreicht</option><option value="failed">Ziel verfehlt</option><option value="unclear">Unklar</option></select></label>
          <label>Quellenlage <select aria-label="Quellenlage der Antwort" value={sourceQuality} onChange={(event) => setSourceQuality(event.target.value)}><option value="supported">Durch geprüfte Quellen gestützt</option><option value="unsupported">Nicht ausreichend belegt</option><option value="not_applicable">Keine Quelle erforderlich</option><option value="unclear">Unklar</option></select></label>
          <textarea aria-label="Begründung der Antwortbewertung" maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Woran erkennst du Erfolg oder Fehler?" />
          <label><input type="checkbox" checked={regressionCase} onChange={(event) => setRegressionCase(event.target.checked)} /> Als Antwort-Testfall sichern</label>
          {regressionCase && <textarea aria-label="Erwartete Antwort im Testfall" maxLength={500} value={expectedResult} onChange={(event) => setExpectedResult(event.target.value)} placeholder="Welches Ergebnis soll bei dieser Frage erreicht werden?" />}
          <button className="primary" disabled={evaluationBusy || !reason.trim() || (regressionCase && (!['partial', 'failed'].includes(verdict) || !expectedResult.trim()))} onClick={() => void saveEvaluation()}>Antwortbewertung speichern</button></div>
      </section>}
      {evaluationNotice && <p className="notice" role="status">{evaluationNotice}</p>}
      <section className="card"><h3>Gesicherte Antwort-Testfälle</h3><p>Die letzten 20 Fälle zeigen Frage, beobachtete Antwort, Regel- und Skillversion sowie erwartetes Ergebnis. Kein Fall wird automatisch erneut ausgeführt.</p><div className="item-list">{cases.length ? cases.slice(0, 20).map((item) => <div className="item-row" key={item.message_id}><div><strong>{item.question || 'Frage nicht mehr vorhanden'}</strong><small>{item.model || item.provider || 'Modell unbekannt'} · {item.rule_version != null ? `Chat-Regel v${item.rule_version}` : 'Regel unbekannt'} · {item.skill_id ? `Skill v${item.skill_version} · ${item.skill_name || item.skill_id} (${item.skill_hash?.slice(0, 12)}…)` : 'kein Skill'} · {item.cost_kind === 'local_no_api_charge' ? 'keine API-Gebühr (lokal)' : item.cost_usd != null ? `${item.cost_usd.toFixed(5)} USD (${item.cost_kind || 'gemeldet'})` : 'Kosten unbekannt'}</small><p>Antwort: {item.answer || '(keine Ausgabe)'}</p><p>Bewertung: {verdictLabels[item.verdict]} · {sourceLabels[item.source_quality]} · {item.reason}</p><p>Erwartet: {item.expected_result}</p></div></div>) : <p>Keine Antwort-Testfälle gespeichert.</p>}</div></section>
    </section>
  </div>;
}
