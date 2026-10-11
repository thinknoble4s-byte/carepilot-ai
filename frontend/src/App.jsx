import React, { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';

const DB_LABEL = {
  available: 'Database running',
  stopped: 'Database is off (kept off between demos to save cost)',
  starting: 'Database starting, about 5 minutes',
  stopping: 'Database stopping',
  unreachable: 'Database unreachable',
};

const THEMES = [
  { id: 'system', label: 'Auto' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
];

function readTheme() {
  try {
    return localStorage.getItem('carepilot-theme') || 'system';
  } catch (e) {
    return 'system';
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
  try {
    localStorage.setItem('carepilot-theme', theme);
  } catch (e) {
    // Storage unavailable (private window): theme still applies for this visit.
  }
}

function ThemeSwitch() {
  const [theme, setTheme] = useState(readTheme);
  useEffect(() => applyTheme(theme), [theme]);
  return (
    <div className="theme" role="group" aria-label="Theme">
      {THEMES.map((t) => (
        <button key={t.id} type="button" aria-pressed={theme === t.id} onClick={() => setTheme(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export default function App() {
  const [health, setHealth] = useState(null);
  const [roles, setRoles] = useState([]);
  const [role, setRole] = useState('coordinator');
  const [audit, setAudit] = useState(null);
  const [error, setError] = useState('');

  const loadAudit = useCallback(async () => {
    try {
      setAudit(await api('audit?limit=20'));
    } catch (e) {
      setAudit(null);
    }
  }, []);

  const checkHealth = useCallback(async () => {
    try {
      const h = await api('health');
      setHealth(h);
      return h;
    } catch (e) {
      setError(`API not reachable: ${e.message}`);
      return null;
    }
  }, []);

  useEffect(() => {
    api('roles').then(setRoles).catch(() => {});
    checkHealth().then((h) => h?.db === 'available' && loadAudit());
  }, [checkHealth, loadAudit]);

  const switchRole = async (next) => {
    setRole(next);
    setError('');
    try {
      await api('session', { method: 'POST', body: JSON.stringify({ role: next }) });
      loadAudit();
    } catch (e) {
      setError(e.message);
    }
  };

  const dbState = health?.db;
  const current = roles.find((r) => r.id === role);

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">AI</span>
          <div>
            <strong>CarePilot</strong>
            <small>Follow-up &amp; Continuity of Care · MVP demo</small>
          </div>
        </div>
        <div className="controls">
          <label className="viewing">
            Viewing as
            <select value={role} onChange={(e) => switchRole(e.target.value)}>
              {roles.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label} — {r.person}
                </option>
              ))}
            </select>
          </label>
          <ThemeSwitch />
        </div>
      </header>

      <div className="banner">
        Prototype — all patients, records, documents and messages are synthetic. CarePilot organises, reminds, tracks and
        routes. It never diagnoses, prescribes, scores clinical risk, interprets results or advises patients; people decide.
      </div>

      <main>
        <section className="card status">
          <h2>System status</h2>
          <p>
            <span className={`dot ${health ? 'ok' : 'bad'}`} /> API {health ? 'running' : 'not reachable'}
            {health?.env ? ` · ${health.env}` : ''}
          </p>
          <p>
            <span className={`dot ${dbState === 'available' ? 'ok' : dbState === 'starting' ? 'wait' : 'bad'}`} />{' '}
            {DB_LABEL[dbState] || (dbState ? `Database ${dbState}` : 'Checking database…')}
          </p>
          {dbState && dbState !== 'available' && (
            <p className="muted">
              The demo database is switched on by the CarePilot team for scheduled demos. Ask them to start it, then
              reload this page.
            </p>
          )}
          {error && <p className="error">{error}</p>}
        </section>

        <section className="card">
          <h2>Workspace</h2>
          <p className="muted">
            {current ? `${current.person} (${current.label}). ` : ''}
            Patients, order cards and the agent's morning run arrive in Increments 1 and 2.
          </p>
        </section>

        <section className="card wide">
          <h2>Audit trail</h2>
          {audit ? (
            <>
              <p className={audit.chain.valid ? 'muted' : 'error'}>
                Hash chain {audit.chain.valid ? 'intact' : `broken at entry ${audit.chain.firstBadId}`} ·{' '}
                {audit.chain.checked} entries checked
              </p>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>When</th>
                      <th>Actor</th>
                      <th>Action</th>
                      <th>Entity</th>
                      <th>Hash</th>
                    </tr>
                  </thead>
                  <tbody>
                    {audit.entries.map((e) => (
                      <tr key={e.id}>
                        <td>{e.id}</td>
                        <td>{new Date(e.occurred_at).toLocaleString()}</td>
                        <td>
                          {e.actor_name} <span className="muted">({e.actor_type})</span>
                        </td>
                        <td>{e.action}</td>
                        <td>{[e.entity_type, e.entity_id].filter(Boolean).join(' / ')}</td>
                        <td>
                          <code>{e.hash.slice(0, 10)}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <p className="muted">Available when the database is running.</p>
          )}
        </section>
      </main>
    </div>
  );
}
