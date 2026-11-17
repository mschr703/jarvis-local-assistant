import { useEffect, useRef, useState } from 'react';
import { api } from './api';

type Capability = { name: string; scope: string; effect: string };
type Evaluation = { run_id: string; verdict: string; reason: string; regression_case: number; expected_result: string; version: number; updated_at: string };
type Run = { id: string; tool_name: string; args_json: string; args_hash: string; status: string; cancel_requested: number; result_json: string | null; error: string | null; created_at: string; execution_seconds: number | null; ledger: { status: string; started_at: string; finished_at: string | null } | null; evaluation: Evaluation | null; model_cost_usd: number | null };
type EvaluationCase = Evaluation & { tool_name: string; args_json: string; status: string; result_json: string | null; error: string | null };
type PlanEvaluation = { plan_id: string; verdict: string; source_quality: string; reason: string; regression_case: number; expected_result: string; version: number };
type PlanEvaluationCase = PlanEvaluation & { objective: string; steps_json: string; status: string; error: string | null; rule_version: number | null; usage_id: string | null; cost_usd: number | null; cost_kind: string | null; run_results: { id: string; tool_name: string; status: string; result_json: string | null; error: string | null }[] };
type ModelUsage = { id: string; provider: string; model: string; status: string; input_tokens: number | null; output_tokens: number | null; cost_usd: number; cost_kind: string };
type Document = { id: string; path: string; excluded: number; status: string };
type Activity = { id: string; kind: string; detail: string; created_at: string };
type Plan = { id: string; objective: string; status: string; error: string | null; current_index: number; rule_version: number | null; usage_id: string | null; usage: ModelUsage | null; usage_duration_seconds: number | null; evaluation: PlanEvaluation | null; steps: { tool_name: string; args: Record<string, unknown>; effect: string; status: string; run_id: string | null; display_path?: string }[] };

function DynamicReadResult({ run }: { run: Run }) {
  const result = JSON.parse(run.result_json || '{}') as { url?: string; title?: string;
    text?: string; status_code?: number; screenshot?: string };
  return <div className="dynamic-read-result">
    <p style={{ overflowWrap: 'anywhere' }}><strong>Gelesene Seite:</strong> {result.url && <a href={result.url} target="_blank" rel="noopener noreferrer">{result.url}</a>}</p>
    <p><strong>Titel:</strong> {result.title || 'ohne Titel'} · <strong>Status:</strong> {result.status_code || 'unbekannt'}</p>
    <details><summary>Gelesenen DOM-Ausschnitt ansehen</summary><p style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{result.text}</p></details>
    {result.screenshot && <img src={`/api/runs/${run.id}/screenshot`} alt="Screenshot der gelesenen öffentlichen Seite" style={{ maxWidth: '100%', height: 'auto' }} />}
  </div>;
}

type CadBuildResult = { model_id: string; template: string; description: string; dimensions_mm: Record<string, number>;
  source_sha256: string; preview: boolean };

function CadResult({ run, onExport, acting }: { run: Run;
  onExport: (result: CadBuildResult, format: 'step' | 'stl') => void; acting: string[] }) {
  const result = JSON.parse(run.result_json || '{}') as CadBuildResult;
  return <div className="dynamic-read-result"><p><strong>{result.description}</strong></p>
    <p>Halter {result.dimensions_mm.width} × {result.dimensions_mm.depth} × {result.dimensions_mm.thickness} mm;
      zwei Bohrungen Ø {result.dimensions_mm.hole_diameter} mm mit {result.dimensions_mm.hole_spacing} mm Abstand.</p>
    <img src={`/api/runs/${run.id}/cad-preview`} alt="CAD-Vorschau des Halters" style={{ maxWidth: '100%', height: 'auto' }} />
    <small>Modell-ID: {result.model_id} · Vorlage: {result.template} · Skript-SHA-256: {result.source_sha256}</small>
    <p><a href={`/api/runs/${run.id}/cad-script`} download>Geprüftes Modellskript herunterladen</a></p>
    <p>Prüfe Maße und Vorschau. Jeder Export benötigt eine eigene Freigabe.</p>
    <div className="item-actions"><button disabled={acting.includes(`cad:${result.model_id}`)} onClick={() => onExport(result, 'step')}>STEP exportieren</button>
      <button disabled={acting.includes(`cad:${result.model_id}`)} onClick={() => onExport(result, 'stl')}>STL exportieren</button></div>
  </div>;
}

