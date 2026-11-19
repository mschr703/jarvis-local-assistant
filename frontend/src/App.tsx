import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { api, type Health } from './api';
import type { Source } from './DocumentView';
import type { Draft, DraftComposer, StyleForm } from './DraftView';
import SettingsPanel, { type Settings } from './SettingsPanel';

const ChatView = lazy(() => import('./ChatView'));
const DocumentView = lazy(() => import('./DocumentView'));
const DraftView = lazy(() => import('./DraftView'));
const TaskView = lazy(() => import('./TaskView'));
const ActivityView = lazy(() => import('./ActivityView'));
const WorkStateView = lazy(() => import('./WorkStateView'));
const TimeModelView = lazy(() => import('./TimeModelView'));
const EveningPlanView = lazy(() => import('./EveningPlanView'));
const ResearchView = lazy(() => import('./ResearchView'));
const VaultView = lazy(() => import('./VaultView'));
const ChatRulesView = lazy(() => import('./ChatRulesView'));
const SkillView = lazy(() => import('./SkillView'));
const LearningView = lazy(() => import('./LearningView'));
const MemoryReviewView = lazy(() => import('./MemoryReviewView'));
const FileGraphView = lazy(() => import('./FileGraphView'));
const CodeProjectView = lazy(() => import('./CodeProjectView'));
const ModelChoiceBanner = lazy(() => import('./ModelChoiceBanner'));
const UnifiedSearchView = lazy(() => import('./UnifiedSearchView'));
const ContactView = lazy(() => import('./ContactView'));

type Page = 'status' | 'chat' | 'chat_rules' | 'skills' | 'documents' | 'unified_search' | 'file_graph' | 'code_projects' | 'learning' | 'memory_review' | 'drafts' | 'draft_rules' | 'research' | 'tasks' | 'contacts' | 'time_model' | 'evening_plans' | 'work_states' | 'activity' | 'plan_rules' | 'vault';
type QuickCapture = { text: string; files: string[]; notice: string };
type SearchSourceRequest =
  | { kind: 'project'; id: string; serial: number }
  | { kind: 'document' | 'memory'; id: string; serial: number };
type ReminderCount = { read_at: string | null; focus_suppressed: boolean; focus_deferred_at: string | null; priority: number };
type FocusStatus = { active: boolean };
const pages: [Page, string][] = [['status', 'Übersicht'], ['chat', 'Chat'], ['chat_rules', 'Chat-Regeln'], ['skills', 'Chat-Skills'], ['documents', 'Dokumente'], ['unified_search', 'Suche'], ['file_graph', 'Datei-Graph'], ['code_projects', 'Codeprojekte'], ['learning', 'Lernen'], ['memory_review', 'Gedächtnis'], ['drafts', 'Entwürfe'], ['draft_rules', 'Entwurfsregeln'], ['research', 'Recherche'], ['tasks', 'Aufgaben'], ['contacts', 'Kontakte'], ['time_model', 'Zeitmodell'], ['evening_plans', 'Abendplanung'], ['work_states', 'Arbeitsstände'], ['activity', 'Aktivität'], ['plan_rules', 'Planregeln'], ['vault', 'Zugänge']];

