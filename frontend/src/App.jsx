import React, { useCallback, useEffect, useState } from 'react';
import { api, setUser } from './api.js';
import AuditTrail from './AuditTrail.jsx';
import Decisions from './Decisions.jsx';
import Journey from './Journey.jsx';
import Workspace from './Workspace.jsx';

const DB_LABEL = {
  available: 'Database running',
  stopped: 'Database is off (kept off between demos to save cost)',
  starting: 'Database starting, about 5 minutes',
  stopping: 'Database stopping',
  unreachable: 'Database unreachable',
};

const TABS = {
  coordinator: ['Workspace', 'Patient journey', 'Audit trail'],
  oncologist: ['Needs my decision', 'Workspace', 'Patient journey', 'Audit trail'],
  chief: ['Summary', 'Audit trail'],
};

const THEMES = [
  { id: 'system', label: 'Auto' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
];

function readStored(key, fallback) {
  try {
    return localStorage.getItem(key) || fallback;
  } catch (e) {
    return fallback;
  }
}

function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    // Storage unavailable (private window): the choice still applies for this visit.
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === 'system') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', theme);
  store('carepilot-theme', theme);
}

function ThemeSwitch() {
  const [theme, setTheme] = useState(() => readStored('carepilot-theme', 'system'));
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

const initialUser = readStored('carepilot-user', 'u_priya');
setUser(initialUser);

export default function App() {
  const [health, setHealth] = useState(null);
  const [users, setUsers] = useState([]);
  const [userId, setUserId] = useState(initialUser);
  const [tab, setTab] = useState('Workspace');
  const [journeyMrn, setJourneyMrn] = useState('SYN-0007');
  const [error, setError] = useState('');

  const user = users.find((u) => u.id === userId);
  const role = user?.role || 'coordinator';
  const tabs = TABS[role];
  const dbUp = health?.db === 'available';

  const checkHealth = useCallback(async () => {
    try {
      setHealth(await api('health'));
    } catch (e) {
      setError(`API not reachable: ${e.message}`);
    }
  }, []);

  useEffect(() => {
    api('users').then(setUsers).catch(() => {});
    checkHealth();
  }, [checkHealth]);

  useEffect(() => {
    if (!tabs.includes(tab)) setTab(tabs[0]);
  }, [tabs, tab]);

  const switchUser = async (next) => {
    setUserId(next);
    setUser(next);
    store('carepilot-user', next);
    setError('');
    if (dbUp) api('session', { method: 'POST' }).catch(() => {});
  };

  const openJourney = (mrn) => {
    setJourneyMrn(mrn);
    setTab('Patient journey');
  };

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
            <select value={userId} onChange={(e) => switchUser(e.target.value)}>
              {users.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.label} — {u.person}
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

      <section className="card status-line">
        <span>
          <span className={`dot ${health ? 'ok' : 'bad'}`} /> API {health ? 'running' : 'not reachable'}
          {health?.env ? ` · ${health.env}` : ''}
        </span>
        <span>
          <span className={`dot ${dbUp ? 'ok' : health?.db === 'starting' ? 'wait' : 'bad'}`} />
          {DB_LABEL[health?.db] || (health?.db ? `Database ${health.db}` : 'Checking database…')}
        </span>
        {error && <span className="error">{error}</span>}
      </section>

      {health && !dbUp ? (
        <section className="card">
          <h2>Demo is paused</h2>
          <p className="muted">
            The demo database is switched on by the CarePilot team for scheduled demos. Ask them to start it, then
            reload this page.
          </p>
        </section>
      ) : (
        dbUp && (
          <>
            <nav className="tabs" aria-label="Screens">
              {tabs.map((t) => (
                <button key={t} type="button" aria-pressed={tab === t} onClick={() => setTab(t)}>
                  {t}
                </button>
              ))}
            </nav>
            {(tab === 'Workspace' || tab === 'Summary') && (
              <Workspace key={userId} role={role} onOpenPatient={openJourney} />
            )}
            {tab === 'Patient journey' && (
              <Journey key={`${userId}-${journeyMrn}`} role={role} mrn={journeyMrn} onSelect={setJourneyMrn} />
            )}
            {tab === 'Needs my decision' && <Decisions key={userId} onOpenPatient={openJourney} />}
            {tab === 'Audit trail' && <AuditTrail users={users} />}
          </>
        )
      )}
    </div>
  );
}