export default function ActivityView({ provider, visible }: { provider: string; visible: boolean }) {
  const [tools, setTools] = useState<Capability[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [activity, setActivity] = useState<Activity[]>([]);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [cases, setCases] = useState<EvaluationCase[]>([]);
  const [planCases, setPlanCases] = useState<PlanEvaluationCase[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState('');
  const [planVerdict, setPlanVerdict] = useState('unclear');
  const [planSourceQuality, setPlanSourceQuality] = useState('unclear');
  const [planReason, setPlanReason] = useState('');
  const [planRegressionCase, setPlanRegressionCase] = useState(false);
  const [planExpectedResult, setPlanExpectedResult] = useState('');
  const [planEvaluationVersion, setPlanEvaluationVersion] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState('');
  const [verdict, setVerdict] = useState('unclear');
  const [reason, setReason] = useState('');
  const [regressionCase, setRegressionCase] = useState(false);
  const [expectedResult, setExpectedResult] = useState('');
  const [evaluationVersion, setEvaluationVersion] = useState(0);
  const [objective, setObjective] = useState('');
  const [allowCloud, setAllowCloud] = useState(false);
  const [toolName, setToolName] = useState('browser_read');
  const [url, setUrl] = useState('https://example.com/');
  const [appId, setAppId] = useState('notepad');
  const [fileId, setFileId] = useState('');
  const [noteTitle, setNoteTitle] = useState('');
  const [noteContent, setNoteContent] = useState('');
  const [cadDescription, setCadDescription] = useState('Rechteckiger Halter: 60 mm breit, 30 mm tief, 6 mm dick; zwei Bohrungen mit 5 mm Durchmesser und 40 mm Abstand der Mittelpunkte.');
  const [cadWidth, setCadWidth] = useState('60');
  const [cadDepth, setCadDepth] = useState('30');
  const [cadThickness, setCadThickness] = useState('6');
  const [cadHoleDiameter, setCadHoleDiameter] = useState('5');
  const [cadHoleSpacing, setCadHoleSpacing] = useState('40');
  const [cadProposing, setCadProposing] = useState(false);
  const [busy, setBusy] = useState(false);
  const submissionRef = useRef(false);
  const actionRef = useRef(new Set<string>());
  const [acting, setActing] = useState<string[]>([]);
  const [notice, setNotice] = useState('');
  const refreshRequest = useRef(0);

  const beginAction = (key: string) => {
    if (actionRef.current.has(key)) return false;
    actionRef.current.add(key); setActing([...actionRef.current]); return true;
  };
  const endAction = (key: string) => {
    actionRef.current.delete(key); setActing([...actionRef.current]);
  };

  const refresh = async () => {
    const request = ++refreshRequest.current;
    const [nextTools, nextRuns, nextDocuments, nextActivity, nextPlans, nextCases, nextPlanCases] = await Promise.all([
      api<Capability[]>('/tools'), api<Run[]>('/runs'), api<Document[]>('/documents'), api<Activity[]>('/activity'), api<Plan[]>('/plans'), api<EvaluationCase[]>('/evaluation-cases'), api<PlanEvaluationCase[]>('/plan-evaluation-cases'),
    ]);
    if (request === refreshRequest.current) {
      setTools(nextTools); setRuns(nextRuns); setDocuments(nextDocuments); setActivity(nextActivity); setPlans(nextPlans); setCases(nextCases); setPlanCases(nextPlanCases);
    }
  };
  useEffect(() => {
    if (!visible) return;
    void refresh().catch((error: Error) => setNotice(error.message));
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 5000);
    return () => { ++refreshRequest.current; window.clearInterval(timer); };
  }, [visible]);
  const run = async () => {
    if (submissionRef.current) return;
    submissionRef.current = true;
    const args = toolName === 'cad_holder_build' ? {
      description: cadDescription, width_mm: Number(cadWidth), depth_mm: Number(cadDepth),
      thickness_mm: Number(cadThickness), hole_diameter_mm: Number(cadHoleDiameter),
      hole_spacing_mm: Number(cadHoleSpacing),
    } : ['browser_read', 'browser_dynamic_read'].includes(toolName) ? { url } : toolName === 'open_app' ? { app_id: appId } : toolName === 'save_note' ? { title: noteTitle, content: noteContent } : { file_id: fileId };
    setBusy(true); setNotice('Werkzeug läuft…');
    try {
      const result = await api<Run>('/runs', { method: 'POST', body: JSON.stringify({
        tool_name: toolName, args, client_request_id: crypto.randomUUID(), background: true,
      }) });
      setNotice(`Auftrag ${result.status}: ${result.error || result.id}`); await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { submissionRef.current = false; setBusy(false); }
  };
  const exportCad = async (result: CadBuildResult, format: 'step' | 'stl') => {
    const key = `cad:${result.model_id}`;
    if (!beginAction(key)) return;
    try {
      const run = await api<Run>('/runs', { method: 'POST', body: JSON.stringify({
        tool_name: 'cad_holder_export', args: { model_id: result.model_id,
          expected_sha256: result.source_sha256, format },
        client_request_id: crypto.randomUUID(), background: true,
      }) });
      setNotice(`Exportauftrag ${run.status}. Nach der Freigabe wird ${format.toUpperCase()} lokal erzeugt.`);
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { endAction(key); }
  };
  const proposeCadDimensions = async () => {
    if (cadProposing) return;
    setCadProposing(true); setNotice('Ollama liest die ausdrücklich genannten Maße…');
    try {
      const result = await api<{ dimensions: Record<string, number>; note: string }>('/cad/holder-proposal', {
        method: 'POST', body: JSON.stringify({ description: cadDescription }),
      });
      setCadWidth(String(result.dimensions.width_mm));
      setCadDepth(String(result.dimensions.depth_mm));
      setCadThickness(String(result.dimensions.thickness_mm));
      setCadHoleDiameter(String(result.dimensions.hole_diameter_mm));
      setCadHoleSpacing(String(result.dimensions.hole_spacing_mm));
      setNotice(result.note);
    } catch (error) { setNotice((error as Error).message); }
    finally { setCadProposing(false); }
  };
  const decide = async (item: Run, approved: boolean) => {
    const key = `run:${item.id}`;
    if (!beginAction(key)) return;
    try {
      await api<Run>(`/runs/${item.id}/decision`, { method: 'POST', body: JSON.stringify({ args_hash: item.args_hash, approved }) });
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { endAction(key); }
  };
  const cancel = async (item: Run) => {
    const key = `run:${item.id}`;
    if (!beginAction(key)) return;
    try { await api<Run>(`/runs/${item.id}/cancel`, { method: 'POST' }); await refresh(); }
    catch (error) { setNotice((error as Error).message); }
    finally { endAction(key); }
  };
  const selectEvaluation = (item: Run) => {
    setSelectedRunId(item.id);
    setVerdict(item.evaluation?.verdict || 'unclear');
    setReason(item.evaluation?.reason || '');
    setRegressionCase(Boolean(item.evaluation?.regression_case));
    setExpectedResult(item.evaluation?.expected_result || '');
    setEvaluationVersion(item.evaluation?.version || 0);
  };
  const saveEvaluation = async () => {
    const key = `evaluation:${selectedRunId}`;
    if (!selectedRunId || !beginAction(key)) return;
    try {
      const saved = await api<Evaluation>(`/runs/${selectedRunId}/evaluation`, {
        method: 'PUT', body: JSON.stringify({ verdict, reason, regression_case: regressionCase,
          expected_result: regressionCase ? expectedResult : '', version: evaluationVersion }),
      });
      setEvaluationVersion(saved.version);
      setNotice('Bewertung gespeichert. Der ursprüngliche Laufbeleg bleibt erhalten.');
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { endAction(key); }
  };
  const propose = async () => {
    if (submissionRef.current) return;
    submissionRef.current = true;
    setBusy(true); setNotice('Erstelle einen überprüfbaren Plan…');
    try {
      const plan = await api<Plan>('/plans', { method: 'POST', body: JSON.stringify({ objective, allow_cloud: allowCloud }) });
      setNotice(`Plan mit ${plan.steps.length} Schritt(en) vorgeschlagen. Prüfe die Argumente vor dem Start.`);
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { submissionRef.current = false; setBusy(false); }
  };
  const planAction = async (plan: Plan, action: 'start' | 'cancel') => {
    const key = `plan:${plan.id}`;
    if (!beginAction(key)) return;
    try { await api<Plan>(`/plans/${plan.id}/${action}`, { method: 'POST' }); await refresh(); }
    catch (error) { setNotice((error as Error).message); }
    finally { endAction(key); }
  };
  const selectPlanEvaluation = (plan: Plan) => {
    setSelectedPlanId(plan.id);
    setPlanVerdict(plan.evaluation?.verdict || 'unclear');
    setPlanSourceQuality(plan.evaluation?.source_quality || 'unclear');
    setPlanReason(plan.evaluation?.reason || '');
    setPlanRegressionCase(Boolean(plan.evaluation?.regression_case));
    setPlanExpectedResult(plan.evaluation?.expected_result || '');
    setPlanEvaluationVersion(plan.evaluation?.version || 0);
  };
  const savePlanEvaluation = async () => {
    const key = `plan-evaluation:${selectedPlanId}`;
    if (!selectedPlanId || !beginAction(key)) return;
    try {
      const saved = await api<PlanEvaluation>(`/plans/${selectedPlanId}/evaluation`, {
        method: 'PUT', body: JSON.stringify({ verdict: planVerdict, source_quality: planSourceQuality,
          reason: planReason, regression_case: planRegressionCase,
          expected_result: planRegressionCase ? planExpectedResult : '', version: planEvaluationVersion }),
      });
      setPlanEvaluationVersion(saved.version);
      setNotice('Planbewertung gespeichert. Der ursprüngliche Plan und seine Belege bleiben erhalten.');
      await refresh();
    } catch (error) { setNotice((error as Error).message); }
    finally { endAction(key); }
  };

  return <div className="section-stack"><h2>Aktivität und Werkzeuge</h2>
    <p className="intro">Jede Aktion hat ein festes Schema und einen sichtbaren Status. Webseiten gelten als fremde Daten und können Jarvis keine neuen Rechte geben.</p>
    {notice && <p className="notice" role="status">{notice}</p>}
    <section className="card"><h3>Mehrschrittauftrag planen</h3><p>Das Modell schlägt nur registrierte Werkzeugschritte vor. Vor dem Start kannst du Namen, Argumente und Wirkungen prüfen. Schreibaktionen warten zusätzlich auf eine Freigabe.</p>
      <div className="memory-form"><textarea aria-label="Auftragsziel" value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="z. B. Lies https://example.com und speichere eine kurze Notiz dazu" />
      {provider === 'openrouter' && <label><input type="checkbox" checked={allowCloud} onChange={(event) => setAllowCloud(event.target.checked)} /> Auftragsziel, aktive Planregel und freigegebene Dateinamen an OpenRouter senden</label>}
      <button className="primary" disabled={busy || !objective.trim() || (provider === 'openrouter' && !allowCloud)} onClick={() => void propose()}>{busy ? 'Plant…' : 'Plan vorschlagen'}</button></div>
      <div className="item-list">{plans.map((plan) => <div key={plan.id} className="plan-card"><strong>{plan.objective}</strong><small>Status: {plan.status} · Schritt {Math.min(plan.current_index + 1, plan.steps.length)}/{plan.steps.length} · {plan.rule_version != null ? `Planregel v${plan.rule_version}` : 'alter Plan ohne Regelbeleg'}</small>
        <small>Modellvorschlag: {plan.usage ? `${plan.usage_duration_seconds != null ? `${plan.usage_duration_seconds.toFixed(2)} s` : 'Dauer offen'} · ${plan.usage.input_tokens ?? '?'} Eingabe- und ${plan.usage.output_tokens ?? '?'} Ausgabetokens · ${plan.usage.cost_kind === 'local_no_api_charge' ? 'keine API-Gebühr (lokal)' : `${plan.usage.cost_usd.toFixed(5)} USD (${plan.usage.cost_kind})`}` : 'kein eindeutiger Modellbeleg'}</small>
        <ol>{plan.steps.map((step, index) => { const evidence = runs.find((run) => run.id === step.run_id); return <li key={index}><strong>{step.tool_name}</strong> · {step.effect} · {step.status}{step.display_path && <small>Vorgeschlagene Datei: {step.display_path}</small>}<pre>{JSON.stringify(step.args, null, 2)}</pre>{evidence && <small>Werkzeugbeleg: {evidence.error || evidence.result_json?.slice(0, 1000) || evidence.status}</small>}</li>; })}</ol>
        {plan.error && <p>{plan.error}</p>}
        {plan.evaluation && <small>Bewertung: {plan.evaluation.verdict} · Quellen: {plan.evaluation.source_quality}</small>}
        <div className="item-actions">{plan.status === 'proposed' && <button disabled={acting.includes(`plan:${plan.id}`)} onClick={() => void planAction(plan, 'start')}>Plan starten</button>}{['proposed', 'running', 'waiting_approval'].includes(plan.status) && <button disabled={acting.includes(`plan:${plan.id}`)} onClick={() => void planAction(plan, 'cancel')}>Plan stoppen</button>}{['succeeded', 'failed', 'cancelled', 'interrupted'].includes(plan.status) && <button onClick={() => selectPlanEvaluation(plan)}>Plan bewerten</button>}</div>
      </div>)}</div></section>
    {selectedPlanId && <section className="card"><h3>Planergebnis bewerten</h3><p>Der Werkzeugstatus und der ursprüngliche Modellbeleg bleiben unverändert. Erfolg und Quellenlage sind deine Einschätzung.</p><div className="memory-form"><label>Planerfolg <select aria-label="Planerfolg" value={planVerdict} onChange={(event) => setPlanVerdict(event.target.value)}><option value="achieved">Ziel erreicht</option><option value="partial">Teilweise erreicht</option><option value="failed">Ziel verfehlt</option><option value="unclear">Unklar</option></select></label><label>Quellenlage <select aria-label="Plan-Quellenlage" value={planSourceQuality} onChange={(event) => setPlanSourceQuality(event.target.value)}><option value="supported">Durch Quellen gestützt</option><option value="unsupported">Nicht gestützt</option><option value="not_applicable">Keine Quellen nötig</option><option value="unclear">Unklar</option></select></label><textarea aria-label="Begründung der Planbewertung" maxLength={2000} value={planReason} onChange={(event) => setPlanReason(event.target.value)} placeholder="Welcher Ergebnisbeleg stützt deine Bewertung?" /><label><input type="checkbox" checked={planRegressionCase} onChange={(event) => setPlanRegressionCase(event.target.checked)} /> Als Plan-Fehler-Testfall sichern</label>{planRegressionCase && <textarea aria-label="Erwartetes Planergebnis" maxLength={500} value={planExpectedResult} onChange={(event) => setPlanExpectedResult(event.target.value)} placeholder="Was sollte mit demselben Ziel passieren?" />}<button className="primary" disabled={!planReason.trim() || (planRegressionCase && (!['partial', 'failed'].includes(planVerdict) || !planExpectedResult.trim())) || acting.includes(`plan-evaluation:${selectedPlanId}`)} onClick={() => void savePlanEvaluation()}>Planbewertung speichern</button></div></section>}
    <section className="card"><h3>Gesicherte Plan-Fehler-Testfälle</h3><p>Die Fälle zeigen ursprüngliches Ziel, Planbeleg, beobachtetes und erwartetes Ergebnis. Sie werden nicht automatisch erneut ausgeführt.</p><div className="item-list">{planCases.length ? planCases.slice(0, 20).map((item) => <div key={item.plan_id} className="item-row"><div><strong>{item.objective}</strong><small>{item.status} · {item.rule_version != null ? `Planregel v${item.rule_version}` : 'Regel unbelegt'} · {item.usage_id ? item.cost_kind === 'local_no_api_charge' ? 'keine API-Gebühr (lokal)' : `${item.cost_usd?.toFixed(5) ?? '?'} USD (${item.cost_kind || 'unklar'})` : 'Modellkosten unbelegt'}</small><p>Schritte: {item.steps_json}</p><p>Beobachtet: {item.error || item.status}</p>{item.run_results.map((run) => <p key={run.id}>Werkzeug {run.tool_name}: {run.error || run.result_json?.slice(0, 1000) || run.status}</p>)}<p>Bewertung: {item.reason}</p><p>Erwartet: {item.expected_result}</p></div></div>) : <p>Keine Plan-Fehler-Testfälle gespeichert.</p>}</div></section>
    <section className="card"><h3>Werkzeug ausführen</h3><div className="memory-form">
      <select aria-label="Werkzeug" value={toolName} onChange={(event) => setToolName(event.target.value)}>{tools.filter((tool) => tool.name !== 'cad_holder_export').map((tool) => <option key={tool.name} value={tool.name}>{tool.name} · {tool.scope} · {tool.effect}</option>)}</select>
      {toolName === 'cad_holder_build' ? <>
        <textarea aria-label="CAD-Beschreibung" maxLength={500} value={cadDescription} onChange={(event) => setCadDescription(event.target.value)} />
        <button disabled={cadProposing || cadDescription.trim().length < 10} onClick={() => void proposeCadDimensions()}>{cadProposing ? 'Ollama liest Maße…' : 'Maße aus Beschreibung vorschlagen'}</button>
        <label>Breite in mm<input aria-label="CAD-Breite" type="number" min="1" max="500" step="0.1" value={cadWidth} onChange={(event) => setCadWidth(event.target.value)} /></label>
        <label>Tiefe in mm<input aria-label="CAD-Tiefe" type="number" min="1" max="500" step="0.1" value={cadDepth} onChange={(event) => setCadDepth(event.target.value)} /></label>
        <label>Dicke in mm<input aria-label="CAD-Dicke" type="number" min="0.1" max="100" step="0.1" value={cadThickness} onChange={(event) => setCadThickness(event.target.value)} /></label>
        <label>Bohrungsdurchmesser in mm<input aria-label="CAD-Bohrungsdurchmesser" type="number" min="0.1" max="100" step="0.1" value={cadHoleDiameter} onChange={(event) => setCadHoleDiameter(event.target.value)} /></label>
        <label>Bohrungsabstand in mm<input aria-label="CAD-Bohrungsabstand" type="number" min="0.1" max="500" step="0.1" value={cadHoleSpacing} onChange={(event) => setCadHoleSpacing(event.target.value)} /></label>
      </> : ['browser_read', 'browser_dynamic_read'].includes(toolName) ? <input aria-label="Öffentliche URL" value={url} onChange={(event) => setUrl(event.target.value)} /> : toolName === 'open_app' ? <select aria-label="Anwendung" value={appId} onChange={(event) => setAppId(event.target.value)}><option value="notepad">Notepad</option><option value="calculator">Rechner</option></select> : toolName === 'save_note' ? <><input aria-label="Notiztitel" value={noteTitle} onChange={(event) => setNoteTitle(event.target.value)} /><textarea aria-label="Notiztext" value={noteContent} onChange={(event) => setNoteContent(event.target.value)} /></> : <select aria-label="Freigegebene Datei" value={fileId} onChange={(event) => setFileId(event.target.value)}><option value="">Datei auswählen</option>{documents.filter((item) => !item.excluded && item.status === 'indexed').map((item) => <option key={item.id} value={item.id}>{item.path}</option>)}</select>}
      {toolName === 'browser_dynamic_read' && <p className="muted">Geplante Aktion: genau diese öffentliche HTTPS-Seite mit JavaScript im isolierten Chromium lesen. Domain: {(() => { try { return new URL(url).hostname; } catch { return 'ungültige URL'; } })()}. Grenzen: eine Domain, ein Leseaufruf, höchstens 12.000 Textzeichen und 120 Sekunden; keine Anmeldung, Eingabe, Klicks oder Downloads. Ein Screenshot und die erreichte Quellen-URL werden im Auftrag gespeichert. „Stoppen“ beendet den Browserprozess.</p>}
      {toolName === 'cad_holder_build' && <p className="muted">Dieser Pilot baut einen rechteckigen Halter mit genau zwei Bohrungen. Die Maße werden geprüft; die Beschreibung ist ein Beleg und erzeugt keinen freien Python-Code. Die Vorschau entsteht lokal mit Ollama-freier CAD-Laufzeit. STEP oder STL folgen erst nach Prüfung und eigener Freigabe.</p>}
      <button className="primary" onClick={() => void run()} disabled={busy || (toolName === 'open_file' && !fileId) || (toolName === 'save_note' && (!noteTitle.trim() || !noteContent.trim())) || (toolName === 'cad_holder_build' && (!cadDescription.trim() || [cadWidth, cadDepth, cadThickness, cadHoleDiameter, cadHoleSpacing].some((value) => !value || Number(value) <= 0)))}>{busy ? 'Läuft…' : toolName === 'browser_dynamic_read' ? 'Im Browser lesen' : toolName === 'cad_holder_build' ? 'CAD-Vorschau erstellen' : 'Ausführen'}</button>
    </div></section>
    <section className="card"><h3>Aufträge</h3><p>„succeeded“ bestätigt nur die Werkzeugausführung. Den tatsächlichen Erfolg bewertest du anhand des Ergebnisses. Modellkosten sind keinem einzelnen Werkzeuglauf eindeutig zugeordnet.</p><div className="item-list">{runs.map((item) => <div key={item.id} className="item-row"><div><strong>{item.tool_name} · {item.status}{item.cancel_requested ? ' · Stopp angefordert' : ''}</strong><small>{new Date(item.created_at).toLocaleString('de-DE')} · Lauf-ID {item.id}</small>{item.tool_name === 'browser_dynamic_read' && item.status === 'succeeded' && item.result_json ? <DynamicReadResult run={item} /> : item.tool_name === 'cad_holder_build' && item.status === 'succeeded' && item.result_json ? <CadResult run={item} onExport={(result, format) => void exportCad(result, format)} acting={acting} /> : item.tool_name === 'cad_holder_export' && item.status === 'succeeded' && item.result_json ? <p><a href={`/api/runs/${item.id}/cad-file`} download>Freigegebene CAD-Datei herunterladen</a> · {JSON.parse(item.result_json).format.toUpperCase()} · Vorlage {JSON.parse(item.result_json).template} · SHA-256 {JSON.parse(item.result_json).file_sha256}</p> : <p>{item.error || (item.result_json ? JSON.stringify(JSON.parse(item.result_json)) : item.args_json)}</p>}<small>Werkzeugdauer: {item.execution_seconds === null ? 'kein abgeschlossener Werkzeugbeleg' : `${item.execution_seconds.toFixed(2)} s`} · Modellkosten: nicht zugeordnet{item.evaluation ? ` · Bewertung: ${item.evaluation.verdict}` : ''}</small></div><div className="item-actions">{item.status === 'waiting_approval' && <><button disabled={acting.includes(`run:${item.id}`)} onClick={() => void decide(item, true)}>Freigeben</button><button disabled={acting.includes(`run:${item.id}`)} onClick={() => void decide(item, false)}>Ablehnen</button></>}{['queued', 'running'].includes(item.status) && !item.cancel_requested && <button disabled={acting.includes(`run:${item.id}`)} onClick={() => void cancel(item)}>Stoppen</button>}{['succeeded', 'failed', 'cancelled', 'interrupted'].includes(item.status) && <button onClick={() => selectEvaluation(item)}>Ergebnis bewerten</button>}</div></div>)}</div></section>
    {selectedRunId && <section className="card"><h3>Auftragsergebnis bewerten</h3><p>Die Bewertung ergänzt den gespeicherten Lauf; sie ändert weder Werkzeugstatus noch Ergebnis.</p><div className="memory-form"><label>Aufgabenerfolg <select aria-label="Aufgabenerfolg" value={verdict} onChange={(event) => setVerdict(event.target.value)}><option value="achieved">Ziel erreicht</option><option value="partial">Teilweise erreicht</option><option value="failed">Ziel verfehlt</option><option value="unclear">Unklar</option></select></label><textarea aria-label="Begründung der Bewertung" maxLength={2000} value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Welcher Ergebnisbeleg stützt die Bewertung?" /><label><input type="checkbox" checked={regressionCase} onChange={(event) => setRegressionCase(event.target.checked)} /> Als Fehler-Testfall sichern</label>{regressionCase && <textarea aria-label="Erwartetes Ergebnis im Testfall" maxLength={500} value={expectedResult} onChange={(event) => setExpectedResult(event.target.value)} placeholder="Was sollte bei denselben Eingaben passieren?" />}<button className="primary" disabled={!reason.trim() || (regressionCase && (!['partial', 'failed'].includes(verdict) || !expectedResult.trim())) || acting.includes(`evaluation:${selectedRunId}`)} onClick={() => void saveEvaluation()}>Bewertung speichern</button></div></section>}
    <section className="card"><h3>Gesicherte Fehler-Testfälle</h3><p>Diese Fälle enthalten Werkzeug, Eingaben, beobachteten Status und erwartetes Ergebnis. Sie werden nicht automatisch erneut ausgeführt.</p><div className="item-list">{cases.length ? cases.map((item) => <div key={item.run_id} className="item-row"><div><strong>{item.tool_name} · {item.status}</strong><small>Lauf-ID {item.run_id}</small><p>Eingaben: {item.args_json}</p><p>Beobachtet: {item.error || item.result_json || 'kein Werkzeugergebnis'}</p><p>Bewertung: {item.reason}</p><p>Erwartet: {item.expected_result}</p></div></div>) : <p>Keine Fehler-Testfälle gespeichert.</p>}</div></section>
    <section className="card"><h3>Protokoll</h3><div className="item-list">{activity.slice(0, 50).map((item) => <div key={item.id} className="item-row"><div><strong>{item.kind}</strong><small>{new Date(item.created_at).toLocaleString('de-DE')}</small><p>{item.detail}</p></div></div>)}</div></section>
  </div>;
}
