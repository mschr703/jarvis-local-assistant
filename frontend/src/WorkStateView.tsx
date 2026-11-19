import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type FileRef = { id: string; path: string; sha256: string; status: string };
type TaskRef = { id: string; title: string; status: string; current_title: string | null;
  due_utc?: string | null; due_local?: string | null; timezone?: string | null;
  due_offset?: string | null; due_fold?: number | null;
  current_due_utc: string | null; current_due_local: string | null;
  current_timezone: string | null; current_due_offset: string | null;
  current_due_fold: number | null; due_status: string };
type WorkState = { id: string; project: string; confirmed: string; open_questions: string; next_step: string;
  documents: FileRef[]; tasks: TaskRef[]; links: string[]; version: number; created_at: string; updated_at: string };
type WorkSummary = Pick<WorkState, 'id' | 'project' | 'next_step' | 'version' | 'updated_at'>;
type ProjectCheck = { state: WorkState; ready: boolean; blockers: string[];
  prepared_task_id: string | null; prepared_task: Task | null; already_prepared: boolean;
  open_tasks: number; done_tasks: number; missing_tasks: number };
type ReviewSchedule = { interval_days: number; local_time: string; timezone: string;
  next_local_date: string; paused: boolean; last_status: string | null; last_error: string | null };
type ReviewSummary = { project: string; state_version: number; confirmed: string;
  open_questions: string; next_step: string; blockers: string[];
  open_tasks: number; done_tasks: number; missing_tasks: number;
  documents: FileRef[]; tasks: TaskRef[] };
type ReviewReport = { id: string; local_date: string; kind: string; status: string;
  changed: number | null; error: string | null; created_at: string; summary: ReviewSummary | null };
type ProjectResearch = { report_id: string; question: string; interpretation: string;
  provider: string; model: string; created_at: string; source_count: number; project_version: number };
type PrepareResult = { task: Task | null; task_id: string; reused: boolean; check: ProjectCheck };
type Document = { id: string; path: string; excluded: number; status: string };
type Task = { id: string; title: string; status: string };
type MemorySearchResult = { id: string; text: string; source_ref: string;
  policy: 'local_only' | 'cloud_allowed' };
type ProjectMemoryLink = { id: string; memory_id: string; memory_text: string;
  memory_source_ref: string; memory_policy: 'local_only' | 'cloud_allowed';
  memory_sha256: string; memory_status: 'unveraendert' | 'geaendert' | 'geloescht';
  current_memory_text: string | null; current_memory_source_ref: string | null;
  current_memory_policy: string | null; source_note: string;
  confidence: ProjectPerson['confidence']; valid_from: string | null;
  valid_until: string | null; validity_status: string; version: number };
type ProjectPerson = { id: string; name: string; relation_type: 'lead' | 'contributor' | 'contact' | 'reviewer' | 'other';
  source_note: string; source_document_id: string | null; source_path: string | null;
  source_sha256: string | null; source_status: string; version: number;
  confidence: 'unverified' | 'plausible' | 'confirmed'; valid_from: string | null;
  valid_until: string | null; validity_status: string };
type PersonAlias = { id: string; person_id: string; person_name: string; person_version: number;
  alias: string; source_note: string; source_document_id: string | null;
  source_path: string | null; source_sha256: string | null; source_status: string;
  confidence: ProjectPerson['confidence']; valid_from: string | null; valid_until: string | null;
  validity_status: string; version: number };
type PersonMerge = { id: string; source_person_id: string; target_person_id: string;
  source_name: string; source_relation_type: ProjectPerson['relation_type'];
  target_name: string | null; canonical_alias_id: string;
  target_version_after: number; moved_alias_count: number; moved_link_count: number;
  created_at: string; split_at: string | null };
type PersonTaskLink = { id: string; person_id: string; person_name: string; person_source_status: string;
  task_id: string; task_title: string; current_task_title: string | null; task_status: string;
  relation_type: 'responsible' | 'involved' | 'reviewer'; source_note: string;
  source_document_id: string | null; source_path: string | null; source_sha256: string | null;
  source_status: string; status: string; version: number;
  confidence: ProjectPerson['confidence']; valid_from: string | null; valid_until: string | null;
  validity_status: string; person_validity_status: string;
  merged_from_person_name: string | null; merged_from_source_status: string | null;
  merged_from_validity_status: string | null; merged_from_confidence: ProjectPerson['confidence'] | null };
type ProjectConnections = { project_id: string; project: string; state_version: number; checked_at: string;
  timezone: string; as_of_local_date: string;
  people: (ProjectPerson & { source_kind: 'own_statement' | 'current_document' | 'stale_document' })[];
  documents: FileRef[]; tasks: TaskRef[]; person_tasks: PersonTaskLink[]; aliases: PersonAlias[];
  memories: ProjectMemoryLink[] };
type GraphNode = { id: string; kind: 'project' | 'person' | 'task' | 'document' | 'alias' | 'memory' | 'deadline';
  label: string; status?: string; source_status?: string; validity_status?: string };
type GraphEdge = { id: string; from: string; to: string; relation: string; role: string | null;
  source_note: string | null; source_status: string | null; source_document_id: string | null;
  source_sha256: string | null; source_path: string | null;
  confidence: ProjectPerson['confidence'] | null;
  validity_status: string | null; status: string | null;
  merged_from_person_name: string | null; merged_from_source_status: string | null;
  merged_from_validity_status: string | null;
  memory_source_ref: string | null; memory_policy: MemorySearchResult['policy'] | null;
  memory_sha256: string | null };
type GraphSnapshot = { project_id: string; state_version: number; checked_at: string;
  nodes: GraphNode[]; edges: GraphEdge[] };
type GraphNeighbors = { node: GraphNode; neighbors: GraphNode[]; edges: GraphEdge[]; checked_at: string };
type GraphPaths = { source: GraphNode; target: GraphNode; max_depth: number;
  paths: { nodes: GraphNode[]; edges: GraphEdge[] }[]; truncated: boolean; checked_at: string };
const graphRelations: Record<string, string> = {
  project_person: 'Projekt → Person', project_task: 'Projekt → Aufgabe',
  project_document: 'Projekt → Datei', person_task: 'Aufgabe → Person',
  person_evidence: 'Person → Dateibeleg', person_alias: 'Person → Alias',
  alias_evidence: 'Alias → Dateibeleg', task_evidence: 'Aufgabe → Dateibeleg',
  project_memory: 'Projekt → Erinnerung', task_deadline: 'Aufgabe → Frist' };
const dueStatusLabels: Record<string, string> = {
  unveraendert: 'Frist unverändert', ohne_frist: 'Ohne Frist', hinzugefuegt: 'Frist hinzugefügt',
  entfernt: 'Frist entfernt', geaendert: 'Frist seit Zuordnung geändert',
  aufgabe_geloescht: 'Aufgabe gelöscht', altbestand_ohne_snapshot: 'Älterer Arbeitsstand ohne Fristbeleg' };
const dueText = (local?: string | null, zone?: string | null, offset?: string | null, fold?: number | null) =>
  local ? `${local.replace('T', ' ')} · ${zone || 'Zeitzone unbekannt'}${offset ? ` · UTC${offset}` : ''}${fold === null || fold === undefined ? '' : ` · Winterzeit-Vorkommen ${fold + 1}`}` : 'Keine Frist';
type MemoryForm = { memory_id: string; source_note: string; confidence: ProjectPerson['confidence'];
  valid_from: string; valid_until: string };
const emptyMemoryForm = (): MemoryForm => ({ memory_id: '', source_note: '', confidence: 'unverified',
  valid_from: '', valid_until: '' });
const memoryStatusLabels: Record<ProjectMemoryLink['memory_status'], string> = {
  unveraendert: 'Erinnerung unverändert', geaendert: 'Erinnerung seit Zuordnung geändert',
  geloescht: 'Ursprüngliche Erinnerung gelöscht' };
const memoryPolicyLabels: Record<MemorySearchResult['policy'], string> = {
  local_only: 'Nur lokal', cloud_allowed: 'Für Cloud freigegeben' };
type PersonForm = Pick<ProjectPerson, 'name' | 'relation_type' | 'source_note' | 'confidence'> &
  { source_document_id: string; valid_from: string; valid_until: string };
const emptyPersonForm = (): PersonForm => ({ name: '', relation_type: 'contact', source_note: '',
  source_document_id: '', confidence: 'unverified', valid_from: '', valid_until: '' });
type AliasForm = { person_id: string; alias: string; source_note: string; source_document_id: string;
  confidence: ProjectPerson['confidence']; valid_from: string; valid_until: string };
const emptyAliasForm = (): AliasForm => ({ person_id: '', alias: '', source_note: '', source_document_id: '',
  confidence: 'unverified', valid_from: '', valid_until: '' });
type PersonTaskForm = Pick<PersonTaskLink, 'person_id' | 'task_id' | 'relation_type' | 'source_note' | 'confidence'> &
  { source_document_id: string };
type PersonTaskDates = { valid_from: string; valid_until: string };
const emptyPersonTaskForm = (): PersonTaskForm & PersonTaskDates => ({ person_id: '', task_id: '', relation_type: 'involved',
  source_note: '', source_document_id: '', confidence: 'unverified', valid_from: '', valid_until: '' });
const confidenceLabels: Record<ProjectPerson['confidence'], string> = {
  unverified: 'Ungeprüft', plausible: 'Plausibel (eigene Einschätzung)', confirmed: 'Bestätigt (eigene Einschätzung)' };
const validityLabels: Record<string, string> = { aktuell: 'Derzeit gültig',
  noch_nicht_gueltig: 'Noch nicht gültig', abgelaufen: 'Abgelaufen' };
const personTaskRelationLabels: Record<PersonTaskLink['relation_type'], string> = {
  responsible: 'Verantwortlich', involved: 'Beteiligt', reviewer: 'Prüfend' };
const personTaskStatusLabels: Record<string, string> = { aktuell: 'Aktuelle Zuordnung',
  aufgabe_nicht_mehr_zugeordnet: 'Aufgabe nicht mehr im Projekt', aufgabe_geloescht: 'Aufgabe gelöscht',
  beleg_pruefen: 'Dateibeleg prüfen', person_beleg_pruefen: 'Personenbeleg prüfen',
  person_nicht_gueltig: 'Personenbeziehung zeitlich nicht gültig',
  zusammengefuehrter_person_beleg_pruefen: 'Ursprünglichen Personenbeleg prüfen',
  zusammengefuehrte_person_nicht_gueltig: 'Ursprüngliche Personenbeziehung zeitlich nicht gültig',
  noch_nicht_gueltig: 'Noch nicht gültig', abgelaufen: 'Abgelaufen' };
const relationLabels: Record<ProjectPerson['relation_type'], string> = { lead: 'Leitung', contributor: 'Mitarbeit',
  contact: 'Kontakt', reviewer: 'Prüfung', other: 'Andere Beziehung' };
const graphRoleLabels: Record<string, Record<string, string>> = {
  project_person: relationLabels, person_task: personTaskRelationLabels };
const personSourceLabels: Record<string, string> = { eigene_angabe: 'Eigene Angabe ohne Dateibeleg',
  nicht_mehr_zugeordnet: 'Quelldatei nicht mehr im Arbeitsstand',
  quelle_erneuert: 'Quelldatei neu bestätigt; Beziehung noch nicht erneut geprüft' };
type Form = { project: string; confirmed: string; open_questions: string; next_step: string;
  document_ids: string[]; task_ids: string[]; links: string };
const emptyForm = (): Form => ({ project: '', confirmed: '', open_questions: '', next_step: '',
  document_ids: [], task_ids: [], links: '' });
