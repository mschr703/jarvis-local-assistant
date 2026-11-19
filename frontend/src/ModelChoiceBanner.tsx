import { useEffect, useState } from 'react';
import { api } from './api';

type Task = 'chat' | 'draft' | 'plan' | 'research';
type Choice = { provider: string; effective_models: Record<Task, {
  model: string | null; reason: string;
}> };
const labels: Record<Task, string> = {
  chat: 'Chat', draft: 'Entwurf und Text', plan: 'Plan', research: 'Recherche',
};

export default function ModelChoiceBanner({ task }: { task: Task }) {
  const [current, setCurrent] = useState<Choice | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let active = true;
    void api<Choice>('/settings').then((value) => { if (active) setCurrent(value); })
      .catch((cause: Error) => { if (active) setError(cause.message); });
    return () => { active = false; };
  }, [task]);
  if (error) return <p className="notice error">Modellwahl konnte nicht geladen werden: {error}</p>;
  if (!current) return <p className="notice">Modellwahl wird geladen…</p>;
  const selected = current.effective_models[task];
  return <p className="notice" style={{ overflowWrap: 'anywhere' }} aria-label={`Modellwahl für ${labels[task]}`}>
    {labels[task]}: {current.provider} / {selected.model || 'kein Modell eingerichtet'} · {selected.reason}.
    {current.provider === 'openrouter' ? ' Cloudfreigabe je Anfrage und Budgetprüfung bleiben erforderlich.' : ' Lokale Verarbeitung.'}
  </p>;
}
