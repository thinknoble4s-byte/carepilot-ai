import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api.js';

const DB_LABEL = {
  available: 'Database running',
  stopped: 'Database stopped (saves cost when unused)',
  starting: 'Database starting, about 5 minutes',
  stopping: 'Database stopping',
  unreachable: 'Database unreachable',
};

export default function App() {
  const [health, setHealth] = useState(null);
  const [roles, setRoles] = useState([]);
  const [role, setRole] = useState('coordinator');
  const [audit, setAudit] = useState(null);
  const [error, setError] = useState('');
  const poll = useRef(null);

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
    return () => clearInterval(poll.current);
  }, [checkHealth, loadAudit]);

  // Poll only while the database is waking up, so an idle page lets it stop.
  const waitForDb = () => {
    clearInterval(poll.current);
    poll.current = setInterval(async () => {
      const h = await checkHealth();
      if (h?.db === 'available') {
        clearInterval(poll.current);
        loadAudit();
      }
    }, 20000);
  };

  const startDb = async () => {
    setError('');
    try {
      const r = await api('db/start', { method: 'POST' });
      setHealth((h) => ({ ...h, db: r.db }));
      waitForDb();
    } catch (e) {
      setError(e.message);
    }
  };

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
          {dbState === 'stopped' && <button onClick={startDb}>Start database</button>}
          {dbState === 'starting' && <p className="muted">This page checks again every 20 seconds.</p>}
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
