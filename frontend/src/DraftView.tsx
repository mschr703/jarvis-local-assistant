import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import { api } from './api';
import { cleanStyleFeedback, essayOutline, validEssayInput, validStyleFeedback, wordCount } from './essay';
import type { Source } from './DocumentView';
import StyleHistory, { type ProfileHistory } from './StyleHistory';

type Policy = 'local_only' | 'cloud_allowed';
type Sample = { id: string; title: string; content: string; policy: Policy;
  source_path?: string | null; source_sha256?: string | null };
type Profile = { description: string; policy: Policy; revision: number };
type TypedKind = 'email' | 'text' | 'answer' | 'essay';
type TypedProfile = Profile & { kind: TypedKind; override: boolean;
  effective_description: string; effective_policy: Policy };
const TYPED_KINDS: TypedKind[] = ['email', 'text', 'answer', 'essay'];
type ProfileSuggestion = { suggestion: string; truncated: boolean;
  samples: { id: string; title: string; sha256: string }[] };
type EssayFeedback = { id: string; draft_id: string; draft_version: number; note: string; created_at: string };
type WritingSignal = { id: string; label: string; excerpt: string; start: number;
  end: number; suggestion: string };
type ImportableDocument = { id: string; path: string; sha256: string | null; size: number | null;
  status: string; excluded: number };
export type StyleForm = {
  profile: Profile; profileReady: boolean; profileSaving: boolean; profileTouched: boolean;
  namedProfile: Profile; namedReady: boolean; namedSaving: boolean; namedTouched: boolean;
  typedProfiles: Partial<Record<TypedKind, TypedProfile>>; typedKind: TypedKind;
  typedTouched: TypedKind[]; typedReady: boolean; typedSaving: boolean;
  name: string; sampleText: string; samplePolicy: Policy; sampleSaving: boolean;
  importDocumentId: string; importExpectedSha: string; importTitle: string;
  ownTextConfirm: boolean; importSaving: boolean;
  suggestion: ProfileSuggestion | null; suggestionSaving: boolean; suggestionOwnTextConfirm: boolean;
};
export type Draft = {
  id: string; kind: TypedKind; brief: string; content: string;
  sources: Source[]; uncertainty: string; version: number; status: string; rule_version: number | null;
  word_target: number | null; outline: string[]; actual_words: number;
  style_feedback: { id: string; note: string }[];
  named_style_profile: { name: string; revision: number; description: string } | null;
  exports: { format: string; path: string }[];
};
export type DraftComposer = {
  kind: Draft['kind']; brief: string; inputText: string;
  namedStyleProfile: '' | 'Maxim';
  selectedSamples: string[]; allowCloud: boolean; wordTarget: string; outlineText: string;
  feedbackNote: string;
};

