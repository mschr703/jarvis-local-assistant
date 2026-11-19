import { useEffect, useRef, useState } from 'react';
import { api } from './api';

export type InstalledModule = {
  id: string; name: string; version: string; api: number; data_version: number; navigation: string;
  view: 'web_component_v1'; element: string; permissions: string[]; dependencies: string[];
  enabled: boolean; requested_enabled: boolean; error: string | null;
};

export function ModuleManagerView({ modules, onChanged }: {
  modules: InstalledModule[]; onChanged: () => void;
}) {
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  async function toggle(item: InstalledModule) {
    setBusy(item.id); setError('');
    try {
      await api(`/modules/${encodeURIComponent(item.id)}/${item.enabled ? 'disable' : 'enable'}`,
        { method: 'POST' });
      onChanged();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(''); }
  }
  return <>
    <h2>Module</h2>
    <p className="intro">Installierte Erweiterungen lassen sich hier aktivieren und deaktivieren. Deaktivieren behält ihre Daten.</p>
    {error && <p className="notice error" role="alert">{error}</p>}
    {!modules.length && <section className="card"><p>Keine Module installiert. Eine lokale Installation wird in der Startanleitung erklärt.</p></section>}
    {modules.map((item) => <section className="card" key={item.id}>
      <div className="card-heading"><h3>{item.name}</h3><span className={item.enabled ? 'badge ok' : 'badge pending'}>{item.enabled ? 'Aktiv' : 'Inaktiv'}</span></div>
      <p>Version {item.version} · Modul-API {item.api} · Datenversion {item.data_version || '?'}</p>
      <p>Vertrauenswürdiger lokaler Code mit Prozessrechten. Deklarierte Nutzung: {item.permissions.join(', ') || 'keine'}{item.dependencies.length ? ` · Benötigt: ${item.dependencies.join(', ')}` : ''}</p>
      {item.error && <p className="notice error" role="alert">{item.error}</p>}
      <button disabled={busy === item.id || item.api === 0} onClick={() => void toggle(item)}>{item.enabled ? 'Deaktivieren' : 'Aktivieren'}</button>
    </section>)}
  </>;
}

export function ModuleHost({ module }: { module: InstalledModule }) {
  const host = useRef<HTMLDivElement>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    const container = host.current;
    const url = `/api/modules/${encodeURIComponent(module.id)}/ui.js`;
    void import(/* @vite-ignore */ url).then(() => {
      if (cancelled || !container) return;
      if (!customElements.get(module.element)) throw new Error('Moduloberfläche ist nicht registriert');
      const element = document.createElement(module.element) as HTMLElement & {
        jarvisApi?: typeof api; moduleId?: string;
      };
      element.jarvisApi = api;
      element.moduleId = module.id;
      container.replaceChildren(element);
      setError('');
    }).catch((cause: Error) => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; container?.replaceChildren(); };
  }, [module.id, module.element]);
  return <>
    <h2>{module.name}</h2>
    {error && <p className="notice error" role="alert">{error}</p>}
    <div ref={host} />
  </>;
}