export default function App() {
  const [page, setPage] = useState<Page>('status');
  const [documentsOpened, setDocumentsOpened] = useState(false);
  const [unifiedSearchOpened, setUnifiedSearchOpened] = useState(false);
  const [fileGraphOpened, setFileGraphOpened] = useState(false);
  const [codeProjectsOpened, setCodeProjectsOpened] = useState(false);
  const [learningOpened, setLearningOpened] = useState(false);
  const [memoryReviewOpened, setMemoryReviewOpened] = useState(false);
  const [requestedChatSource, setRequestedChatSource] = useState<{ chatId: string; messageId: string } | null>(null);
  const [requestedChatAttachments, setRequestedChatAttachments] = useState<File[] | null>(null);
  const [tasksOpened, setTasksOpened] = useState(false);
  const [contactsOpened, setContactsOpened] = useState(false);
  const [activityOpened, setActivityOpened] = useState(false);
  const [researchOpened, setResearchOpened] = useState(false);
  const [requestedResearchReport, setRequestedResearchReport] = useState('');
  const [workStatesOpened, setWorkStatesOpened] = useState(false);
  const [timeModelOpened, setTimeModelOpened] = useState(false);
  const [eveningPlansOpened, setEveningPlansOpened] = useState(false);
  const [vaultOpened, setVaultOpened] = useState(false);
  const [chatRulesOpened, setChatRulesOpened] = useState(false);
  const [skillsOpened, setSkillsOpened] = useState(false);
  const [selectedSkillId, setSelectedSkillId] = useState('');
  const [draftRulesOpened, setDraftRulesOpened] = useState(false);
  const [planRulesOpened, setPlanRulesOpened] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settingsDraft, setSettingsDraft] = useState<Settings | null>(null);
  const [selectedSources, setSelectedSources] = useState<Source[]>([]);
  const [requestedSource, setRequestedSource] = useState<SearchSourceRequest | null>(null);
  const sourceOpenSequence = useRef(0);
  const [chatDraft, setChatDraft] = useState('');
  const [suggestedTaskTitle, setSuggestedTaskTitle] = useState('');
  const [suggestedResearchQuestion, setSuggestedResearchQuestion] = useState('');
  const [quickCapture, setQuickCapture] = useState<QuickCapture | null>(null);
  const [captureError, setCaptureError] = useState('');
  const [captureFocus, setCaptureFocus] = useState(0);
  const claimedCapture = useRef('');
  const [draftComposer, setDraftComposer] = useState<DraftComposer>({
      kind: 'email', brief: '', inputText: '', namedStyleProfile: '',
      selectedSamples: [], allowCloud: false,
      recipientContactId: '', recipientExpectedVersion: null, recipientNoteIds: [],
    wordTarget: '600', outlineText: 'Einleitung\nHauptteil\nSchluss', feedbackNote: '',
  });
  const [styleForm, setStyleForm] = useState<StyleForm>({
      profile: { description: '', policy: 'local_only', revision: 0 }, profileReady: false,
    profileSaving: false, profileTouched: false,
      namedProfile: { description: '', policy: 'local_only', revision: 0 },
      namedReady: false, namedSaving: false, namedTouched: false,
    typedProfiles: {}, typedKind: 'email', typedTouched: [], typedReady: false,
    typedSaving: false,
    name: '', sampleText: '', samplePolicy: 'local_only', sampleSaving: false,
    importDocumentId: '', importExpectedSha: '', importTitle: '',
    ownTextConfirm: false, importSaving: false,
    suggestion: null, suggestionSaving: false, suggestionOwnTextConfirm: false,
  });
  const [activeDraft, setActiveDraft] = useState<Draft | null>(null);
  const [editedDraftContent, setEditedDraftContent] = useState('');
  const [lastDraftRequest, setLastDraftRequest] = useState<string | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftNotice, setDraftNotice] = useState('');
  const [draftRevision, setDraftRevision] = useState(0);
  const draftOperationRef = useRef(false);
  const styleProfileEpoch = useRef(0);
  const [unreadReminders, setUnreadReminders] = useState(0);
  const [deferredReminders, setDeferredReminders] = useState(0);
  const [focusActive, setFocusActive] = useState(false);
  const healthRequest = useRef(0);
  const unreadRequest = useRef(0);
  const modelReady = Boolean(health?.configured) && !error;
  const navigate = (target: Page, fromSearchSource = false) => {
    if (!fromSearchSource && (target === 'documents' || target === 'work_states'))
      setRequestedSource(null);
    if (target === 'documents') setDocumentsOpened(true);
    if (target === 'unified_search') setUnifiedSearchOpened(true);
    if (target === 'file_graph') setFileGraphOpened(true);
    if (target === 'code_projects') setCodeProjectsOpened(true);
    if (target === 'learning') setLearningOpened(true);
    if (target === 'memory_review') setMemoryReviewOpened(true);
    if (target === 'tasks') setTasksOpened(true);
    if (target === 'contacts') setContactsOpened(true);
    if (target === 'activity') setActivityOpened(true);
    if (target === 'research') setResearchOpened(true);
    if (target === 'work_states') setWorkStatesOpened(true);
    if (target === 'time_model') setTimeModelOpened(true);
    if (target === 'evening_plans') setEveningPlansOpened(true);
    if (target === 'vault') setVaultOpened(true);
    if (target === 'chat_rules') setChatRulesOpened(true);
    if (target === 'skills') setSkillsOpened(true);
    if (target === 'draft_rules') setDraftRulesOpened(true);
    if (target === 'plan_rules') setPlanRulesOpened(true);
    setPage(target);
  };

  const refreshHealth = useCallback(() => {
    const request = ++healthRequest.current;
    void fetch('/api/health').then(async (response) => {
      if (!response.ok) throw new Error(`Backend antwortet mit HTTP ${response.status}`);
      return response.json() as Promise<Health>;
    }).then((data) => {
      if (request === healthRequest.current) { setHealth(data); setError(null); }
    }).catch((cause: Error) => {
      if (request === healthRequest.current) setError(cause.message);
    });
  }, []);
  const refreshUnreadReminders = useCallback(() => {
    const request = ++unreadRequest.current;
    void Promise.all([api<ReminderCount[]>('/notifications'), api<FocusStatus>('/focus')])
      .then(([items, focus]) => {
        if (request === unreadRequest.current) {
          setUnreadReminders(items.filter((item) => !item.read_at && !item.focus_suppressed).length);
          setDeferredReminders(items.filter((item) => !item.read_at && item.focus_deferred_at && item.priority < 4).length);
          setFocusActive(focus.active);
        }
      }).catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshHealth();
    const timer = window.setInterval(refreshHealth, 10_000);
    return () => { ++healthRequest.current; window.clearInterval(timer); };
  }, [refreshHealth]);

  useEffect(() => {
    refreshUnreadReminders();
    const timer = window.setInterval(refreshUnreadReminders, 15_000);
    return () => { ++unreadRequest.current; window.clearInterval(timer); };
  }, [refreshUnreadReminders]);

  useEffect(() => {
    const token = new URL(window.location.href).searchParams.get('capture');
    if (!token || claimedCapture.current === token) return;
    claimedCapture.current = token;
    window.history.replaceState(null, '', window.location.pathname + window.location.hash);
    void api<QuickCapture>(`/quick-captures/${encodeURIComponent(token)}`).then((capture) => {
      setQuickCapture(capture);
      setCaptureError('');
      if (capture.text) setChatDraft((current) => current ? `${current}\n\n${capture.text}` : capture.text);
      setPage('chat');
      setCaptureFocus((current) => current + 1);
    }).catch((cause: Error) => {
      setCaptureError(cause.message);
      setPage('chat');
      setCaptureFocus((current) => current + 1);
    });
  }, []);

  return <div className="layout">
    <aside className="sidebar">
      <h1>Jarvis</h1>
      <p>Persönlicher Assistent · lokal</p>
      <nav aria-label="Bereiche">{pages.map(([id, label]) => <button key={id} className={page === id ? 'selected' : ''} onClick={() => navigate(id)}>{label}{id === 'tasks' && unreadReminders > 0 ? ` · ${unreadReminders} neu` : ''}</button>)}</nav>
    </aside>
    <main>
      {page === 'chat' && (quickCapture || captureError) && <section className="card" role="status">
        <div className="card-heading"><h3>Hotkey-Auswahl</h3><button onClick={() => { setQuickCapture(null); setCaptureError(''); }}>Schließen</button></div>
        {captureError && <p className="notice error">{captureError}</p>}
        {quickCapture?.notice && <p>{quickCapture.notice}</p>}
        {quickCapture?.text && <p>Markierter Text liegt im Chat-Eingabefeld. Erst „Senden“ startet eine Modellanfrage.</p>}
        {quickCapture && !quickCapture.text && !quickCapture.files.length && <p>Keine unterstützte Auswahl gefunden. Text hier eingeben oder in der Quellanwendung markieren und den Hotkey erneut drücken.</p>}
        {quickCapture && quickCapture.files.length > 0 && <>
          <p>Ausgewählte Dateien wurden nicht gelesen. Gib ihren Ordner unter „Dokumente“ frei und indexiere ihn, bevor du Dateiinhalte verwendest.</p>
          <ul>{quickCapture.files.map((path) => <li key={path}><code>{path}</code></li>)}</ul>
          <button onClick={() => navigate('documents')}>Dokumente öffnen</button>
        </>}
      </section>}
      {focusActive && page !== 'tasks' && <section className="card muted"><strong>Fokusmodus aktiv</strong><p>Normale Erinnerungen warten gesammelt. Wichtige Hinweise bleiben sichtbar.</p>{deferredReminders > 0 && <button onClick={() => navigate('tasks')}>{deferredReminders} gesammelte Erinnerung{deferredReminders === 1 ? '' : 'en'} ansehen</button>}</section>}
      {unreadReminders > 0 && page !== 'tasks' && <section className="card" role="status"><strong>{unreadReminders} neue Erinnerung{unreadReminders === 1 ? '' : 'en'}{!focusActive && deferredReminders > 0 ? ` · davon ${deferredReminders} im Fokus gesammelt` : ''}</strong> <button onClick={() => navigate('tasks')}>Erinnerungen ansehen</button></section>}
      <div hidden={page !== 'status'}>
        <p className="eyebrow">SYSTEMSTATUS</p>
        <h2>{error || health?.status === 'degraded' ? 'Ein Dienst ist ausgefallen.' : health?.configured ? 'Jarvis ist bereit.' : 'Jarvis einrichten.'}</h2>
        <p className="intro">Chat, Dokumente, Entwürfe, Aufgaben und Werkzeuge laufen auf diesem Laptop. Der aktuelle Modellstatus steht hier.</p>
        <section className="card" aria-live="polite">
          <div className="card-heading"><h3>Verbindung</h3><span className={error || health?.status === 'degraded' ? 'badge error' : health?.configured ? 'badge ok' : 'badge pending'}>{error ? 'Offline' : health?.status === 'degraded' ? 'Dienst gestört' : health?.configured ? 'Modell bereit' : health ? 'Einrichtung nötig' : 'Prüfe…'}</span></div>
          {error ? <p>Backend nicht erreichbar: {error}. Starte Jarvis mit dem Befehl aus der README.</p> :
            health ? <><p>{health.message}</p><dl><dt>Provider</dt><dd>{health.provider}</dd><dt>Modell</dt><dd>{health.model ?? 'Nicht eingerichtet'}</dd></dl></> : <p>Verbindung wird geprüft.</p>}
          <div className="item-actions"><button onClick={refreshHealth}>Status prüfen</button></div>
        </section>
        <section className="card muted"><h3>Einrichtung</h3><p>Der Chat antwortet erst, wenn ein lokales Ollama-Modell läuft oder OpenRouter mit Schlüssel und Modell eingerichtet ist. Es werden keine Beispielantworten als echte Ergebnisse gezeigt.</p></section>
        <SettingsPanel healthStatus={health?.status || 'starting'} visible={page === 'status'} onSaved={refreshHealth} settings={settingsDraft} setSettings={setSettingsDraft} />
      </div>
      <Suspense fallback={<section className="card" role="status">Bereich wird geladen…</section>}>
      {['chat', 'drafts', 'research', 'activity'].includes(page) &&
        <ModelChoiceBanner key={page} task={page === 'drafts' ? 'draft' : page === 'research' ? 'research' : page === 'activity' ? 'plan' : 'chat'} />}
      {page === 'status' ? null : page === 'chat' ? <ChatView configured={modelReady} setupMessage={error || health?.message || 'Verbindung wird geprüft'} provider={health?.provider || 'ollama'} draft={chatDraft} setDraft={setChatDraft} focusOnCapture={captureFocus} selectedSkillId={selectedSkillId} setSelectedSkillId={setSelectedSkillId} onSkills={() => navigate('skills')} onPrepareTask={(title) => { setSuggestedTaskTitle(title); navigate('tasks'); }} onPrepareResearch={(question) => { setSuggestedResearchQuestion(question); navigate('research'); }} requestedChatSource={requestedChatSource} onSourceOpened={() => setRequestedChatSource(null)} requestedAttachments={requestedChatAttachments} onAttachmentsPrepared={() => setRequestedChatAttachments(null)} /> :
        page === 'documents' ? null :
        page === 'unified_search' ? null :
        page === 'file_graph' ? null :
        page === 'code_projects' ? null :
        page === 'learning' ? null :
        page === 'memory_review' ? null :
        page === 'chat_rules' ? null :
        page === 'skills' ? null :
        page === 'draft_rules' ? null :
        page === 'drafts' ? <DraftView personalStyleLabel={settingsDraft?.personal_style_label || 'Mein Stil'} selectedSources={selectedSources} onClearSources={() => setSelectedSources([])} configured={modelReady} provider={health?.provider || 'ollama'} composer={draftComposer} setComposer={setDraftComposer} active={activeDraft} setActive={setActiveDraft} editedContent={editedDraftContent} setEditedContent={setEditedDraftContent} lastRequest={lastDraftRequest} setLastRequest={setLastDraftRequest} busy={draftBusy} setBusy={setDraftBusy} notice={draftNotice} setNotice={setDraftNotice} operationRef={draftOperationRef} profileEpoch={styleProfileEpoch} revision={draftRevision} onChanged={() => setDraftRevision((current) => current + 1)} styleForm={styleForm} setStyleForm={setStyleForm} /> :
        page === 'research' ? null :
        page === 'tasks' ? null :
        page === 'contacts' ? null :
        page === 'time_model' ? null :
        page === 'evening_plans' ? null :
        page === 'work_states' ? null :
        page === 'activity' ? null :
        page === 'vault' ? null :
        <><h2>{pages.find(([id]) => id === page)?.[1]}</h2><p className="intro">Dieser Bereich wird derzeit implementiert. Es sind noch keine Daten oder Aktionen verfügbar.</p></>}
      {documentsOpened && <div hidden={page !== 'documents'}><DocumentView visible={page === 'documents'} requestedSource={requestedSource?.kind === 'document' || requestedSource?.kind === 'memory' ? requestedSource : null} onDraft={(sources) => { setSelectedSources(sources); navigate('drafts'); }} onLearn={(sources) => { setSelectedSources(sources); navigate('learning'); }} onSourcesInvalidated={() => setSelectedSources([])} /></div>}
      {unifiedSearchOpened && <div hidden={page !== 'unified_search'}><UnifiedSearchView onDraft={(sources) => { setSelectedSources(sources); navigate('drafts'); }} onOpenSource={(kind, id) => { setRequestedSource({ kind, id, serial: ++sourceOpenSequence.current }); navigate(kind === 'project' ? 'work_states' : 'documents', true); }} localChatReady={modelReady && health?.provider === 'ollama'} onLocalChat={(files) => { setRequestedChatSource(null); setRequestedChatAttachments(files); navigate('chat'); }} /></div>}
      {fileGraphOpened && <div hidden={page !== 'file_graph'}><FileGraphView visible={page === 'file_graph'} onDocuments={() => navigate('documents')} /></div>}
      {codeProjectsOpened && <div hidden={page !== 'code_projects'}><CodeProjectView visible={page === 'code_projects'} onDocuments={() => navigate('documents')} /></div>}
      {learningOpened && <div hidden={page !== 'learning'}><LearningView visible={page === 'learning'} sources={selectedSources} configured={modelReady} provider={health?.provider || 'ollama'} onDocuments={() => navigate('documents')} /></div>}
      {memoryReviewOpened && <div hidden={page !== 'memory_review'}><MemoryReviewView visible={page === 'memory_review'} configured={modelReady} provider={health?.provider || 'ollama'} onChat={(chatId, messageId) => { setRequestedChatSource({ chatId, messageId }); navigate('chat'); }} /></div>}
      {researchOpened && <div hidden={page !== 'research'}><ResearchView visible={page === 'research'} configured={modelReady} provider={health?.provider || 'ollama'} requestedReportId={requestedResearchReport} onReportLoaded={() => setRequestedResearchReport('')} suggestedQuestion={suggestedResearchQuestion} onSuggestionTaken={() => setSuggestedResearchQuestion('')} /></div>}
      {tasksOpened && <div hidden={page !== 'tasks'}><TaskView visible={page === 'tasks'} onNotificationsChanged={refreshUnreadReminders} suggestedTitle={suggestedTaskTitle} onSuggestionTaken={() => setSuggestedTaskTitle('')} /></div>}
      {timeModelOpened && <div hidden={page !== 'time_model'}><TimeModelView visible={page === 'time_model'} /></div>}
      {eveningPlansOpened && <div hidden={page !== 'evening_plans'}><EveningPlanView visible={page === 'evening_plans'} /></div>}
      {workStatesOpened && <div hidden={page !== 'work_states'}><WorkStateView visible={page === 'work_states'} requestedSource={requestedSource?.kind === 'project' ? requestedSource : null} onTasks={() => navigate('tasks')} onResearch={(reportId) => { setRequestedResearchReport(reportId); navigate('research'); }} /></div>}
      {activityOpened && <div hidden={page !== 'activity'}><ActivityView provider={health?.provider || 'ollama'} visible={page === 'activity'} onChatEvidence={(chatId, messageId) => { setRequestedChatSource({ chatId, messageId }); navigate('chat'); }} onResearchEvidence={(reportId) => { setRequestedResearchReport(reportId); navigate('research'); }} /></div>}
      {vaultOpened && <div hidden={page !== 'vault'}><VaultView visible={page === 'vault'} onChanged={refreshHealth} /></div>}
      {chatRulesOpened && <div hidden={page !== 'chat_rules'}><ChatRulesView visible={page === 'chat_rules'} /></div>}
      {skillsOpened && <div hidden={page !== 'skills'}><SkillView visible={page === 'skills'} /></div>}
      {contactsOpened && <div hidden={page !== 'contacts'}><ContactView visible={page === 'contacts'} onPrepareTask={(title) => { setSuggestedTaskTitle(title); navigate('tasks'); }} /></div>}
      {draftRulesOpened && <div hidden={page !== 'draft_rules'}><ChatRulesView visible={page === 'draft_rules'} scope="draft" /></div>}
      {planRulesOpened && <div hidden={page !== 'plan_rules'}><ChatRulesView visible={page === 'plan_rules'} scope="plan" /></div>}
      </Suspense>
    </main>
  </div>;
}