export default function DraftView({ personalStyleLabel, selectedSources, onClearSources, configured, provider, composer, setComposer,
  active, setActive, editedContent, setEditedContent, lastRequest, setLastRequest,
  busy, setBusy, notice, setNotice, operationRef, profileEpoch, revision, onChanged, styleForm, setStyleForm }: {
  personalStyleLabel: string; selectedSources: Source[]; onClearSources: () => void; configured: boolean; provider: string;
  composer: DraftComposer; setComposer: Dispatch<SetStateAction<DraftComposer>>;
  active: Draft | null; setActive: Dispatch<SetStateAction<Draft | null>>;
  editedContent: string; setEditedContent: Dispatch<SetStateAction<string>>;
  lastRequest: string | null; setLastRequest: Dispatch<SetStateAction<string | null>>;
  busy: boolean; setBusy: Dispatch<SetStateAction<boolean>>;
  notice: string; setNotice: Dispatch<SetStateAction<string>>;
  operationRef: { current: boolean }; profileEpoch: { current: number };
  revision: number; onChanged: () => void;
  styleForm: StyleForm; setStyleForm: Dispatch<SetStateAction<StyleForm>>;
}) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [documents, setDocuments] = useState<ImportableDocument[]>([]);
  const [essayFeedback, setEssayFeedback] = useState<EssayFeedback[]>([]);
  const [generalHistory, setGeneralHistory] = useState<ProfileHistory | null>(null);
  const [typedHistories, setTypedHistories] = useState<Partial<Record<TypedKind, ProfileHistory>>>({});
  const profileSavingRef = useRef(false);
  const typedSavingRef = useRef(false);
  const namedSavingRef = useRef(false);
  const refreshRequest = useRef(0);
  const { profile, profileReady, profileSaving, namedProfile, namedReady, namedSaving,
    name, sampleText, samplePolicy, sampleSaving,
    importDocumentId, importExpectedSha, importTitle, ownTextConfirm, importSaving,
    suggestion, suggestionSaving, suggestionOwnTextConfirm,
    typedProfiles, typedKind, typedReady, typedSaving } = styleForm;
  const currentTyped = typedProfiles[typedKind];
  const updateStyleForm = (change: Partial<StyleForm>) => setStyleForm((current) => ({ ...current, ...change }));
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [polishProposal, setPolishProposal] = useState<{
    draftId: string; version: number; text: string;
  } | null>(null);
  const [signalCheck, setSignalCheck] = useState<{
    draftId: string; content: string; signals: WritingSignal[];
  } | null>(null);
  const { kind, brief, inputText, namedStyleProfile, selectedSamples, allowCloud,
    wordTarget, outlineText, feedbackNote } = composer;
  const updateComposer = (change: Partial<DraftComposer>) => setComposer((current) => ({ ...current, ...change }));
  const outline = essayOutline(outlineText);
  const essayValid = validEssayInput(wordTarget, outline);
  const importable = documents.filter((item) => item.status === 'indexed' && !item.excluded &&
    item.sha256 && item.size !== null && item.size <= 80_000 &&
    /\.(txt|md|markdown)$/i.test(item.path));

  const requestBody = () => JSON.stringify({
    kind, brief, input_text: inputText, passage_ids: selectedSources.map((item) => item.passage_id),
    style_sample_ids: selectedSamples, named_style_profile: namedStyleProfile || null,
    allow_cloud: allowCloud,
    ...(kind === 'essay' ? { word_target: Number(wordTarget), outline } : {}),
  });

  useEffect(() => {
    const request = ++refreshRequest.current;
    const epoch = profileEpoch.current;
    void Promise.all([
      api<Sample[]>('/style/samples'), api<Profile>('/style/profile'),
      api<Profile | null>('/style/named/Maxim'), api<Draft[]>('/drafts'),
      api<EssayFeedback[]>('/style/essay-feedback'), api<ImportableDocument[]>('/documents'),
      Promise.all(TYPED_KINDS.map((item) => api<TypedProfile>(`/style/profiles/${item}`))),
      api<ProfileHistory>('/style/profile/history'),
      Promise.all(TYPED_KINDS.map((item) => api<ProfileHistory>(`/style/profiles/${item}/history`))),
    ]).then(([nextSamples, nextProfile, nextNamed, nextDrafts, nextFeedback, nextDocuments, nextTyped,
      nextGeneralHistory, nextTypedHistories]) => {
      if (request !== refreshRequest.current) return;
      setSamples(nextSamples);
      setStyleForm((current) => {
        const profiles = { ...current.typedProfiles };
        for (const item of nextTyped) {
          if (!current.typedTouched.includes(item.kind) && !current.typedSaving &&
              profileEpoch.current === epoch) profiles[item.kind] = item;
        }
        return { ...current,
          profile: current.profileTouched || current.profileSaving || profileEpoch.current !== epoch
            ? current.profile : nextProfile,
          profileReady: true, typedProfiles: profiles, typedReady: true,
          namedProfile: current.namedTouched || current.namedSaving || profileEpoch.current !== epoch
            ? current.namedProfile : (nextNamed || { description: '', policy: 'local_only', revision: 0 }),
          namedReady: true,
        };
      });
      setDrafts(nextDrafts);
      setEssayFeedback(nextFeedback);
      setDocuments(nextDocuments);
      setGeneralHistory(nextGeneralHistory);
      setTypedHistories(Object.fromEntries(nextTypedHistories.map((item) => [item.scope, item])) as
        Partial<Record<TypedKind, ProfileHistory>>);
    }).catch((error: Error) => {
      if (request === refreshRequest.current) setNotice(error.message);
    });
    return () => { ++refreshRequest.current; };
  }, [revision]);

  const selectDraft = (draft: Draft) => {
    if (active?.id !== draft.id) updateComposer({ feedbackNote: '' });
    setPolishProposal(null);
    setSignalCheck(null);
    setActive(draft); setEditedContent(draft.content);
  };
  const addSample = async () => {
    if (operationRef.current) return;
    operationRef.current = true;
    setBusy(true);
    updateStyleForm({ sampleSaving: true });
    try {
      await api<Sample>('/style/samples', { method: 'POST', body: JSON.stringify({ title: name, content: sampleText, policy: samplePolicy }) });
      updateStyleForm({ name: '', sampleText: '' }); setNotice('Schreibprobe gespeichert.'); onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); updateStyleForm({ sampleSaving: false }); }
  };
  const importSample = async () => {
    const document = importable.find((item) => item.id === importDocumentId);
    if (!document?.sha256 || document.sha256 !== importExpectedSha || !ownTextConfirm ||
        !importTitle.trim() || operationRef.current) return;
    operationRef.current = true; setBusy(true); updateStyleForm({ importSaving: true });
    try {
      const sample = await api<Sample>('/style/samples/import', { method: 'POST', body: JSON.stringify({
        document_id: document.id, expected_sha256: importExpectedSha,
        title: importTitle.trim(), confirm_own_text: true,
      }) });
      setComposer((current) => ({ ...current, selectedSamples: current.selectedSamples.includes(sample.id)
        || current.selectedSamples.length >= 5 ? current.selectedSamples : [...current.selectedSamples, sample.id] }));
      updateStyleForm({ importDocumentId: '', importExpectedSha: '', importTitle: '',
        ownTextConfirm: false, suggestionOwnTextConfirm: false, suggestion: null });
      setNotice('Eigene Datei als lokale Schreibprobe übernommen. Herkunft und Hash bleiben gespeichert.');
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); updateStyleForm({ importSaving: false }); }
  };
  const deleteSample = async (id: string) => {
    try {
      await api(`/style/samples/${id}`, { method: 'DELETE' });
      setComposer((current) => ({ ...current, selectedSamples: current.selectedSamples.filter((item) => item !== id) }));
      updateStyleForm({ suggestionOwnTextConfirm: false, suggestion: null });
      setNotice('Schreibprobe gelöscht.'); onChanged();
    } catch (error) { setNotice((error as Error).message); }
  };
  const saveProfile = async () => {
    if (!profileReady || profileSaving || profileSavingRef.current) return;
    profileSavingRef.current = true;
    ++profileEpoch.current;
    updateStyleForm({ profileSaving: true });
    try {
      const result = await api<Profile>('/style/profile', { method: 'PUT', body: JSON.stringify({
        description: profile.description, policy: profile.policy, expected_revision: profile.revision,
      }) });
      ++profileEpoch.current;
      updateStyleForm({ profileTouched: false, profile: result });
      setNotice('Stilprofil gespeichert.'); onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { profileSavingRef.current = false; updateStyleForm({ profileSaving: false }); }
  };
  const saveNamedProfile = async () => {
    if (!namedReady || namedSaving || namedSavingRef.current || !namedProfile.description.trim()) return;
    namedSavingRef.current = true; ++profileEpoch.current;
    updateStyleForm({ namedSaving: true });
    try {
      const result = await api<Profile>('/style/named/Maxim', { method: 'PUT', body: JSON.stringify({
        description: namedProfile.description, policy: namedProfile.policy,
        expected_revision: namedProfile.revision,
      }) });
      ++profileEpoch.current;
      updateStyleForm({ namedProfile: result, namedTouched: false });
      setNotice(`Schreibprofil ${personalStyleLabel} gespeichert. Bei neuen Entwürfen ausdrücklich auswählen.`);
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { namedSavingRef.current = false; updateStyleForm({ namedSaving: false }); }
  };
  const reloadNamedProfile = async () => {
    if (namedSavingRef.current) return;
    namedSavingRef.current = true; ++profileEpoch.current;
    updateStyleForm({ namedSaving: true });
    try {
      const result = await api<Profile | null>('/style/named/Maxim');
      ++profileEpoch.current;
      updateStyleForm({ namedProfile: result || { description: '', policy: 'local_only', revision: 0 },
        namedTouched: false });
      setNotice(`Schreibprofil ${personalStyleLabel} neu geladen. Ungespeicherte Änderungen wurden verworfen.`);
    } catch (error) { setNotice((error as Error).message); }
    finally { namedSavingRef.current = false; updateStyleForm({ namedSaving: false }); }
  };
  const reloadProfile = async () => {
    if (profileSavingRef.current) return;
    profileSavingRef.current = true; ++profileEpoch.current;
    updateStyleForm({ profileSaving: true });
    try {
      const [result, history] = await Promise.all([
        api<Profile>('/style/profile'), api<ProfileHistory>('/style/profile/history'),
      ]);
      ++profileEpoch.current;
      updateStyleForm({ profile: result, profileTouched: false });
      setGeneralHistory(history);
      setNotice('Gespeicherte Profilfassung neu geladen. Ungespeicherte Eingabe wurde verworfen.');
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { profileSavingRef.current = false; updateStyleForm({ profileSaving: false }); }
  };
  const restoreProfile = async (version: number) => {
    if (profileSavingRef.current || styleForm.profileTouched) return;
    profileSavingRef.current = true; ++profileEpoch.current;
    updateStyleForm({ profileSaving: true });
    try {
      const result = await api<Profile>('/style/profile/restore', { method: 'POST',
        body: JSON.stringify({ revision: version, expected_revision: profile.revision }) });
      const history = await api<ProfileHistory>('/style/profile/history');
      ++profileEpoch.current;
      updateStyleForm({ profile: result, profileTouched: false });
      setGeneralHistory(history);
      setNotice(`Allgemeine Fassung ${version} wiederhergestellt; Cloud-Freigabe: ${result.policy === 'local_only' ? 'nur lokal' : 'erlaubt'}.`);
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { profileSavingRef.current = false; updateStyleForm({ profileSaving: false }); }
  };
  const changeTypedProfile = (change: Partial<Profile>) => setStyleForm((current) => {
    const selected = current.typedProfiles[current.typedKind];
    if (!selected) return current;
    return { ...current, typedProfiles: { ...current.typedProfiles,
      [current.typedKind]: { ...selected, ...change } },
      typedTouched: current.typedTouched.includes(current.typedKind)
        ? current.typedTouched : [...current.typedTouched, current.typedKind] };
  });
  const saveTypedProfile = async (remove = false) => {
    if (!typedReady || !currentTyped || typedSaving || typedSavingRef.current) return;
    typedSavingRef.current = true;
    const selectedKind = typedKind;
    const input = { description: remove ? '' : currentTyped.description,
      policy: currentTyped.policy, expected_revision: currentTyped.revision };
    ++profileEpoch.current;
    updateStyleForm({ typedSaving: true });
    try {
      const result = await api<TypedProfile>(`/style/profiles/${selectedKind}`, {
        method: 'PUT', body: JSON.stringify(input),
      });
      ++profileEpoch.current;
      setStyleForm((current) => ({ ...current,
        typedProfiles: { ...current.typedProfiles, [selectedKind]: result },
        typedTouched: current.typedTouched.filter((item) => item !== selectedKind),
      }));
      setNotice(result.override ? 'Stilprofil für Textsorte gespeichert.' : 'Eigene Vorgabe entfernt; allgemeines Profil gilt wieder.');
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { typedSavingRef.current = false; updateStyleForm({ typedSaving: false }); }
  };
  const reloadTypedProfile = async () => {
    if (typedSavingRef.current) return;
    typedSavingRef.current = true;
    const selectedKind = typedKind;
    ++profileEpoch.current; updateStyleForm({ typedSaving: true });
    try {
      const [result, history] = await Promise.all([
        api<TypedProfile>(`/style/profiles/${selectedKind}`),
        api<ProfileHistory>(`/style/profiles/${selectedKind}/history`),
      ]);
      ++profileEpoch.current;
      setStyleForm((current) => ({ ...current,
        typedProfiles: { ...current.typedProfiles, [selectedKind]: result },
        typedTouched: current.typedTouched.filter((item) => item !== selectedKind),
      }));
      setTypedHistories((current) => ({ ...current, [selectedKind]: history }));
      setNotice('Gespeicherte Textsortenfassung neu geladen. Ungespeicherte Eingabe wurde verworfen.');
    } catch (error) { setNotice((error as Error).message); }
    finally { typedSavingRef.current = false; updateStyleForm({ typedSaving: false }); }
  };
  const restoreTypedProfile = async (version: number) => {
    if (typedSavingRef.current || !currentTyped || styleForm.typedTouched.includes(typedKind)) return;
    typedSavingRef.current = true;
    const selectedKind = typedKind;
    ++profileEpoch.current; updateStyleForm({ typedSaving: true });
    try {
      const result = await api<TypedProfile>(`/style/profiles/${selectedKind}/restore`, {
        method: 'POST', body: JSON.stringify({
          revision: version, expected_revision: currentTyped.revision,
        }),
      });
      const history = await api<ProfileHistory>(`/style/profiles/${selectedKind}/history`);
      ++profileEpoch.current;
      setStyleForm((current) => ({ ...current,
        typedProfiles: { ...current.typedProfiles, [selectedKind]: result },
        typedTouched: current.typedTouched.filter((item) => item !== selectedKind),
      }));
      setTypedHistories((current) => ({ ...current, [selectedKind]: history }));
      setNotice(`${selectedKind}: Fassung ${version} wiederhergestellt; ${result.override ? 'eigene Vorgabe' : 'allgemeiner Rückfall'} aktiv.`);
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { typedSavingRef.current = false; updateStyleForm({ typedSaving: false }); }
  };
  const suggestProfile = async () => {
    if (provider !== 'ollama' || !suggestionOwnTextConfirm || selectedSamples.length < 1 || selectedSamples.length > 3 ||
        operationRef.current || suggestionSaving) return;
    operationRef.current = true; setBusy(true);
    updateStyleForm({ suggestionSaving: true, suggestion: null });
    setNotice('Lokales Ollama prüft die ausgewählten Schreibproben…');
    try {
      const result = await api<ProfileSuggestion>('/style/profile/suggest', {
        method: 'POST', body: JSON.stringify({ style_sample_ids: selectedSamples, confirm_own_text: true }),
      });
      updateStyleForm({ suggestion: result });
      setNotice('Vorschlag bereit. Prüfe und bearbeite ihn vor der Übernahme.');
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); updateStyleForm({ suggestionSaving: false, suggestionOwnTextConfirm: false }); }
  };
  const generate = async () => {
    if (operationRef.current) return;
    operationRef.current = true;
    const payload = requestBody();
    setBusy(true); setNotice('Entwurf wird lokal erstellt…');
    try {
      const draft = await api<Draft>('/drafts/generate', { method: 'POST', body: payload });
      selectDraft(draft); setNotice('Entwurf erstellt. Prüfe und bearbeite den Text vor dem Export.');
      setLastRequest(payload); onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); }
  };
  const saveDraft = async () => {
    if (!active || operationRef.current) return;
    operationRef.current = true;
    setBusy(true);
    try {
      const draft = await api<Draft>(`/drafts/${active.id}`, { method: 'PATCH', body: JSON.stringify({ content: editedContent, expected_version: active.version }) });
      selectDraft(draft); setNotice('Änderung gespeichert.'); onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); }
  };
  const polishDraft = async () => {
    if (!active || !active.named_style_profile || provider !== 'ollama' ||
        editedContent !== active.content || operationRef.current) return;
    operationRef.current = true; setBusy(true); setPolishProposal(null);
    setNotice('Lokales Ollama erstellt einen Stilvorschlag…');
    try {
      const result = await api<{ draft_id: string; draft_version: number;
        proposal: string; notice: string }>(`/drafts/${active.id}/style-polish`, {
          method: 'POST', body: JSON.stringify({ expected_version: active.version }),
        });
      setPolishProposal({ draftId: result.draft_id, version: result.draft_version,
        text: result.proposal });
      setNotice(result.notice);
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); }
  };
  const checkWritingSignals = async () => {
    if (!active || operationRef.current || editedContent.length > 40_000) return;
    const checkedContent = editedContent;
    operationRef.current = true; setBusy(true); setSignalCheck(null);
    try {
      const result = await api<{ signals: WritingSignal[]; notice: string }>(
        '/style/writing-signals', {
          method: 'POST', body: JSON.stringify({ content: checkedContent }),
        });
      setSignalCheck({ draftId: active.id, content: checkedContent, signals: result.signals });
      setNotice(result.notice);
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); }
  };
  const reloadDraft = async () => {
    if (!active || busy) return;
    try {
      const fresh = await api<Draft>(`/drafts/${active.id}`);
      selectDraft(fresh);
      setNotice('Aktuelle Serverversion geladen; lokale ungespeicherte Änderungen verworfen.');
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
  };
  const exportDraft = async (format: 'txt' | 'md' | 'eml') => {
    if (!active || operationRef.current || editedContent !== active.content) return;
    operationRef.current = true;
    setBusy(true);
    try {
      const result = await api<{ path: string }>(`/drafts/${active.id}/export`, { method: 'POST', body: JSON.stringify({ format, expected_version: active.version }) });
      setNotice(`Gespeichert: ${result.path}`);
      const fresh = await api<Draft>(`/drafts/${active.id}`); selectDraft(fresh); onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); }
  };

  const saveEssayFeedback = async () => {
    if (!active || active.kind !== 'essay' || operationRef.current ||
        editedContent !== active.content || !validStyleFeedback(feedbackNote)) return;
    operationRef.current = true; setBusy(true);
    try {
      await api<EssayFeedback>(`/drafts/${active.id}/style-feedback`, {
        method: 'POST', body: JSON.stringify({
          note: cleanStyleFeedback(feedbackNote), expected_version: active.version,
        }),
      });
      updateComposer({ feedbackNote: '' });
      setNotice('Stilhinweis für künftige lokale Essays gespeichert.'); onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); }
  };
  const deleteEssayFeedback = async (id: string) => {
    if (operationRef.current) return;
    operationRef.current = true; setBusy(true);
    try {
      await api(`/style/essay-feedback/${id}`, { method: 'DELETE' });
      setEssayFeedback((current) => current.filter((item) => item.id !== id));
      setNotice('Stilhinweis gelöscht; frühere Entwürfe behalten ihren Herkunftsbeleg.');
      onChanged();
    } catch (error) { setNotice((error as Error).message); }
    finally { operationRef.current = false; setBusy(false); }
  };

  return <div className="section-stack">
    <h2>Entwürfe</h2><p className="intro">Schreibproben und Stilprofil bleiben bearbeitbar. Jarvis speichert Entwürfe lokal und versendet keine E-Mails.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Stilprofil</h3><textarea aria-label="Stilprofil" value={profile.description} disabled={profileSaving} onChange={(event) => setStyleForm((current) => ({ ...current, profileTouched: true, profile: { ...current.profile, description: event.target.value } }))} placeholder="z. B. knapp, freundlich, ohne Floskeln" />
      <div className="inline-form"><select aria-label="Cloud-Freigabe Stilprofil" value={profile.policy} disabled={profileSaving} onChange={(event) => setStyleForm((current) => ({ ...current, profileTouched: true, profile: { ...current.profile, policy: event.target.value as Policy } }))}><option value="local_only">Nur lokal</option><option value="cloud_allowed">Cloud erlaubt</option></select><button onClick={() => void saveProfile()} disabled={!profileReady || profileSaving}>Profil speichern</button></div>
      <div className="item-actions"><small>Gespeicherte Revision {profile.revision}</small><button disabled={profileSaving} onClick={() => void reloadProfile()}>Allgemeines Profil neu laden</button></div>
      <StyleHistory history={generalHistory} scopeLabel="Allgemeines Profil" currentRevision={profile.revision}
        touched={styleForm.profileTouched} saving={profileSaving} onRestore={(version) => void restoreProfile(version)} />
      <div className="memory-form"><h4>Stilprofil aus eigenen Proben vorschlagen</h4><p className="muted">Wähle unten 1–3 selbst verfasste Schreibproben. Ein lokaler Ollama-Aufruf betrachtet Ton, Wortwahl, Satzbau und Struktur. Der Vorschlag wird erst nach Übernahme in das allgemeine Profil oder in {personalStyleLabel} und dem jeweiligen Speichern gesichert.</p><label><input type="checkbox" checked={suggestionOwnTextConfirm} disabled={suggestionSaving} onChange={(event) => updateStyleForm({ suggestionOwnTextConfirm: event.target.checked })} /> Ich habe die ausgewählten Schreibproben selbst verfasst.</label><button onClick={() => void suggestProfile()} disabled={busy || provider !== 'ollama' || !suggestionOwnTextConfirm || selectedSamples.length < 1 || selectedSamples.length > 3}>{suggestionSaving ? 'Prüfe Proben…' : 'Stilprofil vorschlagen'}</button>{provider !== 'ollama' && <small>Nur mit lokalem Ollama verfügbar.</small>}{selectedSamples.length > 3 && <small>Bitte höchstens drei Proben auswählen.</small>}
      {suggestion && <><p className="muted">Vorschlag aus {suggestion.samples.map((item) => `${item.title} (SHA-256 ${item.sha256})`).join(' · ')}{suggestion.truncated ? ' · Längere Proben wurden auf 1800 Zeichen gekürzt.' : ''}</p><textarea aria-label="Stilprofil-Vorschlag" maxLength={2000} value={suggestion.suggestion} onChange={(event) => setStyleForm((current) => ({ ...current, suggestion: current.suggestion ? { ...current.suggestion, suggestion: event.target.value } : null }))} /><button onClick={() => { setStyleForm((current) => ({ ...current, profileTouched: true, profile: { ...current.profile, description: current.suggestion?.suggestion.trim() || '', policy: 'local_only' }, suggestion: null })); setNotice('Vorschlag ins bearbeitbare lokale Profil übernommen. Zum Speichern „Profil speichern“ wählen.'); }} disabled={!suggestion.suggestion.trim() || profileSaving}>Ins Stilprofil übernehmen</button></>}</div>
      <h4>Schreibproben</h4><div className="memory-form"><input aria-label="Titel der Schreibprobe" value={name} disabled={sampleSaving} onChange={(event) => updateStyleForm({ name: event.target.value })} placeholder="Titel" /><textarea aria-label="Schreibprobe" value={sampleText} disabled={sampleSaving} onChange={(event) => updateStyleForm({ sampleText: event.target.value })} placeholder="Eigener Beispieltext" /><select aria-label="Cloud-Freigabe Schreibprobe" value={samplePolicy} disabled={sampleSaving} onChange={(event) => updateStyleForm({ samplePolicy: event.target.value as Policy })}><option value="local_only">Nur lokal</option><option value="cloud_allowed">Cloud erlaubt</option></select><button onClick={() => void addSample()} disabled={busy || !name.trim() || !sampleText.trim()}>{sampleSaving ? 'Speichere Probe…' : 'Probe speichern'}</button></div>
      <div className="item-list">{samples.map((sample) => <div key={sample.id} className="item-row"><label><input type="checkbox" checked={selectedSamples.includes(sample.id)} disabled={suggestionSaving} onChange={(event) => { setComposer((current) => ({ ...current, selectedSamples: event.target.checked ? [...current.selectedSamples, sample.id] : current.selectedSamples.filter((id) => id !== sample.id) })); updateStyleForm({ suggestionOwnTextConfirm: false, suggestion: null }); }} /> {sample.title} <small>{sample.policy === 'local_only' ? 'nur lokal' : 'Cloud erlaubt'}{sample.source_path ? ` · aus ${sample.source_path} · SHA-256 ${sample.source_sha256}` : ''}</small></label><button disabled={suggestionSaving} onClick={() => void deleteSample(sample.id)}>Löschen</button></div>)}</div>
      <h4>Eigene Datei als Schreibprobe übernehmen</h4><p className="muted">Zuerst unter „Dokumente“ einen eigenen TXT-/Markdown-Text freigeben und indexieren. Wähle hier genau eine Datei. Jarvis prüft die angezeigte Hash-Fassung erneut und speichert eine lokale Kopie; die Originaldatei bleibt unverändert.</p>
      <div className="memory-form"><select aria-label="Datei für Schreibprobe" value={importDocumentId} disabled={importSaving} onChange={(event) => { const chosen = importable.find((item) => item.id === event.target.value); updateStyleForm({ importDocumentId: event.target.value, importExpectedSha: chosen?.sha256 || '', importTitle: chosen ? chosen.path.split(/[\\/]/).pop()?.replace(/\.(txt|md|markdown)$/i, '') || '' : '', ownTextConfirm: false }); }}><option value="">Datei auswählen</option>{importable.map((item) => <option key={item.id} value={item.id}>{item.path}</option>)}</select><input aria-label="Titel der importierten Schreibprobe" maxLength={200} value={importTitle} disabled={importSaving} onChange={(event) => updateStyleForm({ importTitle: event.target.value })} placeholder="Titel der eigenen Schreibprobe" /><label><input type="checkbox" checked={ownTextConfirm} disabled={importSaving} onChange={(event) => updateStyleForm({ ownTextConfirm: event.target.checked })} /> Ich habe diesen Text selbst verfasst und möchte ihn als lokale Schreibprobe übernehmen.</label><button onClick={() => void importSample()} disabled={busy || !importDocumentId || !importTitle.trim() || !ownTextConfirm || importable.find((item) => item.id === importDocumentId)?.sha256 !== importExpectedSha}>{importSaving ? 'Übernehme Datei…' : 'Eigene Datei übernehmen'}</button></div>
      {importDocumentId && importable.find((item) => item.id === importDocumentId)?.sha256 !== importExpectedSha && <p className="notice">Die indexierte Datei hat sich seit der Auswahl geändert. Bitte erneut auswählen und bestätigen.</p>}
      {importable.length === 0 && <p className="muted">Noch keine passende indexierte TXT-/Markdown-Datei vorhanden.</p>}
    </section>
    <section className="card"><h3>Schreibprofil {personalStyleLabel}</h3>
      <p className="muted">Beschreibe deinen tatsächlichen Schreibstil. Du kannst eigene Schreibproben auswählen und den lokalen Profilvorschlag übernehmen. Das Profil gilt nur bei ausdrücklicher Auswahl im Entwurf. Es gibt keine Garantie gegen KI-Erkennung.</p>
      <textarea aria-label={`Schreibprofil ${personalStyleLabel}`} maxLength={4000} value={namedProfile.description}
        disabled={namedSaving} onChange={(event) => updateStyleForm({ namedTouched: true,
          namedProfile: { ...namedProfile, description: event.target.value } })}
        placeholder="Meine typische Anrede, Satzlänge, Wortwahl und Schlussformel" />
      <div className="inline-form"><select aria-label={`Cloud-Freigabe Schreibprofil ${personalStyleLabel}`}
        value={namedProfile.policy} disabled={namedSaving} onChange={(event) => updateStyleForm({
          namedTouched: true, namedProfile: { ...namedProfile, policy: event.target.value as Policy },
        })}><option value="local_only">Nur lokal</option><option value="cloud_allowed">Cloud erlaubt</option></select>
        <button disabled={!namedReady || namedSaving || !namedProfile.description.trim()}
          onClick={() => void saveNamedProfile()}>{namedSaving ? 'Speichere…' : `${personalStyleLabel} speichern`}</button></div>
      <small>Gespeicherte Fassung {namedProfile.revision || 'noch keine'}.</small>
      <button disabled={namedSaving} onClick={() => void reloadNamedProfile()}>{personalStyleLabel} neu laden</button>
      {suggestion && <button onClick={() => updateStyleForm({ namedTouched: true,
        namedProfile: { ...namedProfile, description: suggestion.suggestion.trim(), policy: 'local_only' },
        suggestion: null })} disabled={!suggestion.suggestion.trim() || namedSaving}>
        Vorschlag in {personalStyleLabel} übernehmen</button>}
    </section>
    <section className="card"><h3>Stilprofil je Textsorte</h3><p className="muted">Für E-Mail, Text, belegte Antwort und Essay kannst du eine eigene Vorgabe speichern. Ohne eigene Vorgabe gilt das gespeicherte allgemeine Stilprofil. Neue Entwürfe übernehmen die beim Start passende Fassung; ältere Entwürfe behalten ihren Stilbeleg.</p>
      <div className="memory-form"><label>Textsorte<select aria-label="Textsorte für Stilprofil" value={typedKind} disabled={typedSaving} onChange={(event) => updateStyleForm({ typedKind: event.target.value as TypedKind })}><option value="email">E-Mail</option><option value="text">Text</option><option value="answer">Belegte Antwort</option><option value="essay">Essay</option></select></label>
        {typedReady && currentTyped && <><p className="muted">{currentTyped.override ? 'Eigene Vorgabe aktiv.' : `Allgemeines Profil gilt: ${currentTyped.effective_description ? currentTyped.effective_description.slice(0, 240) : '(leer)'}`}</p><textarea aria-label="Stilprofil für Textsorte" maxLength={4000} value={currentTyped.description} disabled={typedSaving} onChange={(event) => changeTypedProfile({ description: event.target.value })} placeholder="Eigene Vorgabe für diese Textsorte" /><select aria-label="Cloud-Freigabe Textsortenprofil" value={currentTyped.policy} disabled={typedSaving} onChange={(event) => changeTypedProfile({ policy: event.target.value as Policy })}><option value="local_only">Nur lokal</option><option value="cloud_allowed">Cloud erlaubt</option></select><div className="item-actions"><button onClick={() => void saveTypedProfile()} disabled={typedSaving || !currentTyped.description.trim()}>{typedSaving ? 'Speichere…' : 'Textsorte speichern'}</button>{currentTyped.override && <button onClick={() => void saveTypedProfile(true)} disabled={typedSaving}>Ausnahme entfernen</button>}</div></>}
        {typedReady && currentTyped && <><div className="item-actions"><small>Gespeicherte Revision {currentTyped.revision}</small><button disabled={typedSaving} onClick={() => void reloadTypedProfile()}>Textsorte neu laden</button></div><StyleHistory history={typedHistories[typedKind] || null} scopeLabel={typedKind} currentRevision={currentTyped.revision} touched={styleForm.typedTouched.includes(typedKind)} saving={typedSaving} onRestore={(version) => void restoreTypedProfile(version)} /></>}
        {!typedReady && <p className="muted">Textsortenprofile werden geladen…</p>}</div>
    </section>
    <section className="card"><h3>Neuen Entwurf erstellen</h3><div className="memory-form"><select aria-label="Entwurfstyp" value={kind} onChange={(event) => updateComposer({ kind: event.target.value as Draft['kind'] })}><option value="email">E-Mail</option><option value="text">Text</option><option value="answer">Belegte Antwort</option><option value="essay">Essay</option></select><input aria-label="Schreibauftrag" value={brief} onChange={(event) => updateComposer({ brief: event.target.value })} placeholder="Was soll der Entwurf leisten?" /><textarea aria-label="Eingefügter Kontext" value={inputText} onChange={(event) => updateComposer({ inputText: event.target.value })} placeholder="Optional: eingegangene Nachricht oder weiterer Kontext" />
      <label>Schreibprofil<select aria-label="Schreibprofil für Entwurf" value={namedStyleProfile}
        onChange={(event) => updateComposer({ namedStyleProfile: event.target.value as '' | 'Maxim' })}>
        <option value="">Allgemein / Textsorte</option>
        <option value="Maxim" disabled={!namedReady || namedProfile.revision === 0}>
          {personalStyleLabel}{namedProfile.revision > 0 ? ` · Fassung ${namedProfile.revision}` : ' · zuerst speichern'}
        </option></select></label>
      {namedStyleProfile === 'Maxim' && styleForm.namedTouched && <p className="notice">Ungespeicherte Profiländerungen gelten noch nicht für diesen Entwurf.</p>}
      {kind === 'essay' && <><label>Wortziel (300–1200)<input aria-label="Essay-Wortziel" type="number" min={300} max={1200} step={50} value={wordTarget} onChange={(event) => updateComposer({ wordTarget: event.target.value })} /></label><label>Gliederung (2–6 Zeilen)<textarea aria-label="Essay-Gliederung" value={outlineText} onChange={(event) => updateComposer({ outlineText: event.target.value })} /></label><p className="muted">Für den Essay zuerst ein Stilprofil speichern oder eine Schreibprobe auswählen. Über 600/750/900/1050 Wörter sind mindestens 3/4/5/6 Gliederungspunkte nötig. Bis 900 Wörter sind höchstens 4 lokale Ollama-Aufrufe mit je 360 Sekunden möglich; darüber höchstens 8 mit je 480 Sekunden. Jarvis schreibt jeden Punkt einzeln und ergänzt bei weniger als 80 % des Wortziels gegebenenfalls vorhandene Abschnitte. Auf langsameren Rechnern können lange Essays deutlich länger dauern. Das Wortziel ist ein Richtwert; die tatsächliche Länge, Aufrufzahl und mögliche Teilfehler stehen im Entwurf. Quellen bitte selbst prüfen.</p>{provider !== 'ollama' && <p className="notice error">Essays sind derzeit nur mit lokalem Ollama verfügbar.</p>}</>}
      {selectedSources.length > 0 && <p>{selectedSources.length} Fundstelle(n) ausgewählt. <button onClick={onClearSources}>Auswahl entfernen</button></p>}
      {provider === 'openrouter' && <label><input type="checkbox" checked={allowCloud} onChange={(event) => updateComposer({ allowCloud: event.target.checked })} /> Schreibauftrag, eingefügten Text, aktive Entwurfsregel sowie freigegebene Quellen und Stilangaben an OpenRouter senden</label>}
      <button className="primary" onClick={() => void generate()} disabled={busy || !configured || !brief.trim() || (provider === 'openrouter' && !allowCloud) || (kind === 'essay' && (!essayValid || provider !== 'ollama'))}>{busy ? 'Erstelle…' : lastRequest === requestBody() ? 'Weitere Variante erstellen' : 'Entwurf erstellen'}</button></div></section>
    <section className="card"><h3>Bestätigte Essay-Stilhinweise</h3><p className="muted">Nur diese bewusst gespeicherten Hinweise fließen in künftige lokale Essays ein. Jarvis verwendet höchstens die fünf neuesten. Löschen entfernt einen Hinweis für neue Essays; frühere Entwürfe behalten ihren Herkunftsbeleg.</p>
      <div className="item-list">{essayFeedback.map((item) => <div className="item-row" key={item.id}><span>{item.note} <small>Entwurfsversion {item.draft_version} · {new Date(item.created_at).toLocaleString('de-DE')}</small></span><button disabled={busy} onClick={() => void deleteEssayFeedback(item.id)}>Löschen</button></div>)}</div>
      {essayFeedback.length === 0 && <p className="muted">Noch keine bestätigten Hinweise.</p>}
    </section>
    <section className="card"><h3>Gespeicherte Entwürfe</h3><div className="item-list">{drafts.map((draft) => <button key={draft.id} disabled={busy} onClick={() => selectDraft(draft)}>{draft.kind}: {draft.brief} · {draft.status} · {draft.rule_version != null ? `Entwurfsregel v${draft.rule_version}` : 'alte Fassung ohne Regelbeleg'}</button>)}</div></section>
    {active && <section className="card"><h3>Entwurf bearbeiten</h3><p>{active.uncertainty}</p>{active.kind === 'essay' && <p><strong>Essay:</strong> Wortziel {active.word_target}; aktuell {wordCount(editedContent)} Wörter · Gliederung: {active.outline.join(' · ')}{active.status === 'partial' ? ' · Teilentwurf nach Fehler' : ''}</p>}<p>{active.rule_version != null ? `Erzeugt mit Entwurfsregel v${active.rule_version}` : 'Alte Fassung ohne Entwurfsregel-Beleg'}</p><p>{active.named_style_profile
        ? `Schreibprofil ${active.named_style_profile.name === 'Maxim' ? personalStyleLabel : active.named_style_profile.name} · Fassung ${active.named_style_profile.revision}`
        : 'Allgemeines oder Textsortenprofil'}</p><textarea aria-label="Entwurfstext"  className="draft-editor" value={editedContent} onChange={(event) => setEditedContent(event.target.value)} disabled={busy || active.exports.length > 0} />
      <div className="item-actions"><button onClick={() => void checkWritingSignals()} disabled={busy || !editedContent.trim()}>Schreibmuster prüfen</button>
        <small>Lokale Stilhinweise, kein KI-Detektor und keine Aussage über Autorschaft.</small></div>
      {signalCheck && signalCheck.draftId === active.id && signalCheck.content === editedContent &&
        <div className="memory-form"><h4>Schreibmuster im aktuellen Text</h4>
          {signalCheck.signals.length === 0 ? <p>Keine der geprüften Floskeln gefunden.</p> :
            <ul>{signalCheck.signals.map((signal, index) =>
              <li key={`${signal.id}-${signal.start}-${index}`}><strong>{signal.label}:</strong> „{signal.excerpt}“ · {signal.suggestion}</li>)}</ul>}
          <small>Treffer einzeln prüfen. Eine passende Formulierung im eigenen Stil kann stehen bleiben.</small>
        </div>}
      {active.named_style_profile && provider === 'ollama' && <div className="item-actions">
        <button onClick={() => void polishDraft()} disabled={busy || editedContent !== active.content ||
          active.exports.length > 0 || active.content.length > 8000}>Mit {personalStyleLabel}-Stil überarbeiten</button>
        <small>Lokaler Vorschlag. Fakten und Stil vor dem Speichern prüfen; keine Detektor-Garantie.</small>
      </div>}
      {polishProposal && polishProposal.draftId === active.id &&
        polishProposal.version === active.version && <div className="memory-form">
          <h4>Stilvorschlag prüfen</h4>
          <textarea aria-label={`${personalStyleLabel}-Stilvorschlag`} value={polishProposal.text} readOnly />
          <div className="item-actions"><button onClick={() => {
            setEditedContent(polishProposal.text); setPolishProposal(null);
            setNotice('Vorschlag im Editor. Prüfe Fakten und Stil, dann „Änderung speichern“.');
          }}>In Editor übernehmen</button><button onClick={() => setPolishProposal(null)}>Vorschlag verwerfen</button></div>
        </div>}
      {editedContent !== active.content && <p className="notice">Änderungen zuerst speichern, danach exportieren.</p>}
      <div className="item-actions"><button onClick={() => void saveDraft()} disabled={busy || editedContent === active.content || active.exports.length > 0}>Änderung speichern</button><button onClick={() => void reloadDraft()} disabled={busy}>Serverversion laden</button><button onClick={() => void exportDraft('txt')} disabled={busy || editedContent !== active.content}>TXT speichern</button><button onClick={() => void exportDraft('md')} disabled={busy || editedContent !== active.content}>Markdown speichern</button>{active.kind === 'email' && <button onClick={() => void exportDraft('eml')} disabled={busy || editedContent !== active.content}>EML speichern</button>}</div>
      {active.kind === 'essay' && <div className="memory-form"><h4>Stilkorrektur für spätere Essays</h4><p className="muted">Nur selbst bestätigte Hinweise speichern. Beschreibe konkret, was am Stil anders sein soll; Jarvis übernimmt den Modelltext nicht automatisch als Schreibprobe.</p><textarea aria-label="Essay-Stilhinweis" value={feedbackNote} onChange={(event) => updateComposer({ feedbackNote: event.target.value })} placeholder="Zum Beispiel: Kürzere Sätze und weniger einleitende Floskeln." /><button onClick={() => void saveEssayFeedback()} disabled={busy || editedContent !== active.content || !validStyleFeedback(feedbackNote) || essayFeedback.length >= 50}>Stilhinweis speichern</button>{editedContent !== active.content && <small>Entwurf zuerst speichern, damit der Hinweis zur richtigen Version gehört.</small>}{active.style_feedback.length > 0 && <p>Bei diesem Entwurf verwendet: {active.style_feedback.map((item) => item.note).join(' · ')}</p>}</div>}
      {active.sources.length > 0 && <div><h4>Quellen</h4>{active.sources.map((source, index) => <div className="source-result" key={source.passage_id}><span><strong>[{index + 1}] {source.path}{source.page ? ` · Seite ${source.page}` : ''}</strong><small>SHA-256 {source.sha256}</small><p>{source.text}</p></span></div>)}</div>}
      {active.exports.map((item) => <p key={item.format}>{item.format.toUpperCase()}: {item.path}</p>)}</section>}
  </div>;
}