const fileStatus: Record<string, string> = { unveraendert: 'Unverändert', geaendert: 'Seit dem Sichern geändert',
  fehlt: 'Datei fehlt', ausgeschlossen: 'Ausgeschlossen', freigabe_entfernt: 'Freigabe entfernt',
  pfad_geaendert: 'Pfad geändert', index_ausstehend: 'Index ausstehend', nicht_verfuegbar: 'Nicht verfügbar' };

export default function WorkStateView({ visible, onTasks, onResearch, requestedSource }: {
  visible: boolean; onTasks: () => void; onResearch: (reportId: string) => void;
  requestedSource: { kind: 'project'; id: string; serial: number;
    match?: { kind: 'document' | 'task' | 'alias' | 'person' | 'memory'; id: string } } | null }) {
  const [items, setItems] = useState<WorkSummary[]>([]);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [people, setPeople] = useState<ProjectPerson[]>([]);
  const [memoryLinks, setMemoryLinks] = useState<ProjectMemoryLink[]>([]);
  const [memorySearch, setMemorySearch] = useState('');
  const [memoryCandidates, setMemoryCandidates] = useState<MemorySearchResult[]>([]);
  const [memoryForm, setMemoryForm] = useState<MemoryForm>(emptyMemoryForm);
  const [editingMemoryLinkId, setEditingMemoryLinkId] = useState<string | null>(null);
  const [memoryDeleteCandidate, setMemoryDeleteCandidate] = useState<string | null>(null);
  const [aliases, setAliases] = useState<PersonAlias[]>([]);
  const [personMerges, setPersonMerges] = useState<PersonMerge[]>([]);
  const [mergeSourceId, setMergeSourceId] = useState('');
  const [mergeTargetId, setMergeTargetId] = useState('');
  const [mergeConfirm, setMergeConfirm] = useState(false);
  const [splitCandidate, setSplitCandidate] = useState<string | null>(null);
  const [aliasForm, setAliasForm] = useState<AliasForm>(emptyAliasForm);
  const [editingAliasId, setEditingAliasId] = useState<string | null>(null);
  const [aliasDeleteCandidate, setAliasDeleteCandidate] = useState<string | null>(null);
  const [personTasks, setPersonTasks] = useState<PersonTaskLink[]>([]);
  const [personTaskForm, setPersonTaskForm] = useState<PersonTaskForm & PersonTaskDates>(emptyPersonTaskForm);
  const [editingPersonTaskId, setEditingPersonTaskId] = useState<string | null>(null);
  const [personTaskDeleteCandidate, setPersonTaskDeleteCandidate] = useState<string | null>(null);
  const [connections, setConnections] = useState<ProjectConnections | null>(null);
  const [graph, setGraph] = useState<GraphSnapshot | null>(null);
  const [graphSource, setGraphSource] = useState('');
  const [graphTarget, setGraphTarget] = useState('');
  const [graphDepth, setGraphDepth] = useState(3);
  const [graphDirection, setGraphDirection] = useState<'in' | 'out' | 'both'>('both');
  const [graphFilters, setGraphFilters] = useState<string[]>([]);
  const [graphNeighbors, setGraphNeighbors] = useState<GraphNeighbors | null>(null);
  const [graphPaths, setGraphPaths] = useState<GraphPaths | null>(null);
  const [personForm, setPersonForm] = useState<PersonForm>(emptyPersonForm);
  const [editingPersonId, setEditingPersonId] = useState<string | null>(null);
  const [personDeleteCandidate, setPersonDeleteCandidate] = useState<string | null>(null);
  const [active, setActive] = useState<WorkState | null>(null);
  const [projectCheck, setProjectCheck] = useState<ProjectCheck | null>(null);
  const [reviewSchedule, setReviewSchedule] = useState<ReviewSchedule | null>(null);
  const [reviewReports, setReviewReports] = useState<ReviewReport[]>([]);
  const [researchReports, setResearchReports] = useState<ProjectResearch[]>([]);
  const [reviewForm, setReviewForm] = useState({ interval_days: 1, local_time: '09:00',
    timezone: 'Europe/Paris', paused: false });
  const [form, setForm] = useState<Form>(emptyForm);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [notice, setNotice] = useState('');
  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const request = useRef(0);
  const lastSourceScroll = useRef<number | null>(null);
  const [loadedSourceSerial, setLoadedSourceSerial] = useState<number | null>(null);

  const loadReviews = async (id: string) => {
    const [schedule, reports, research] = await Promise.all([
      api<ReviewSchedule | null>(`/work-states/${id}/reviews/schedule`),
      api<ReviewReport[]>(`/work-states/${id}/reviews`),
      api<ProjectResearch[]>(`/work-states/${id}/research`),
    ]);
    setReviewSchedule(schedule); setReviewReports(reports); setResearchReports(research);
    if (schedule) setReviewForm({ interval_days: schedule.interval_days,
      local_time: schedule.local_time, timezone: schedule.timezone, paused: schedule.paused });
  };
  const loadPeople = async (id: string) => {
    setConnections(null); setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    setPeople(await api<ProjectPerson[]>(`/work-states/${id}/people`));
  };
  const loadMemoryLinks = async (id: string) => {
    setConnections(null); setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    setMemoryLinks(await api<ProjectMemoryLink[]>(`/work-states/${id}/memory-links`));
  };
  const loadAliases = async (id: string) => {
    setConnections(null); setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    setAliases(await api<PersonAlias[]>(`/work-states/${id}/aliases`));
  };
  const loadPersonMerges = async (id: string) => {
    setPersonMerges(await api<PersonMerge[]>(`/work-states/${id}/person-merges`));
  };
  const loadPersonTasks = async (id: string) => {
    setConnections(null); setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    setPersonTasks(await api<PersonTaskLink[]>(`/work-states/${id}/person-tasks`));
  };

  const refresh = async (skipActive = false) => {
    setConnections(null); setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    const current = ++request.current;
    const [nextItems, nextDocuments, nextTasks] = await Promise.all([
      api<WorkSummary[]>('/work-states'), api<Document[]>('/documents'), api<Task[]>('/tasks'),
    ]);
    if (current !== request.current) return;
    setItems(nextItems); setDocuments(nextDocuments); setTasks(nextTasks);
    if (active && !skipActive) {
      const [refreshed, schedule, reports, research, refreshedPeople, refreshedPersonTasks, refreshedAliases, refreshedMerges, refreshedMemories] = await Promise.all([
        api<ProjectCheck>(`/work-states/${active.id}/check`),
        api<ReviewSchedule | null>(`/work-states/${active.id}/reviews/schedule`),
        api<ReviewReport[]>(`/work-states/${active.id}/reviews`),
        api<ProjectResearch[]>(`/work-states/${active.id}/research`),
        api<ProjectPerson[]>(`/work-states/${active.id}/people`),
        api<PersonTaskLink[]>(`/work-states/${active.id}/person-tasks`),
        api<PersonAlias[]>(`/work-states/${active.id}/aliases`),
        api<PersonMerge[]>(`/work-states/${active.id}/person-merges`),
        api<ProjectMemoryLink[]>(`/work-states/${active.id}/memory-links`),
      ]);
      if (current === request.current && !editingId) {
        setActive(refreshed.state); setProjectCheck(refreshed);
        setReviewSchedule(schedule); setReviewReports(reports); setResearchReports(research);
        setPeople(refreshedPeople); setPersonTasks(refreshedPersonTasks); setAliases(refreshedAliases);
        setPersonMerges(refreshedMerges);
        setMemoryLinks(refreshedMemories);
        if (schedule) setReviewForm({ interval_days: schedule.interval_days,
          local_time: schedule.local_time, timezone: schedule.timezone, paused: schedule.paused });
      }
    }
  };
  useEffect(() => {
    let current = true;
    if (visible) void refresh(Boolean(requestedSource))
      .then(() => {
        if (current && requestedSource) {
          setActive(null); setProjectCheck(null); setEditingId(null); setNotice('');
          select(requestedSource.id, requestedSource.serial);
        }
      })
      .catch((error: Error) => { if (current) setNotice(error.message); });
    return () => { current = false; ++request.current; };
  }, [visible, requestedSource?.serial]);
  useEffect(() => {
    if (!visible || !requestedSource || active?.id !== requestedSource.id || busy
        || loadedSourceSerial !== requestedSource.serial
        || lastSourceScroll.current === requestedSource.serial) return;
    const frame = window.requestAnimationFrame(() => {
      const match = requestedSource.match;
      const target = match ? window.document.getElementById(`project-${match.kind}-${match.id}`) : null;
      (target || window.document.getElementById(`search-source-project-${requestedSource.id}`))
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      lastSourceScroll.current = requestedSource.serial;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [visible, requestedSource?.serial, active?.id, busy, loadedSourceSerial]);

  const withBusy = async (action: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try { await action(); }
    catch (error) { setNotice((error as Error).message); }
    finally { busyRef.current = false; setBusy(false); }
  };
  const select = (id: string, sourceSerial?: number) => void withBusy(async () => {
    setConnections(null); setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    setGraphSource(''); setGraphTarget('');
    const check = await api<ProjectCheck>(`/work-states/${id}/check`);
    setActive(check.state); setProjectCheck(check); setEditingId(null); setDeleteConfirm(false); setNotice('');
    setPersonForm(emptyPersonForm()); setEditingPersonId(null); setPersonDeleteCandidate(null);
    setAliasForm(emptyAliasForm()); setEditingAliasId(null); setAliasDeleteCandidate(null);
    setPersonTaskForm(emptyPersonTaskForm()); setEditingPersonTaskId(null); setPersonTaskDeleteCandidate(null);
    setMergeSourceId(''); setMergeTargetId(''); setMergeConfirm(false); setSplitCandidate(null);
    setMemoryForm(emptyMemoryForm()); setEditingMemoryLinkId(null); setMemoryDeleteCandidate(null);
    setMemorySearch(''); setMemoryCandidates([]);
    await Promise.all([loadReviews(id), loadPeople(id), loadPersonTasks(id), loadAliases(id), loadPersonMerges(id), loadMemoryLinks(id)]);
    if (sourceSerial != null) setLoadedSourceSerial(sourceSerial);
  });
  const edit = () => {
    if (!active) return;
    setEditingId(active.id); setDeleteConfirm(false);
    setForm({ project: active.project, confirmed: active.confirmed,
      open_questions: active.open_questions, next_step: active.next_step,
      document_ids: active.documents.map((item) => item.id),
      task_ids: active.tasks.map((item) => item.id), links: active.links.join('\n') });
  };
  const newState = () => { setEditingId(null); setActive(null); setProjectCheck(null); setReviewSchedule(null);
    setConnections(null); setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    setReviewReports([]); setResearchReports([]); setPeople([]); setAliases([]); setPersonMerges([]); setPersonTasks([]); setMemoryLinks([]); setPersonForm(emptyPersonForm());
    setAliasForm(emptyAliasForm()); setEditingAliasId(null); setAliasDeleteCandidate(null);
    setPersonTaskForm(emptyPersonTaskForm()); setEditingPersonTaskId(null); setPersonTaskDeleteCandidate(null);
    setMemoryForm(emptyMemoryForm()); setEditingMemoryLinkId(null); setMemoryDeleteCandidate(null);
    setMemorySearch(''); setMemoryCandidates([]);
    setEditingPersonId(null); setPersonDeleteCandidate(null); setDeleteConfirm(false);
    setForm(emptyForm()); setNotice(''); };
  const toggle = (field: 'document_ids' | 'task_ids', id: string) => {
    setForm((previous) => ({ ...previous, [field]: previous[field].includes(id)
      ? previous[field].filter((item) => item !== id) : [...previous[field], id] }));
  };
  const save = () => void withBusy(async () => {
    const payload = { ...form, links: form.links.split(/\r?\n/).map((item) => item.trim()).filter(Boolean) };
    const state = editingId && active
      ? await api<WorkState>(`/work-states/${editingId}`, { method: 'PUT',
        body: JSON.stringify({ ...payload, expected_version: active.version }) })
      : await api<WorkState>('/work-states', { method: 'POST', body: JSON.stringify(payload) });
    setActive(state); setEditingId(null); setDeleteConfirm(false);
    setProjectCheck(await api<ProjectCheck>(`/work-states/${state.id}/check`));
    await Promise.all([loadReviews(state.id), loadPeople(state.id), loadPersonTasks(state.id), loadAliases(state.id), loadPersonMerges(state.id), loadMemoryLinks(state.id)]);
    setNotice('Arbeitsstand gespeichert. Die Referenzen wurden beim Speichern geprüft.');
    await refresh();
  });
  const remove = () => void withBusy(async () => {
    if (!active) return;
    await api(`/work-states/${active.id}?version=${active.version}`, { method: 'DELETE' });
    setActive(null); setProjectCheck(null); setReviewSchedule(null); setReviewReports([]); setResearchReports([]);
    setPeople([]); setAliases([]); setPersonMerges([]); setPersonTasks([]); setMemoryLinks([]); setPersonForm(emptyPersonForm()); setEditingPersonId(null);
    setMemoryForm(emptyMemoryForm()); setEditingMemoryLinkId(null); setMemoryDeleteCandidate(null);
    setMemorySearch(''); setMemoryCandidates([]);
    setGraph(null); setGraphNeighbors(null); setGraphPaths(null);
    setAliasForm(emptyAliasForm()); setEditingAliasId(null); setAliasDeleteCandidate(null);
    setPersonTaskForm(emptyPersonTaskForm()); setEditingPersonTaskId(null); setPersonTaskDeleteCandidate(null);
    setPersonDeleteCandidate(null); setEditingId(null); setDeleteConfirm(false); setForm(emptyForm());
    setNotice('Arbeitsstand gelöscht. Dateien und Aufgaben bleiben unverändert.');
    await refresh();
  });
  const editPerson = (person: ProjectPerson) => {
    setEditingPersonId(person.id); setPersonDeleteCandidate(null);
    setPersonForm({ name: person.name, relation_type: person.relation_type,
      source_note: person.source_note, source_document_id: person.source_document_id || '',
      confidence: person.confidence, valid_from: person.valid_from || '', valid_until: person.valid_until || '' });
  };
  const savePerson = () => void withBusy(async () => {
    if (!active) return;
    const payload = { ...personForm, expected_state_version: active.version,
      source_document_id: personForm.source_document_id || null,
      valid_from: personForm.valid_from || null, valid_until: personForm.valid_until || null };
    const existing = people.find((item) => item.id === editingPersonId);
    if (editingPersonId && !existing) throw new Error('Personenbeziehung wurde inzwischen entfernt; Projekt neu öffnen.');
    await api(`/work-states/${active.id}/people${existing ? `/${existing.id}` : ''}`, {
      method: existing ? 'PUT' : 'POST',
      body: JSON.stringify(existing ? { ...payload, expected_version: existing.version } : payload),
    });
    await Promise.all([loadPeople(active.id), loadPersonTasks(active.id), loadAliases(active.id)]);
    setEditingPersonId(null); setPersonForm(emptyPersonForm()); setPersonDeleteCandidate(null);
    setNotice('Personenbeziehung mit Herkunft gespeichert.');
  });
  const removePerson = (person: ProjectPerson) => void withBusy(async () => {
    if (!active) return;
    await api(`/work-states/${active.id}/people/${person.id}?expected_state_version=${active.version}&expected_version=${person.version}`,
      { method: 'DELETE' });
    await Promise.all([loadPeople(active.id), loadPersonTasks(active.id), loadAliases(active.id)]);
    if (editingPersonId === person.id) { setEditingPersonId(null); setPersonForm(emptyPersonForm()); }
    setPersonDeleteCandidate(null); setNotice('Personenbeziehung entfernt.');
  });
  const editAlias = (item: PersonAlias) => {
    setEditingAliasId(item.id); setAliasDeleteCandidate(null);
    setAliasForm({ person_id: item.person_id, alias: item.alias, source_note: item.source_note,
      source_document_id: item.source_document_id || '', confidence: item.confidence,
      valid_from: item.valid_from || '', valid_until: item.valid_until || '' });
  };
  const saveAlias = () => void withBusy(async () => {
    if (!active) return;
    const person = people.find((item) => item.id === aliasForm.person_id);
    if (!person) throw new Error('Projektperson wurde inzwischen entfernt; Projekt neu öffnen.');
    const existing = aliases.find((item) => item.id === editingAliasId);
    if (editingAliasId && !existing) throw new Error('Alias wurde inzwischen entfernt; Projekt neu öffnen.');
    const payload = { ...aliasForm, expected_state_version: active.version,
      expected_person_version: person.version,
      source_document_id: aliasForm.source_document_id || null,
      valid_from: aliasForm.valid_from || null, valid_until: aliasForm.valid_until || null };
    await api(`/work-states/${active.id}/aliases${existing ? `/${existing.id}` : ''}`, {
      method: existing ? 'PUT' : 'POST',
      body: JSON.stringify(existing ? { ...payload, expected_version: existing.version } : payload),
    });
    await loadAliases(active.id);
    setEditingAliasId(null); setAliasForm(emptyAliasForm()); setAliasDeleteCandidate(null);
    setNotice('Aliasname mit Herkunft gespeichert.');
  });
  const removeAlias = (item: PersonAlias) => void withBusy(async () => {
    if (!active) return;
    await api(`/work-states/${active.id}/aliases/${item.id}?expected_state_version=${active.version}&expected_version=${item.version}`,
      { method: 'DELETE' });
    await loadAliases(active.id);
    if (editingAliasId === item.id) { setEditingAliasId(null); setAliasForm(emptyAliasForm()); }
    setAliasDeleteCandidate(null); setNotice('Aliasname entfernt.');
  });
  const mergePeople = () => void withBusy(async () => {
    if (!active) return;
    const source = people.find((item) => item.id === mergeSourceId);
    const target = people.find((item) => item.id === mergeTargetId);
    if (!source || !target || source.id === target.id) throw new Error('Zwei verschiedene Projektpersonen wählen.');
    await api(`/work-states/${active.id}/person-merges`, { method: 'POST',
      body: JSON.stringify({ expected_state_version: active.version,
        source_person_id: source.id, expected_source_version: source.version,
        target_person_id: target.id, expected_target_version: target.version }) });
    await Promise.all([loadPeople(active.id), loadAliases(active.id), loadPersonTasks(active.id), loadPersonMerges(active.id)]);
    setMergeSourceId(''); setMergeTargetId(''); setMergeConfirm(false);
    setEditingPersonId(null); setPersonForm(emptyPersonForm()); setPersonDeleteCandidate(null);
    setEditingAliasId(null); setAliasForm(emptyAliasForm());
    setEditingPersonTaskId(null); setPersonTaskForm(emptyPersonTaskForm());
    setNotice('Personen zusammengeführt. Ursprung, Aliase und Aufgabenbeziehungen bleiben nachvollziehbar.');
  });
  const splitPeople = (merge: PersonMerge) => void withBusy(async () => {
    if (!active) return;
    const target = people.find((item) => item.id === merge.target_person_id);
    if (!target) throw new Error('Zielperson wurde entfernt; Trennen ist nicht mehr möglich.');
    await api(`/work-states/${active.id}/person-merges/${merge.id}/split`, { method: 'POST',
      body: JSON.stringify({ expected_state_version: active.version,
        expected_target_version: target.version }) });
    await Promise.all([loadPeople(active.id), loadAliases(active.id), loadPersonTasks(active.id), loadPersonMerges(active.id)]);
    setSplitCandidate(null); setPersonDeleteCandidate(null);
    setNotice('Zusammenführung getrennt. Ursprüngliche Personen und Beziehungen wiederhergestellt.');
  });
  const editPersonTask = (link: PersonTaskLink) => {
    setEditingPersonTaskId(link.id); setPersonTaskDeleteCandidate(null);
    setPersonTaskForm({ person_id: link.person_id, task_id: link.task_id,
      relation_type: link.relation_type, source_note: link.source_note,
      source_document_id: link.source_document_id || '', confidence: link.confidence,
      valid_from: link.valid_from || '', valid_until: link.valid_until || '' });
  };
  const savePersonTask = () => void withBusy(async () => {
    if (!active) return;
    const existing = personTasks.find((item) => item.id === editingPersonTaskId);
    if (editingPersonTaskId && !existing) throw new Error('Aufgabenbeziehung wurde inzwischen entfernt; Projekt neu öffnen.');
    const payload = { ...personTaskForm, expected_state_version: active.version,
      source_document_id: personTaskForm.source_document_id || null,
      valid_from: personTaskForm.valid_from || null, valid_until: personTaskForm.valid_until || null };
    await api(`/work-states/${active.id}/person-tasks${existing ? `/${existing.id}` : ''}`, {
      method: existing ? 'PUT' : 'POST',
      body: JSON.stringify(existing ? { ...payload, expected_version: existing.version } : payload),
    });
    await loadPersonTasks(active.id);
    setEditingPersonTaskId(null); setPersonTaskForm(emptyPersonTaskForm()); setPersonTaskDeleteCandidate(null);
    setNotice('Aufgabe und Person mit Herkunft verknüpft.');
  });
  const removePersonTask = (link: PersonTaskLink) => void withBusy(async () => {
    if (!active) return;
    await api(`/work-states/${active.id}/person-tasks/${link.id}?expected_state_version=${active.version}&expected_version=${link.version}`,
      { method: 'DELETE' });
    await loadPersonTasks(active.id);
    if (editingPersonTaskId === link.id) { setEditingPersonTaskId(null); setPersonTaskForm(emptyPersonTaskForm()); }
    setPersonTaskDeleteCandidate(null); setNotice('Aufgabenbeziehung entfernt.');
  });
  const searchProjectMemories = () => void withBusy(async () => {
    if (memorySearch.trim().length < 2) throw new Error('Für die Erinnerungssuche mindestens zwei Zeichen eingeben.');
    const found = await api<MemorySearchResult[]>(`/memories?q=${encodeURIComponent(memorySearch.trim())}`);
    setMemoryCandidates(found);
    setNotice(`${found.length} passende lokale Erinnerungen gefunden.`);
  });
  const editMemoryLink = (link: ProjectMemoryLink) => {
    setEditingMemoryLinkId(link.id); setMemoryDeleteCandidate(null);
    setMemoryForm({ memory_id: link.memory_id, source_note: link.source_note,
      confidence: link.confidence, valid_from: link.valid_from || '',
      valid_until: link.valid_until || '' });
  };
  const saveMemoryLink = () => void withBusy(async () => {
    if (!active) return;
    const existing = memoryLinks.find((item) => item.id === editingMemoryLinkId);
    if (editingMemoryLinkId && !existing) throw new Error('Projekt-Erinnerung wurde inzwischen entfernt; Projekt neu öffnen.');
    const payload = { expected_state_version: active.version, source_note: memoryForm.source_note,
      confidence: memoryForm.confidence, valid_from: memoryForm.valid_from || null,
      valid_until: memoryForm.valid_until || null };
    await api(`/work-states/${active.id}/memory-links${existing ? `/${existing.id}` : ''}`, {
      method: existing ? 'PUT' : 'POST', body: JSON.stringify(existing
        ? { ...payload, expected_version: existing.version }
        : { ...payload, memory_id: memoryForm.memory_id }),
    });
    await loadMemoryLinks(active.id);
    setMemoryForm(emptyMemoryForm()); setEditingMemoryLinkId(null); setMemoryDeleteCandidate(null);
    setNotice('Erinnerung mit ursprünglichem Text, Quelle und Policy im Projekt belegt.');
  });
  const confirmMemoryLink = (link: ProjectMemoryLink) => void withBusy(async () => {
    if (!active) return;
    await api(`/work-states/${active.id}/memory-links/${link.id}/confirm`, { method: 'POST',
      body: JSON.stringify({ expected_state_version: active.version, expected_version: link.version }) });
    await loadMemoryLinks(active.id);
    setNotice('Aktuellen Erinnerungstext, Quelle und Policy ausdrücklich bestätigt.');
  });
  const removeMemoryLink = (link: ProjectMemoryLink) => void withBusy(async () => {
    if (!active) return;
    await api(`/work-states/${active.id}/memory-links/${link.id}?expected_state_version=${active.version}&expected_version=${link.version}`,
      { method: 'DELETE' });
    await loadMemoryLinks(active.id);
    setMemoryDeleteCandidate(null); setEditingMemoryLinkId(null); setMemoryForm(emptyMemoryForm());
    setNotice('Projektverknüpfung entfernt. Die Erinnerung selbst bleibt erhalten.');
  });
  const queryConnections = () => void withBusy(async () => {
    if (!active) return;
    setConnections(null);
    const result = await api<ProjectConnections>(`/work-states/${active.id}/connections`);
    setConnections(result);
    setNotice('Projektbeziehungen lokal abgefragt. Quellenstatus zum Abfragezeitpunkt geprüft.');
  });
  const loadGraph = () => void withBusy(async () => {
    if (!active) return;
    const next = await api<GraphSnapshot>(`/work-states/${active.id}/graph`);
    setGraph(next); setGraphNeighbors(null); setGraphPaths(null);
    setGraphSource(`project:${active.id}`); setGraphTarget('');
    setNotice('Belegte Projektknoten lokal geladen. Quellenstatus zum Ladezeitpunkt geprüft.');
  });
  const queryGraphNeighbors = () => void withBusy(async () => {
    if (!active || !graphSource) return;
    const params = new URLSearchParams({ node: graphSource, direction: graphDirection });
    if (graphFilters.length) params.set('relations', graphFilters.join(','));
    const result = await api<GraphNeighbors>(`/work-states/${active.id}/graph/neighbors?${params}`);
    setGraphNeighbors(result); setGraphPaths(null);
    setNotice('Direkte Nachbarn lokal abgefragt.');
  });
  const queryGraphPaths = () => void withBusy(async () => {
    if (!active || !graphSource || !graphTarget) return;
    const params = new URLSearchParams({ source: graphSource, target: graphTarget,
      max_depth: String(graphDepth) });
    if (graphFilters.length) params.set('relations', graphFilters.join(','));
    const result = await api<GraphPaths>(`/work-states/${active.id}/graph/paths?${params}`);
    setGraphPaths(result); setGraphNeighbors(null);
    setNotice('Gerichtete Projektpfade lokal abgefragt.');
  });
  const toggleGraphFilter = (relation: string) => {
    setGraphFilters((previous) => previous.includes(relation)
      ? previous.filter((item) => item !== relation) : [...previous, relation]);
    setGraphNeighbors(null); setGraphPaths(null);
  };
  const jumpTo = (kind: 'person' | 'document' | 'task' | 'person-task' | 'alias' | 'memory' | 'deadline', id: string) => {
    document.getElementById(`project-${kind}-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };
  const jumpToGraphNode = (node: GraphNode) => {
    if (node.kind === 'project') return;
    jumpTo(node.kind, node.id.slice(node.kind.length + 1));
  };
  const runTool = (tool_name: string, args: Record<string, string>, label: string) => void withBusy(async () => {
    const result = await api<{ status: string; error: string | null }>('/runs', { method: 'POST',
      body: JSON.stringify({ tool_name, args, client_request_id: crypto.randomUUID(), background: false }) });
    setNotice(result.status === 'succeeded' ? `${label}: erfolgreich.` : `${label}: ${result.error || result.status}.`);
    if (active) { const check = await api<ProjectCheck>(`/work-states/${active.id}/check`); setActive(check.state); setProjectCheck(check); }
  });
  const confirmDocument = (documentId: string) => void withBusy(async () => {
    if (!active) return;
    const state = await api<WorkState>(`/work-states/${active.id}/documents/${documentId}/confirm`, {
      method: 'POST', body: JSON.stringify({ expected_version: active.version }),
    });
    const check = await api<ProjectCheck>(`/work-states/${state.id}/check`);
    setActive(check.state); setProjectCheck(check);
    await Promise.all([loadPeople(state.id), loadPersonTasks(state.id), loadAliases(state.id), loadMemoryLinks(state.id)]);
    setNotice('Neuen Datei-Hash nach erneuter Indexierung für dieses Projekt bestätigt.');
  });
  const prepareNextTask = () => void withBusy(async () => {
    if (!active) return;
    const result = await api<PrepareResult>(`/work-states/${active.id}/next-task`, {
      method: 'POST', body: JSON.stringify({ expected_version: active.version }),
    });
    setActive(result.check.state); setProjectCheck(result.check);
    setNotice(result.reused ? 'Dieser nächste Schritt war bereits als Aufgabe erfasst.' : 'Nächsten Schritt einmalig als Aufgabe angelegt.');
    await refresh();
  });
  const saveReviewSchedule = () => void withBusy(async () => {
    if (!active) return;
    await api<ReviewSchedule>(`/work-states/${active.id}/reviews/schedule`, {
      method: 'PUT', body: JSON.stringify(reviewForm),
    });
    await loadReviews(active.id);
    setNotice(reviewForm.paused ? 'Regelmäßige Projektprüfung pausiert.' : 'Regelmäßige Projektprüfung gespeichert.');
  });
  const runReview = () => void withBusy(async () => {
    if (!active) return;
    const report = await api<ReviewReport>(`/work-states/${active.id}/reviews/run`, { method: 'POST' });
    await loadReviews(active.id);
    setNotice(report.status === 'succeeded' ? 'Projektprüfung als Bericht gespeichert.'
      : `Projektprüfung fehlgeschlagen: ${report.error || 'Unbekannter Fehler'}`);
  });
  const availableDocuments = documents.filter((item) => !item.excluded && item.status === 'indexed');
  const hasActiveMerge = (personId: string) => personMerges.some((merge) =>
    !merge.split_at && merge.target_person_id === personId);
  const availableTasks = tasks.filter((item) => item.status === 'open');
  const missingDocuments = form.document_ids.filter((id) => !availableDocuments.some((item) => item.id === id));
  const missingTasks = form.task_ids.filter((id) => !availableTasks.some((item) => item.id === id));

  return <div className="section-stack"><h2>Arbeitsstände</h2>
    <p className="intro">Halte Ergebnisse, offene Fragen und den nächsten Schritt bewusst fest. Jarvis prüft ausgewählte Quellen beim Speichern und zeigt Änderungen beim Wiedereinstieg.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><div className="card-heading"><h3>Gespeicherte Arbeitsstände</h3><button onClick={newState} disabled={busy}>Neuen Arbeitsstand anlegen</button></div>
      {items.length === 0 ? <p>Noch kein Arbeitsstand gespeichert.</p> : <div className="item-list">{items.map((item) =>
        <div className="item-row" key={item.id}><div><strong>{item.project}</strong><small>Weiter: {item.next_step} · {new Date(item.updated_at).toLocaleString('de-DE')}</small></div>
          <button disabled={busy} onClick={() => select(item.id)}>Öffnen</button></div>)}</div>}
    </section>
    {active && !editingId && <section id={`search-source-project-${active.id}`}
      className={`card${requestedSource?.id === active.id ? ' source-opened' : ''}`}>
      <div className="card-heading"><h3>{active.project}</h3><small>Stand {active.version}</small></div>
      <div className="work-sections"><div><strong>Bestätigte Ergebnisse</strong><p>{active.confirmed || 'Keine eingetragen'}</p></div>
        <div><strong>Offene Fragen</strong><p>{active.open_questions || 'Keine eingetragen'}</p></div>
        <div><strong>Nächster Schritt</strong><p>{active.next_step}</p></div></div>
      <h4>Projektprüfung</h4>
      {projectCheck && <div className="work-sections"><p>{projectCheck.open_tasks} offene · {projectCheck.done_tasks} erledigte · {projectCheck.missing_tasks} gelöschte verknüpfte Aufgaben.</p>
        {projectCheck.blockers.length > 0 && <><p>Vorbereitung blockiert, bis diese Quellen erneut geprüft sind:</p><ul>{projectCheck.blockers.map((item) => <li key={item}>{item}</li>)}</ul></>}
        {projectCheck.already_prepared ? <p>Dieser nächste Schritt ist bereits als Aufgabe erfasst{projectCheck.prepared_task ? ` (${projectCheck.prepared_task.status === 'done' ? 'erledigt' : 'offen'})` : ' (Aufgabe inzwischen gelöscht)'}. Für eine neue Aufgabe den nächsten Schritt bewusst ändern.</p>
          : <button disabled={busy || !projectCheck.ready} onClick={prepareNextTask}>Nächsten Schritt als Aufgabe anlegen</button>}
      </div>}
      <h4>Regelmäßige Projektprüfung</h4>
      <p>Jarvis prüft nur bei laufender App. Ein verpasster Termin wird beim nächsten Start nachgeholt; während einer Pause entstehen keine Berichte.</p>
      <div className="work-form"><label>Alle wie viele Tage?<input aria-label="Prüfintervall in Tagen" type="number" min="1" max="30" disabled={busy}
        value={reviewForm.interval_days} onChange={(event) => setReviewForm({ ...reviewForm, interval_days: Number(event.target.value) })} /></label>
        <label>Lokale Uhrzeit<input aria-label="Projektprüfzeit" type="time" value={reviewForm.local_time} disabled={busy}
          onChange={(event) => setReviewForm({ ...reviewForm, local_time: event.target.value })} /></label>
        <label>IANA-Zeitzone<input aria-label="Projektprüf-Zeitzone" value={reviewForm.timezone} disabled={busy}
          onChange={(event) => setReviewForm({ ...reviewForm, timezone: event.target.value })} /></label>
        <label><input aria-label="Projektprüfung pausieren" type="checkbox" checked={reviewForm.paused} disabled={busy}
          onChange={(event) => setReviewForm({ ...reviewForm, paused: event.target.checked })} /> Projektprüfung pausieren</label>
        <div className="item-actions"><button disabled={busy || reviewForm.interval_days < 1 || reviewForm.interval_days > 30}
          onClick={saveReviewSchedule}>Prüfplan speichern</button><button disabled={busy} onClick={runReview}>Jetzt als Bericht prüfen</button></div></div>
      {reviewSchedule && <p role="status">{reviewSchedule.paused ? 'Pausiert' : `Nächste Prüfung: ${reviewSchedule.next_local_date} um ${reviewSchedule.local_time} (${reviewSchedule.timezone})`}
        {reviewSchedule.last_status ? ` · letzter Lauf: ${reviewSchedule.last_status}` : ''}
        {reviewSchedule.last_error ? ` · Fehler: ${reviewSchedule.last_error}` : ''}</p>}
      <h4>Prüfberichte</h4>{reviewReports.length === 0 ? <p>Noch kein Bericht gespeichert.</p> : <div className="item-list project-review-list">{reviewReports.map((report) =>
        <div className="item-row" key={report.id}><div><strong>{report.local_date} · {report.kind === 'scheduled' ? 'Geplant' : 'Manuell'} · {report.status === 'succeeded' ? 'Erfolgreich' : 'Fehler'}</strong>
          <small>{report.changed === 1 ? 'Stand geändert' : report.changed === 0 ? 'Stand unverändert' : ''} · {new Date(report.created_at).toLocaleString('de-DE')}</small>
          {report.error && <p>{report.error}</p>}
          {report.summary && <><p>Stand {report.summary.state_version}: {report.summary.confirmed || 'Keine bestätigten Ergebnisse'} · Weiter: {report.summary.next_step}</p>
            <p>{report.summary.open_tasks} offen · {report.summary.done_tasks} erledigt · {report.summary.missing_tasks} gelöscht. Offene Fragen: {report.summary.open_questions || 'Keine'}</p>
            {report.summary.blockers.length > 0 && <ul>{report.summary.blockers.map((blocker) => <li key={blocker}>{blocker}</li>)}</ul>}
            {report.summary.documents.map((document) => <small key={document.id}>{document.path}: {fileStatus[document.status] || document.status}</small>)}</>}
        </div></div>)}</div>}
      <h4>Recherchen mit diesem Arbeitsstand</h4>
      <p>Hier erscheinen nur Berichte, bei denen dieser Arbeitsstand ausdrücklich für den Rechercheauftrag gewählt wurde. Der gespeicherte Snapshot bleibt auch nach späteren Änderungen nachvollziehbar.</p>
      {researchReports.length === 0 ? <p>Noch keine zugeordnete Recherche.</p> : <div className="item-list">{researchReports.map((item) =>
        <div className="item-row" key={item.report_id}><div><strong>{item.question}</strong>
          <small>{new Date(item.created_at).toLocaleString('de-DE')} · Arbeitsstand-Version {item.project_version} · {item.source_count} gelesene Quellen · {item.provider}/{item.model}</small>
          <p>{item.interpretation || 'Keine Modell-Einordnung gespeichert; Fundstellen im Bericht prüfen.'}</p></div>
          <button type="button" onClick={() => onResearch(item.report_id)}>Bericht öffnen</button></div>)}</div>}
      <h4>Projektbeziehungen abfragen</h4>
      <p>Zeigt die ausdrücklich zugeordneten Personen, Dateien und Aufgaben dieses Projekts mit ihrem aktuellen Quellenstatus. Die Abfrage läuft nur lokal und verwendet kein Modell.</p>
      <button disabled={busy} onClick={queryConnections}>Projektbeziehungen lokal abfragen</button>
      {connections && connections.project_id === active.id && <div className="work-sections" aria-label="Ergebnis der lokalen Projektabfrage">
        <p><strong>{connections.project}</strong> · Arbeitsstand-Fassung {connections.state_version} · geprüft {new Date(connections.checked_at).toLocaleString('de-DE')} · lokaler Stichtag {connections.as_of_local_date} ({connections.timezone})</p>
        <div><strong>Personen ({connections.people.length})</strong>{connections.people.length === 0 ? <p>Keine Personen zugeordnet.</p> :
          <ul>{connections.people.map((person) => <li key={person.id}>{person.name} · {relationLabels[person.relation_type]} ·
            {' '}{person.source_kind === 'own_statement' ? 'Eigene Angabe' : person.source_kind === 'current_document' ? 'Dateibeleg unverändert' : 'Dateibeleg prüfen'}
            {' '}({personSourceLabels[person.source_status] || fileStatus[person.source_status] || person.source_status}) · {person.source_note}
            {' '}· {confidenceLabels[person.confidence]} · {validityLabels[person.validity_status] || person.validity_status}
            <button type="button" onClick={() => jumpTo('person', person.id)}>Personenbeleg ansehen</button>
            {person.source_document_id && connections.documents.some((item) => item.id === person.source_document_id) &&
              <button type="button" onClick={() => jumpTo('document', person.source_document_id!)}>Quelldatei ansehen</button>}</li>)}</ul>}</div>
        <div><strong>Aliase ({connections.aliases.length})</strong>{connections.aliases.length === 0 ? <p>Keine Aliasnamen eingetragen.</p> :
          <ul>{connections.aliases.map((item) => <li key={item.id}>{item.alias} → {item.person_name} ·
            {' '}{personSourceLabels[item.source_status] || fileStatus[item.source_status] || item.source_status}
            {' '}· {confidenceLabels[item.confidence]} · {validityLabels[item.validity_status] || item.validity_status}
            <button type="button" onClick={() => jumpTo('alias', item.id)}>Aliasbeleg ansehen</button>
            <button type="button" onClick={() => jumpTo('person', item.person_id)}>Person ansehen</button></li>)}</ul>}</div>
        <div><strong>Erinnerungen ({connections.memories.length})</strong>{connections.memories.length === 0 ? <p>Keine Erinnerungen zugeordnet.</p> :
          <ul>{connections.memories.map((item) => <li key={item.id}>{item.memory_text.slice(0, 200)} ·
            {' '}{memoryStatusLabels[item.memory_status]} · {memoryPolicyLabels[item.memory_policy]}
            {' '}· Quelle: {item.memory_source_ref} · {item.source_note} · {confidenceLabels[item.confidence]}
            {' '}· {validityLabels[item.validity_status] || item.validity_status}
            <button type="button" onClick={() => jumpTo('memory', item.id)}>Erinnerungsbeleg ansehen</button></li>)}</ul>}</div>
        <div><strong>Dateien ({connections.documents.length})</strong>{connections.documents.length === 0 ? <p>Keine Dateien zugeordnet.</p> :
          <ul>{connections.documents.map((item) => <li key={item.id}>{item.path} · {fileStatus[item.status] || item.status}
            <button type="button" onClick={() => jumpTo('document', item.id)}>Dateibeleg ansehen</button></li>)}</ul>}</div>
        <div><strong>Aufgaben ({connections.tasks.length})</strong>{connections.tasks.length === 0 ? <p>Keine Aufgaben zugeordnet.</p> :
          <ul>{connections.tasks.map((item) => <li key={item.id}>{item.title} · {item.status}
            {item.current_title && item.current_title !== item.title ? ` · Jetzt: ${item.current_title}` : ''}
            {item.current_due_local || item.due_local ? ` · ${dueStatusLabels[item.due_status] || item.due_status} · Beim Zuordnen: ${item.due_status === 'altbestand_ohne_snapshot' ? 'Nicht belegt' : dueText(item.due_local, item.timezone, item.due_offset, item.due_fold)} · Jetzt: ${dueText(item.current_due_local, item.current_timezone, item.current_due_offset, item.current_due_fold)}` : ''}
            <button type="button" onClick={() => jumpTo('task', item.id)}>Aufgabe ansehen</button></li>)}</ul>}</div>
        <div><strong>Aufgabe → Person ({connections.person_tasks.length})</strong>{connections.person_tasks.length === 0 ? <p>Keine direkten Aufgabenbeziehungen eingetragen.</p> :
          <ul>{connections.person_tasks.map((link) => <li key={link.id}>{link.task_title} → {link.person_name} · {personTaskRelationLabels[link.relation_type]}
            {' '}· {personTaskStatusLabels[link.status] || link.status} · {link.source_note}
            {' '}· {confidenceLabels[link.confidence]} · {validityLabels[link.validity_status] || link.validity_status}
            {' '}· {personSourceLabels[link.source_status] || fileStatus[link.source_status] || link.source_status}
            {link.merged_from_person_name && <> · ursprünglich {link.merged_from_person_name} ({personSourceLabels[link.merged_from_source_status || ''] || fileStatus[link.merged_from_source_status || ''] || link.merged_from_source_status}; {validityLabels[link.merged_from_validity_status || ''] || link.merged_from_validity_status}; {link.merged_from_confidence && confidenceLabels[link.merged_from_confidence]})</>}
            <button type="button" onClick={() => jumpTo('person-task', link.id)}>Verbindung ansehen</button>
            <button type="button" onClick={() => jumpTo('person', link.person_id)}>Personenbeleg ansehen</button>
            {connections.documents.some((item) => item.id === link.source_document_id) &&
              <button type="button" onClick={() => jumpTo('document', link.source_document_id!)}>Quelldatei ansehen</button>}</li>)}</ul>}</div>
      </div>}
      <h4>Belegte Projektpfade</h4>
      <p>Nachbarn und gerichtete Pfade werden aus den bewusst gespeicherten Beziehungen dieses Projekts berechnet. Höchstens drei Kanten je Pfad und 20 Ergebnisse; Quellenstatus und Gültigkeit stehen an den Kanten. Kein Modellaufruf.</p>
      <button disabled={busy} onClick={loadGraph}>Projektgraph lokal laden</button>
      {graph && graph.project_id === active.id && <div className="work-form" aria-label="Projektgraph-Abfrage">
        <p>{graph.nodes.length} Knoten · {graph.edges.length} Beziehungen · Stand {graph.state_version} · geprüft {new Date(graph.checked_at).toLocaleString('de-DE')}</p>
        <label>Startknoten<select aria-label="Graph-Startknoten" disabled={busy} value={graphSource}
          onChange={(event) => { setGraphSource(event.target.value); setGraphNeighbors(null); setGraphPaths(null); }}>
          <option value="">Knoten wählen</option>{graph.nodes.map((node) => <option key={node.id} value={node.id}>{node.kind}: {node.label}</option>)}</select></label>
        <label>Zielknoten für Pfad<select aria-label="Graph-Zielknoten" disabled={busy} value={graphTarget}
          onChange={(event) => { setGraphTarget(event.target.value); setGraphPaths(null); }}>
          <option value="">Knoten wählen</option>{graph.nodes.map((node) => <option key={node.id} value={node.id}>{node.kind}: {node.label}</option>)}</select></label>
        <label>Nachbarrichtung<select aria-label="Graph-Richtung" disabled={busy} value={graphDirection}
          onChange={(event) => { setGraphDirection(event.target.value as 'in' | 'out' | 'both'); setGraphNeighbors(null); }}>
          <option value="both">Beide Richtungen</option><option value="out">Ausgehend</option><option value="in">Eingehend</option></select></label>
        <label>Maximale Pfadtiefe<select aria-label="Graph-Pfadtiefe" disabled={busy} value={graphDepth}
          onChange={(event) => { setGraphDepth(Number(event.target.value)); setGraphPaths(null); }}>
          <option value={1}>1 Kante</option><option value={2}>2 Kanten</option><option value={3}>3 Kanten</option></select></label>
        <fieldset><legend>Beziehungstypen (keine Auswahl = alle)</legend>
          {Object.entries(graphRelations).map(([relation, label]) => <label key={relation}>
            <input type="checkbox" disabled={busy} checked={graphFilters.includes(relation)}
              onChange={() => toggleGraphFilter(relation)} />{label}</label>)}</fieldset>
        <div className="item-actions"><button disabled={busy || !graphSource} onClick={queryGraphNeighbors}>Nachbarn abfragen</button>
          <button disabled={busy || !graphSource || !graphTarget || graphSource === graphTarget}
            onClick={queryGraphPaths}>Pfade abfragen</button></div>
        {graphNeighbors && <div aria-label="Graph-Nachbarn"><strong>Nachbarn von {graphNeighbors.node.label} ({graphNeighbors.neighbors.length})</strong>
          {graphNeighbors.neighbors.length === 0 ? <p>Keine passenden direkten Nachbarn.</p> : <ul>
            {graphNeighbors.edges.map((edge) => { const neighbor = graph.nodes.find((node) =>
              node.id === (edge.from === graphNeighbors.node.id ? edge.to : edge.from));
              return <li key={edge.id}>{graphRelations[edge.relation]}{edge.role ? ` · ${graphRoleLabels[edge.relation]?.[edge.role] || edge.role}` : ''} → {neighbor?.label || 'Unbekannt'}
                {edge.source_status ? ` · ${personSourceLabels[edge.source_status] || fileStatus[edge.source_status] || edge.source_status}` : ''}
                {edge.confidence ? ` · ${confidenceLabels[edge.confidence]}` : ''}
                {edge.validity_status ? ` · ${validityLabels[edge.validity_status] || edge.validity_status}` : ''}
                {edge.status ? ` · ${dueStatusLabels[edge.status] || personTaskStatusLabels[edge.status] || edge.status}` : ''}
                {edge.merged_from_person_name ? ` · ursprünglich ${edge.merged_from_person_name}: ${personSourceLabels[edge.merged_from_source_status || ''] || fileStatus[edge.merged_from_source_status || ''] || edge.merged_from_source_status}` : ''}
                {edge.merged_from_validity_status ? ` · damals ${validityLabels[edge.merged_from_validity_status] || edge.merged_from_validity_status}` : ''}
                {edge.source_note ? ` · ${edge.source_note}` : ''}
                {edge.memory_source_ref && <small>Erinnerungsquelle: {edge.memory_source_ref} · {edge.memory_policy ? memoryPolicyLabels[edge.memory_policy] : ''}</small>}
                {edge.memory_sha256 && <small>Gespeicherter Erinnerungs-Hash: {edge.memory_sha256}</small>}
                {edge.source_path && <small>Belegpfad: {edge.source_path}</small>}
                {edge.source_sha256 && <small>Gespeicherter SHA-256: {edge.source_sha256}</small>}
                {neighbor && neighbor.kind !== 'project' && <button type="button" onClick={() => jumpToGraphNode(neighbor)}>Eintrag ansehen</button>}</li>; })}</ul>}</div>}
        {graphPaths && <div aria-label="Graph-Pfade"><strong>{graphPaths.paths.length} gerichtete Pfade von {graphPaths.source.label} zu {graphPaths.target.label}</strong>
          {graphPaths.truncated && <p>Weitere Pfade möglich; Ausgabe auf 20 Ergebnisse oder 1000 Schritte begrenzt.</p>}
          {graphPaths.paths.length === 0 ? <p>Kein passender Pfad innerhalb der gewählten Tiefe.</p> : <ol>
            {graphPaths.paths.map((path, index) => <li key={`${path.edges.map((edge) => edge.id).join('|')}-${index}`}>
              <strong>{path.nodes.map((node) => node.label).join(' → ')}</strong>
              <div className="item-actions">{path.nodes.filter((node) => node.kind !== 'project').map((node) =>
                <button type="button" key={node.id} onClick={() => jumpToGraphNode(node)}>{node.kind}: {node.label} ansehen</button>)}</div>
              <ul>{path.edges.map((edge) => <li key={edge.id}>{graphRelations[edge.relation]}{edge.role ? ` · ${graphRoleLabels[edge.relation]?.[edge.role] || edge.role}` : ''}
                {edge.source_status ? ` · ${personSourceLabels[edge.source_status] || fileStatus[edge.source_status] || edge.source_status}` : ''}
                {edge.confidence ? ` · ${confidenceLabels[edge.confidence]}` : ''}
                {edge.validity_status ? ` · ${validityLabels[edge.validity_status] || edge.validity_status}` : ''}
                {edge.status ? ` · ${dueStatusLabels[edge.status] || personTaskStatusLabels[edge.status] || edge.status}` : ''}
                {edge.merged_from_person_name ? ` · ursprünglich ${edge.merged_from_person_name}: ${personSourceLabels[edge.merged_from_source_status || ''] || fileStatus[edge.merged_from_source_status || ''] || edge.merged_from_source_status}` : ''}
                {edge.merged_from_validity_status ? ` · damals ${validityLabels[edge.merged_from_validity_status] || edge.merged_from_validity_status}` : ''}
                {edge.source_note ? ` · ${edge.source_note}` : ''}
                {edge.memory_source_ref && <small>Erinnerungsquelle: {edge.memory_source_ref} · {edge.memory_policy ? memoryPolicyLabels[edge.memory_policy] : ''}</small>}
                {edge.memory_sha256 && <small>Gespeicherter Erinnerungs-Hash: {edge.memory_sha256}</small>}
                {edge.source_path && <small>Belegpfad: {edge.source_path}</small>}
                {edge.source_sha256 && <small>Gespeicherter SHA-256: {edge.source_sha256}</small>}</li>)}</ul></li>)}</ol>}</div>}
      </div>}
      <h4>Ausgewählte Dateien</h4>{active.documents.length === 0 ? <p>Keine Dateien ausgewählt.</p> : <div className="item-list">{active.documents.map((item) =>
        <div className="item-row" id={`project-document-${item.id}`} key={item.id}><div><strong>{item.path}</strong><small>Gespeicherter SHA-256: {item.sha256}</small><small>{fileStatus[item.status] || item.status}</small></div>
          <div className="item-actions"><button disabled={busy || item.status !== 'unveraendert'} onClick={() => runTool('open_file', { file_id: item.id, expected_sha256: item.sha256 }, 'Datei öffnen')}>Datei öffnen</button>
            {item.status !== 'unveraendert' && <button disabled={busy} onClick={() => confirmDocument(item.id)}>Neu indexierte Datei bestätigen</button>}</div></div>)}</div>}
      <h4>Ausgewählte Aufgaben</h4>{active.tasks.length === 0 ? <p>Keine Aufgaben ausgewählt.</p> : <div className="item-list">{active.tasks.map((item) =>
        <div className="item-row" id={`project-task-${item.id}`} key={item.id}><div><strong>{item.title}</strong><small>Status: {item.status}{item.current_title && item.current_title !== item.title ? ` · Jetzt: ${item.current_title}` : ''}</small></div></div>)}</div>}
      {active.tasks.length > 0 && <button onClick={onTasks}>Aufgaben ansehen</button>}
      <h4>Projektfristen</h4>
      <p>Nur Fälligkeiten der ausdrücklich ausgewählten Projektaufgaben. Die damalige Frist bleibt als Beleg erhalten; geänderte oder entfernte Fälligkeiten werden getrennt angezeigt. Ältere Arbeitsstände ohne Frist-Snapshot sind gekennzeichnet.</p>
      {active.tasks.filter((item) => item.due_local || item.current_due_local).length === 0 ? <p>Keine Frist bei ausgewählten Aufgaben.</p> :
        <div className="item-list">{[...active.tasks].filter((item) => item.due_local || item.current_due_local)
          .sort((left, right) => (left.current_due_utc || left.due_utc || '').localeCompare(right.current_due_utc || right.due_utc || '') || left.id.localeCompare(right.id))
          .map((item) => <div className="item-row" id={`project-deadline-${item.id}`} key={item.id}><div>
            <strong>{item.title}</strong><small>{dueStatusLabels[item.due_status] || item.due_status} · Aufgabenstatus: {item.status}</small>
            <small>Beim Zuordnen: {item.due_utc === undefined ? 'Nicht belegt (älterer Arbeitsstand)' : dueText(item.due_local, item.timezone, item.due_offset, item.due_fold)}</small>
            <small>Jetzt: {dueText(item.current_due_local, item.current_timezone, item.current_due_offset, item.current_due_fold)}</small>
          </div><button type="button" onClick={() => jumpTo('task', item.id)}>Aufgabe im Projekt ansehen</button></div>)}</div>}
      <h4>Personen im Projekt</h4>
      <p>Diese Beziehungen trägst du ausdrücklich ein. Eine Herkunftsnotiz ist eine eigene Angabe; eine gewählte Projektdatei wird zusätzlich mit ihrem gespeicherten Hash belegt. Jarvis erkennt oder teilt Personen nicht automatisch.</p>
      {people.length === 0 ? <p>Noch keine Person zugeordnet.</p> : <div className="item-list">{people.map((person) =>
        <div className="item-row" id={`project-person-${person.id}`} key={person.id}><div><strong>{person.name} · {relationLabels[person.relation_type]}</strong>
          <p>{person.source_note}</p>
          <small>Herkunft: {person.source_document_id ? person.source_path : 'selbst eingetragen'} · {personSourceLabels[person.source_status] || fileStatus[person.source_status] || person.source_status}</small>
          {person.source_sha256 && <small>Gespeicherter SHA-256: {person.source_sha256}</small>}
          <small>Vertrauen: {confidenceLabels[person.confidence]} · {validityLabels[person.validity_status] || person.validity_status}
            {person.valid_from ? ` · ab ${person.valid_from}` : ''}{person.valid_until ? ` · bis ${person.valid_until}` : ''} · Fassung {person.version}</small></div>
          <div className="item-actions"><button disabled={busy} onClick={() => editPerson(person)}>Bearbeiten</button>
            {hasActiveMerge(person.id) && <small>Aktive Zusammenführung zuerst trennen.</small>}
            {personDeleteCandidate === person.id ? <><button disabled={busy} onClick={() => removePerson(person)}>Verbindung entfernen</button><button disabled={busy} onClick={() => setPersonDeleteCandidate(null)}>Abbrechen</button></>
              : <button disabled={busy || hasActiveMerge(person.id)} onClick={() => setPersonDeleteCandidate(person.id)}>Entfernen…</button>}</div></div>)}</div>}
      <div className="work-form"><h5>{editingPersonId ? 'Personenbeziehung korrigieren' : 'Person zuordnen'}</h5>
        <label>Name<input aria-label="Personenname" maxLength={160} disabled={busy} value={personForm.name}
          onChange={(event) => setPersonForm({ ...personForm, name: event.target.value })} /></label>
        <label>Beziehung<select aria-label="Projektbeziehung" disabled={busy} value={personForm.relation_type}
          onChange={(event) => setPersonForm({ ...personForm, relation_type: event.target.value as ProjectPerson['relation_type'] })}>
          {Object.entries(relationLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Herkunftsnotiz<textarea aria-label="Herkunftsnotiz" maxLength={500} disabled={busy} value={personForm.source_note}
          onChange={(event) => setPersonForm({ ...personForm, source_note: event.target.value })} /></label>
        <label>Quelldatei aus diesem Projekt (optional)<select aria-label="Personen-Quelldatei" disabled={busy}
          value={personForm.source_document_id} onChange={(event) => setPersonForm({ ...personForm, source_document_id: event.target.value })}>
          <option value="">Nur eigene Angabe</option>{active.documents.map((item) => <option key={item.id} value={item.id}
            disabled={item.status !== 'unveraendert'}>{item.path} · {fileStatus[item.status] || item.status}</option>)}</select></label>
        <label>Vertrauensgrad (eigene Einschätzung)<select aria-label="Personen-Vertrauen" disabled={busy}
          value={personForm.confidence} onChange={(event) => setPersonForm({ ...personForm, confidence: event.target.value as ProjectPerson['confidence'] })}>
          {Object.entries(confidenceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Gültig ab (optional)<input aria-label="Personen gültig ab" type="date" disabled={busy} value={personForm.valid_from}
          onChange={(event) => setPersonForm({ ...personForm, valid_from: event.target.value })} /></label>
        <label>Gültig bis (optional)<input aria-label="Personen gültig bis" type="date" disabled={busy} value={personForm.valid_until}
          onChange={(event) => setPersonForm({ ...personForm, valid_until: event.target.value })} /></label>
        <div className="item-actions"><button disabled={busy || !personForm.name.trim() || personForm.source_note.trim().length < 5 ||
          Boolean(personForm.valid_from && personForm.valid_until && personForm.valid_from > personForm.valid_until)}
          onClick={savePerson}>{editingPersonId ? 'Änderung speichern' : 'Person zuordnen'}</button>
          {editingPersonId && <button disabled={busy} onClick={() => { setEditingPersonId(null); setPersonForm(emptyPersonForm()); }}>Korrektur abbrechen</button>}</div></div>
      <h4>Personen zusammenführen</h4>
      <p>Wähle zwei Einträge ausdrücklich aus. Der Quellname wird zum belegten Alias der Zielperson; Aliase und Aufgabenbeziehungen werden übertragen. Trennen ist möglich, solange die übertragenen Daten und die Zielperson unverändert sind.</p>
      <div className="work-form">
        <label>Quellperson<select aria-label="Quellperson zusammenführen" disabled={busy} value={mergeSourceId}
          onChange={(event) => { setMergeSourceId(event.target.value); setMergeConfirm(false); }}>
          <option value="">Person wählen</option>{people.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Zielperson<select aria-label="Zielperson zusammenführen" disabled={busy} value={mergeTargetId}
          onChange={(event) => { setMergeTargetId(event.target.value); setMergeConfirm(false); }}>
          <option value="">Person wählen</option>{people.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        {mergeConfirm && mergeSourceId && mergeTargetId && mergeSourceId !== mergeTargetId && <p>
          {people.find((item) => item.id === mergeSourceId)?.name} in {people.find((item) => item.id === mergeTargetId)?.name} zusammenführen? Die Quellperson wird aus der Personenliste entfernt und als Alias erhalten.</p>}
        <div className="item-actions">{mergeConfirm
          ? <><button disabled={busy} onClick={mergePeople}>Zusammenführung bestätigen</button>
            <button disabled={busy} onClick={() => setMergeConfirm(false)}>Abbrechen</button></>
          : <button disabled={busy || !mergeSourceId || !mergeTargetId || mergeSourceId === mergeTargetId}
            onClick={() => setMergeConfirm(true)}>Zusammenführen…</button>}</div>
      </div>
      <h5>Zusammenführungsverlauf</h5>
      {personMerges.length === 0 ? <p>Keine Zusammenführung in diesem Projekt.</p> : <div className="item-list">
        {personMerges.map((merge) => <div className="item-row" key={merge.id}><div>
          <strong>{merge.source_name} → {merge.target_name || 'Zielperson entfernt'}</strong>
          <small>{merge.split_at ? 'Getrennt' : 'Zusammengeführt'} · vorher {relationLabels[merge.source_relation_type]} · {merge.moved_alias_count} Aliase und {merge.moved_link_count} Aufgabenbeziehungen übertragen · {new Date(merge.created_at).toLocaleString()}</small>
        </div>{!merge.split_at && <div className="item-actions">{splitCandidate === merge.id
          ? <><button disabled={busy} onClick={() => splitPeople(merge)}>Trennen bestätigen</button>
            <button disabled={busy} onClick={() => setSplitCandidate(null)}>Abbrechen</button></>
          : <button disabled={busy} onClick={() => setSplitCandidate(merge.id)}>Trennen…</button>}</div>}</div>)}</div>}
      <h4>Personen-Aliase</h4>
      <p>Aliasnamen werden nur nach ausdrücklicher Eingabe mit eigener Herkunft gespeichert. Gleichlautende Personennamen oder Aliase im selben Projekt sind gesperrt.</p>
      {aliases.length === 0 ? <p>Noch kein Aliasname eingetragen.</p> : <div className="item-list">{aliases.map((item) =>
        <div className="item-row" id={`project-alias-${item.id}`} key={item.id}><div>
          <strong>{item.alias} → {item.person_name}</strong><p>{item.source_note}</p>
          <small>Herkunft: {item.source_document_id ? item.source_path : 'Eigene Angabe'} · {personSourceLabels[item.source_status] || fileStatus[item.source_status] || item.source_status}</small>
          {item.source_sha256 && <small>Gespeicherter SHA-256: {item.source_sha256}</small>}
          <small>{confidenceLabels[item.confidence]} · {validityLabels[item.validity_status] || item.validity_status}
            {item.valid_from ? ` · ab ${item.valid_from}` : ''}{item.valid_until ? ` · bis ${item.valid_until}` : ''} · Fassung {item.version}</small></div>
          <div className="item-actions"><button disabled={busy} onClick={() => editAlias(item)}>Bearbeiten</button>
            {aliasDeleteCandidate === item.id ? <><button disabled={busy} onClick={() => removeAlias(item)}>Alias entfernen</button><button disabled={busy} onClick={() => setAliasDeleteCandidate(null)}>Abbrechen</button></>
              : <button disabled={busy} onClick={() => setAliasDeleteCandidate(item.id)}>Entfernen…</button>}</div></div>)}</div>}
      <div className="work-form"><h5>{editingAliasId ? 'Alias korrigieren' : 'Alias hinzufügen'}</h5>
        <label>Projektperson<select aria-label="Alias-Person" disabled={busy || Boolean(editingAliasId)} value={aliasForm.person_id}
          onChange={(event) => setAliasForm({ ...aliasForm, person_id: event.target.value })}>
          <option value="">Person wählen</option>{people.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Aliasname<input aria-label="Aliasname" maxLength={160} disabled={busy} value={aliasForm.alias}
          onChange={(event) => setAliasForm({ ...aliasForm, alias: event.target.value })} /></label>
        <label>Herkunftsnotiz<textarea aria-label="Alias-Herkunft" maxLength={500} disabled={busy} value={aliasForm.source_note}
          onChange={(event) => setAliasForm({ ...aliasForm, source_note: event.target.value })} /></label>
        <label>Quelldatei aus diesem Projekt (optional)<select aria-label="Alias-Quelldatei" disabled={busy}
          value={aliasForm.source_document_id} onChange={(event) => setAliasForm({ ...aliasForm, source_document_id: event.target.value })}>
          <option value="">Nur eigene Angabe</option>{active.documents.map((item) => <option key={item.id} value={item.id}
            disabled={item.status !== 'unveraendert'}>{item.path} · {fileStatus[item.status] || item.status}</option>)}</select></label>
        <label>Vertrauensgrad (eigene Einschätzung)<select aria-label="Alias-Vertrauen" disabled={busy}
          value={aliasForm.confidence} onChange={(event) => setAliasForm({ ...aliasForm, confidence: event.target.value as ProjectPerson['confidence'] })}>
          {Object.entries(confidenceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Gültig ab (optional)<input aria-label="Alias gültig ab" type="date" disabled={busy} value={aliasForm.valid_from}
          onChange={(event) => setAliasForm({ ...aliasForm, valid_from: event.target.value })} /></label>
        <label>Gültig bis (optional)<input aria-label="Alias gültig bis" type="date" disabled={busy} value={aliasForm.valid_until}
          onChange={(event) => setAliasForm({ ...aliasForm, valid_until: event.target.value })} /></label>
        <div className="item-actions"><button disabled={busy || !aliasForm.person_id || !aliasForm.alias.trim() || aliasForm.source_note.trim().length < 5 ||
          Boolean(aliasForm.valid_from && aliasForm.valid_until && aliasForm.valid_from > aliasForm.valid_until)}
          onClick={saveAlias}>{editingAliasId ? 'Alias speichern' : 'Alias hinzufügen'}</button>
          {editingAliasId && <button disabled={busy} onClick={() => { setEditingAliasId(null); setAliasForm(emptyAliasForm()); }}>Korrektur abbrechen</button>}</div></div>
      <h4>Aufgaben und Personen verbinden</h4>
      <p>Eine Aufgabenbeziehung wird nur nach deiner Eingabe mit Herkunftsnotiz gespeichert. Ein Dateibeleg ist optional; geänderte oder entzogene Belege werden beim Abfragen kenntlich gemacht.</p>
      {personTasks.length === 0 ? <p>Noch keine direkte Aufgabenbeziehung eingetragen.</p> : <div className="item-list">{personTasks.map((link) =>
        <div className="item-row" id={`project-person-task-${link.id}`} key={link.id}><div>
          <strong>{link.task_title} → {link.person_name} · {personTaskRelationLabels[link.relation_type]}</strong>
          <p>{link.source_note}</p>
          <small>{personTaskStatusLabels[link.status] || link.status} · Aufgabe: {link.task_status}
            {link.current_task_title && link.current_task_title !== link.task_title ? ` · Jetzt: ${link.current_task_title}` : ''}</small>
          <small>Beleg: {link.source_document_id ? link.source_path : 'Eigene Angabe'} · {personSourceLabels[link.source_status] || fileStatus[link.source_status] || link.source_status}</small>
          {link.source_sha256 && <small>Gespeicherter SHA-256: {link.source_sha256}</small>}
          <small>Personenbeleg: {personSourceLabels[link.person_source_status] || fileStatus[link.person_source_status] || link.person_source_status}</small>
          {link.merged_from_person_name && <small>Vor Zusammenführung: {link.merged_from_person_name} · {personSourceLabels[link.merged_from_source_status || ''] || fileStatus[link.merged_from_source_status || ''] || link.merged_from_source_status} · {validityLabels[link.merged_from_validity_status || ''] || link.merged_from_validity_status} · {link.merged_from_confidence && confidenceLabels[link.merged_from_confidence]}</small>}
          <small>Vertrauen: {confidenceLabels[link.confidence]} · {validityLabels[link.validity_status] || link.validity_status}
            {link.valid_from ? ` · ab ${link.valid_from}` : ''}{link.valid_until ? ` · bis ${link.valid_until}` : ''} · Fassung {link.version}</small></div>
          <div className="item-actions"><button disabled={busy} onClick={() => editPersonTask(link)}>Bearbeiten</button>
            {personTaskDeleteCandidate === link.id ? <><button disabled={busy} onClick={() => removePersonTask(link)}>Verbindung entfernen</button><button disabled={busy} onClick={() => setPersonTaskDeleteCandidate(null)}>Abbrechen</button></>
              : <button disabled={busy} onClick={() => setPersonTaskDeleteCandidate(link.id)}>Entfernen…</button>}</div></div>)}</div>}
      <div className="work-form"><h5>{editingPersonTaskId ? 'Aufgabenbeziehung korrigieren' : 'Aufgabe mit Person verbinden'}</h5>
        <label>Projektperson<select aria-label="Aufgabenbeziehung Person" disabled={busy} value={personTaskForm.person_id}
          onChange={(event) => setPersonTaskForm({ ...personTaskForm, person_id: event.target.value })}>
          <option value="">Person wählen</option>{people.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>Projektaufgabe<select aria-label="Aufgabenbeziehung Aufgabe" disabled={busy} value={personTaskForm.task_id}
          onChange={(event) => setPersonTaskForm({ ...personTaskForm, task_id: event.target.value })}>
          <option value="">Aufgabe wählen</option>{active.tasks.filter((item) => item.status !== 'geloescht').map((item) =>
            <option key={item.id} value={item.id}>{item.title} · {item.status}</option>)}</select></label>
        <label>Rolle bei dieser Aufgabe<select aria-label="Aufgabenbeziehung Rolle" disabled={busy} value={personTaskForm.relation_type}
          onChange={(event) => setPersonTaskForm({ ...personTaskForm, relation_type: event.target.value as PersonTaskLink['relation_type'] })}>
          {Object.entries(personTaskRelationLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Herkunftsnotiz zur Aufgabe<textarea aria-label="Aufgabenbeziehung Herkunft" maxLength={500} disabled={busy}
          value={personTaskForm.source_note} onChange={(event) => setPersonTaskForm({ ...personTaskForm, source_note: event.target.value })} /></label>
        <label>Dateibeleg aus diesem Projekt (optional)<select aria-label="Aufgabenbeziehung Quelldatei" disabled={busy}
          value={personTaskForm.source_document_id} onChange={(event) => setPersonTaskForm({ ...personTaskForm, source_document_id: event.target.value })}>
          <option value="">Nur eigene Angabe</option>{active.documents.map((item) => <option key={item.id} value={item.id}
            disabled={item.status !== 'unveraendert'}>{item.path} · {fileStatus[item.status] || item.status}</option>)}</select></label>
        <label>Vertrauensgrad (eigene Einschätzung)<select aria-label="Aufgabenbeziehung Vertrauen" disabled={busy}
          value={personTaskForm.confidence} onChange={(event) => setPersonTaskForm({ ...personTaskForm, confidence: event.target.value as ProjectPerson['confidence'] })}>
          {Object.entries(confidenceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Gültig ab (optional)<input aria-label="Aufgabenbeziehung gültig ab" type="date" disabled={busy} value={personTaskForm.valid_from}
          onChange={(event) => setPersonTaskForm({ ...personTaskForm, valid_from: event.target.value })} /></label>
        <label>Gültig bis (optional)<input aria-label="Aufgabenbeziehung gültig bis" type="date" disabled={busy} value={personTaskForm.valid_until}
          onChange={(event) => setPersonTaskForm({ ...personTaskForm, valid_until: event.target.value })} /></label>
        <div className="item-actions"><button disabled={busy || !personTaskForm.person_id || !personTaskForm.task_id || personTaskForm.source_note.trim().length < 5 ||
          Boolean(personTaskForm.valid_from && personTaskForm.valid_until && personTaskForm.valid_from > personTaskForm.valid_until)}
          onClick={savePersonTask}>{editingPersonTaskId ? 'Aufgabenbeziehung speichern' : 'Aufgabe und Person verbinden'}</button>
          {editingPersonTaskId && <button disabled={busy} onClick={() => { setEditingPersonTaskId(null); setPersonTaskForm(emptyPersonTaskForm()); }}>Korrektur abbrechen</button>}</div></div>
      <h4>Bewusste Erinnerungen im Projekt</h4>
      <p>Erinnerungen werden nur nach Suche und Auswahl zugeordnet. Der damalige Text, Quellenverweis und die Freigabepolitik bleiben als lokaler Beleg erhalten. Änderungen an der Erinnerung verlangen eine neue ausdrückliche Bestätigung; diese Projektverknüpfung wird nicht automatisch in Modellanfragen aufgenommen.</p>
      {memoryLinks.length === 0 ? <p>Noch keine Erinnerung zugeordnet.</p> : <div className="item-list">{memoryLinks.map((item) =>
        <div className="item-row" id={`project-memory-${item.id}`} key={item.id}><div>
          <strong>{item.memory_text.slice(0, 180)}{item.memory_text.length > 180 ? '…' : ''}</strong>
          <small>{memoryStatusLabels[item.memory_status]} · {memoryPolicyLabels[item.memory_policy]} · Fassung {item.version}</small>
          <small>Ursprüngliche Quelle: {item.memory_source_ref} · Gespeicherter SHA-256: {item.memory_sha256}</small>
          <p>{item.source_note}</p>
          <small>{confidenceLabels[item.confidence]} · {validityLabels[item.validity_status] || item.validity_status}
            {item.valid_from ? ` · ab ${item.valid_from}` : ''}{item.valid_until ? ` · bis ${item.valid_until}` : ''}</small>
          <details><summary>Gespeicherten Erinnerungstext anzeigen</summary><p>{item.memory_text}</p></details>
          {item.memory_status === 'geaendert' && <details><summary>Aktuellen Erinnerungstext ansehen</summary>
            <p>{item.current_memory_text}</p><p>Quelle: {item.current_memory_source_ref} · Policy: {item.current_memory_policy}</p></details>}
        </div><div className="item-actions"><button disabled={busy} onClick={() => editMemoryLink(item)}>Beleg bearbeiten</button>
          {item.memory_status === 'geaendert' && <button disabled={busy} onClick={() => confirmMemoryLink(item)}>Aktuelle Erinnerung bestätigen</button>}
          {memoryDeleteCandidate === item.id ? <><button disabled={busy} onClick={() => removeMemoryLink(item)}>Projektverknüpfung entfernen</button>
            <button disabled={busy} onClick={() => setMemoryDeleteCandidate(null)}>Abbrechen</button></>
            : <button disabled={busy} onClick={() => setMemoryDeleteCandidate(item.id)}>Verknüpfung entfernen…</button>}</div></div>)}</div>}
      <div className="work-form"><h5>{editingMemoryLinkId ? 'Erinnerungsbeleg korrigieren' : 'Erinnerung zuordnen'}</h5>
        <label>Erinnerung suchen<input aria-label="Projekt-Erinnerung suchen" disabled={busy} value={memorySearch}
          onChange={(event) => setMemorySearch(event.target.value)} /></label>
        <button disabled={busy || memorySearch.trim().length < 2} onClick={searchProjectMemories}>Lokale Erinnerungen suchen</button>
        <label>Gefundene Erinnerung<select aria-label="Projekt-Erinnerung auswählen" disabled={busy || Boolean(editingMemoryLinkId)}
          value={memoryForm.memory_id} onChange={(event) => setMemoryForm({ ...memoryForm, memory_id: event.target.value })}>
          <option value="">Erinnerung wählen</option>
          {editingMemoryLinkId && !memoryCandidates.some((item) => item.id === memoryForm.memory_id) &&
            <option value={memoryForm.memory_id}>{memoryLinks.find((item) => item.id === editingMemoryLinkId)?.memory_text.slice(0, 100)}</option>}
          {memoryCandidates.map((item) => <option key={item.id} value={item.id}>
            {item.text.slice(0, 100)} · {memoryPolicyLabels[item.policy]} · {item.source_ref}</option>)}</select></label>
        <label>Herkunftsnotiz zur Projektzuordnung<textarea aria-label="Projekt-Erinnerung Herkunft" maxLength={500} disabled={busy}
          value={memoryForm.source_note} onChange={(event) => setMemoryForm({ ...memoryForm, source_note: event.target.value })} /></label>
        <label>Vertrauensgrad (eigene Einschätzung)<select aria-label="Projekt-Erinnerung Vertrauen" disabled={busy}
          value={memoryForm.confidence} onChange={(event) => setMemoryForm({ ...memoryForm, confidence: event.target.value as ProjectPerson['confidence'] })}>
          {Object.entries(confidenceLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Gültig ab (optional)<input aria-label="Projekt-Erinnerung gültig ab" type="date" disabled={busy}
          value={memoryForm.valid_from} onChange={(event) => setMemoryForm({ ...memoryForm, valid_from: event.target.value })} /></label>
        <label>Gültig bis (optional)<input aria-label="Projekt-Erinnerung gültig bis" type="date" disabled={busy}
          value={memoryForm.valid_until} onChange={(event) => setMemoryForm({ ...memoryForm, valid_until: event.target.value })} /></label>
        <div className="item-actions"><button disabled={busy || !memoryForm.memory_id || memoryForm.source_note.trim().length < 5 ||
          Boolean(memoryForm.valid_from && memoryForm.valid_until && memoryForm.valid_from > memoryForm.valid_until)}
          onClick={saveMemoryLink}>{editingMemoryLinkId ? 'Beleg speichern' : 'Erinnerung zuordnen'}</button>
          {editingMemoryLinkId && <button disabled={busy} onClick={() => { setEditingMemoryLinkId(null); setMemoryForm(emptyMemoryForm()); }}>Korrektur abbrechen</button>}</div></div>
      <h4>Links</h4>{active.links.length === 0 ? <p>Keine Links ausgewählt.</p> : <div className="item-list">{active.links.map((item) =>
        <div className="item-row" key={item}><div><strong>{item}</strong><small>Erreichbarkeit noch nicht geprüft</small></div>
          <button disabled={busy} onClick={() => runTool('browser_read', { url: item }, 'Link prüfen')}>Link prüfen</button></div>)}</div>}
      <div className="item-actions"><button disabled={busy} onClick={edit}>Bearbeiten</button>
        <button disabled={busy} onClick={() => void withBusy(async () => { const check = await api<ProjectCheck>(`/work-states/${active.id}/check`); setActive(check.state); setProjectCheck(check); await Promise.all([loadPeople(active.id), loadPersonTasks(active.id), loadAliases(active.id), loadMemoryLinks(active.id)]); setNotice('Projektquellen, Aufgaben, Personen und Erinnerungen neu geprüft.'); })}>Status neu prüfen</button>
        {!deleteConfirm ? <button disabled={busy} onClick={() => setDeleteConfirm(true)}>Löschen…</button>
          : <><button disabled={busy} onClick={remove}>Endgültig löschen</button><button onClick={() => setDeleteConfirm(false)}>Abbrechen</button></>}</div>
    </section>}
    {(!active || editingId) && <section className="card"><h3>{editingId ? 'Arbeitsstand bearbeiten' : 'Neuen Arbeitsstand sichern'}</h3>
      <div className="work-form"><label>Projektname<input aria-label="Projektname" value={form.project} onChange={(event) => setForm({ ...form, project: event.target.value })} /></label>
        <label>Bestätigte Ergebnisse<textarea aria-label="Bestätigte Ergebnisse" value={form.confirmed} onChange={(event) => setForm({ ...form, confirmed: event.target.value })} /></label>
        <label>Offene Fragen<textarea aria-label="Offene Fragen" value={form.open_questions} onChange={(event) => setForm({ ...form, open_questions: event.target.value })} /></label>
        <label>Nächster Schritt<textarea aria-label="Nächster Schritt" value={form.next_step} onChange={(event) => setForm({ ...form, next_step: event.target.value })} /></label>
        <fieldset><legend>Freigegebene Dateien ausdrücklich auswählen</legend>{availableDocuments.length === 0 && <p>Keine indexierten Dateien verfügbar.</p>}
          {availableDocuments.map((item) => <label key={item.id}><input type="checkbox" checked={form.document_ids.includes(item.id)} onChange={() => toggle('document_ids', item.id)} />{item.path}</label>)}
          {missingDocuments.map((id) => <label key={id}><input type="checkbox" checked onChange={() => toggle('document_ids', id)} />Früherer Dateiverweis {id} (nicht verfügbar)</label>)}</fieldset>
        <fieldset><legend>Offene Aufgaben ausdrücklich auswählen</legend>{availableTasks.length === 0 && <p>Keine offenen Aufgaben verfügbar.</p>}
          {availableTasks.map((item) => <label key={item.id}><input type="checkbox" checked={form.task_ids.includes(item.id)} onChange={() => toggle('task_ids', item.id)} />{item.title}</label>)}
          {missingTasks.map((id) => <label key={id}><input type="checkbox" checked onChange={() => toggle('task_ids', id)} />Früherer Aufgabenverweis {id} (nicht offen)</label>)}</fieldset>
        <label>Links (ein HTTP(S)-Link pro Zeile)<textarea aria-label="Arbeitsstand-Links" value={form.links} onChange={(event) => setForm({ ...form, links: event.target.value })} /></label>
        <div className="item-actions"><button className="primary" disabled={busy || !form.project.trim() || !form.next_step.trim()} onClick={save}>{busy ? 'Speichere…' : 'Arbeitsstand speichern'}</button>
          {editingId && <button disabled={busy} onClick={() => { setEditingId(null); setNotice('Bearbeitung verworfen.'); }}>Abbrechen</button>}</div></div>
    </section>}
  </div>;
}
