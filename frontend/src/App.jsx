import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, post, setUser } from './api.js';
import AuditTrail from './AuditTrail.jsx';
import Decisions from './Decisions.jsx';
import Intake from './Intake.jsx';
import Journey from './Journey.jsx';
import Workspace, { CardsByState } from './Workspace.jsx';
import { fmt, Modal, Placeholder, Scope, ScreenHead, UI } from './ui.jsx';

// Tabs per role, as in prototype v7.2: [id, label, count key].
const TABS = {
  coordinator: [['worklist', 'Workspace'], ['journey', 'Patient journey'], ['intake', 'Intake & verification', 'exceptions'],
    ['audit', 'Audit trail'], ['programme', 'Care Plan']],
  oncologist: [['decide', 'Needs my decision', 'decisions'], ['journey', 'Patient journey'], ['audit', 'Audit trail'],
    ['programme', 'Care Plan']],
  chief: [['programme', 'Performance Indicators']],
};
const HOME = { coordinator: 'worklist', oncologist: 'decide', chief: 'programme' };

function readTheme() {
  try {
    return localStorage.getItem('cp_theme');
  } catch (e) {
    return null;
  }
}

function isDark(theme) {
  return theme === 'dark' || (theme !== 'light' && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
}

// The prototype's single Light/Dark toggle (follows the device until clicked).
function ThemeToggle() {
  const [theme, setTheme] = useState(readTheme);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'dark' || theme === 'light') root.setAttribute('data-theme', theme);
    else root.removeAttribute('data-theme');
  }, [theme]);
  const dark = isDark(theme);
  const toggle = () => {
    const next = dark ? 'light' : 'dark';
    try {
      localStorage.setItem('cp_theme', next);
    } catch (e) {
      // Storage unavailable (private window): the choice still applies for this visit.
    }
    setTheme(next);
  };
  return (
    <button type="button" className="theme-toggle" onClick={toggle}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
        {dark ? (
          <path d="M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z" />
        ) : (
          <>
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </>
        )}
      </svg>
      <span>{dark ? 'Dark' : 'Light'}</span>
    </button>
  );
}

function readUser() {
  try {
    return localStorage.getItem('carepilot-user') || 'u_priya';
  } catch (e) {
    return 'u_priya';
  }
}

const initialUser = readUser();
setUser(initialUser);

const DB_LABEL = {
  stopped: 'Database is off (kept off between demos to save cost)',
  starting: 'Database starting, about 5 minutes',
  stopping: 'Database stopping',
  unreachable: 'Database unreachable',
};

export default function App() {
  const [health, setHealth] = useState(null);
  const [users, setUsers] = useState([]);
  const [userId, setUserId] = useState(initialUser);
  const [screen, setScreen] = useState(null);
  const [journeyMrn, setJourneyMrn] = useState('SYN-0007');
  const [today, setToday] = useState(null);
  const [counts, setCounts] = useState({});
  const [version, setVersion] = useState(0);
  const [modal, setModal] = useState(null);
  const [toastText, setToastText] = useState('');
  const [busy, setBusy] = useState(false);

  const user = users.find((u) => u.id === userId);
  const role = user?.role || 'coordinator';
  const dbUp = health?.db === 'available';
  const current = screen && TABS[role].some((t) => t[0] === screen) ? screen : HOME[role];

  const toast = useCallback((text) => {
    setToastText(text);
    clearTimeout(window.__cpToast);
    window.__cpToast = setTimeout(() => setToastText(''), 2800);
  }, []);

  const loadShell = useCallback(async () => {
    try {
      setToday((await api('clock')).today);
      const next = {};
      if (role === 'coordinator') next.exceptions = (await api('exceptions')).length;
      if (role === 'oncologist') next.decisions = (await api('doctor/queue')).length;
      setCounts(next);
    } catch (e) {
      // Counts are a convenience; the screens show their own errors.
    }
  }, [role]);

  const refresh = useCallback(() => {
    setVersion((v) => v + 1);
    loadShell();
  }, [loadShell]);

  const ui = useMemo(() => ({
    toast,
    showModal: (title, sub, body, wide) => setModal({ title, sub, body, wide }),
    closeModal: () => setModal(null),
    refresh,
  }), [toast, refresh]);

  useEffect(() => {
    api('users').then(setUsers).catch(() => {});
    api('health').then(setHealth).catch(() => setHealth({ api: 'down' }));
  }, []);

  useEffect(() => {
    if (dbUp) loadShell();
  }, [dbUp, loadShell, userId]);

  const switchUser = (next) => {
    setUserId(next);
    setUser(next);
    try {
      localStorage.setItem('carepilot-user', next);
    } catch (e) {
      // see above
    }
    setScreen(null);
    setModal(null);
    if (dbUp) api('session', { method: 'POST' }).catch(() => {});
  };

  const demo = async (fn, message) => {
    setBusy(true);
    try {
      toast(message(await fn()));
      refresh();
    } catch (e) {
      toast(e.message);
    } finally {
      setBusy(false);
    }
  };

  const openPatient = (mrn) => {
    setJourneyMrn(mrn);
    setScreen('journey');
    window.scrollTo(0, 0);
  };

  let body;
  if (!health) body = <div className="card"><div className="empty">Connecting…</div></div>;
  else if (!dbUp) {
    body = (
      <div className="card">
        <div className="card-title">Demo is paused</div>
        <div className="small-note">
          {health.api === 'down' ? 'The API is not reachable.' : DB_LABEL[health.db] || `Database ${health.db}`}. The demo
          database is switched on by the CarePilot team for scheduled demos. Ask them to start it, then reload this page.
        </div>
      </div>
    );
  } else if (current === 'worklist') body = <Workspace key={version} onOpenPatient={openPatient} user={user} />;
  else if (current === 'decide') body = <Decisions key={version} onOpenPatient={openPatient} user={user} />;
  else if (current === 'journey') {
    body = <Journey key={`${version}-${userId}-${journeyMrn}`} role={role} mrn={journeyMrn} onSelect={setJourneyMrn} />;
  } else if (current === 'intake') body = <Intake key={version} />;
  else if (current === 'audit') body = <AuditTrail key={version} users={users} role={role} />;
  else if (role === 'chief') {
    body = (
      <>
        <ScreenHead eyebrow={`Chief of hospital · ${user?.person || ''}`} title="Performance Indicators"
          sub="How well the follow-up programme keeps survivors connected. Aggregates only — no patient names or records on this screen." />
        <CardsByState key={version} />
        <Placeholder what="The indicators with baseline and pilot values" when="R6 of the screen rebuild (measured values arrive in Increment 6)" />
      </>
    );
  } else {
    body = (
      <>
        <ScreenHead eyebrow="Configured by the clinical lead · read only here" title="Care Plan"
          sub="The breast cancer follow-up plan and how CarePilot runs it: what is tracked, when it acts, who owns each step, and what the agent may do." />
        <Placeholder what="The read-only Care Plan (schedule, gap rules, deadlines, autonomy policy, approved templates)" when="R6 of the screen rebuild" />
      </>
    );
  }

  return (
    <UI.Provider value={ui}>
      <div className="topbar">
        <div className="topbar-row">
          <div className="brand">
            <div className="cp-glyph"><div className="cp-sq"><i className="dk" /><i className="lt" /></div><div className="cp-bar" /><div className="cp-ai">AI</div></div>
            <div><div className="cp-word">Care<span>Pilot</span></div><div className="cp-tag">Follow-up &amp; Continuity of Care · MVP demo</div></div>
          </div>
          <div className="topbar-right">
            <button type="button" className="scope-btn"
              onClick={() => ui.showModal('What CarePilot is — and is not', 'Applies to every screen.', <Scope />)}>
              What this system is / isn't
            </button>
            <div className="role-switch">
              <label htmlFor="roleSelect">Viewing as</label>
              <select id="roleSelect" value={userId} onChange={(e) => switchUser(e.target.value)}>
                {users.map((u) => <option key={u.id} value={u.id}>{u.label} — {u.person}</option>)}
              </select>
            </div>
            <ThemeToggle />
          </div>
        </div>
        <div className="navtabs">
          {TABS[role].map(([id, label, countKey]) => (
            <button key={id} type="button" className={`navtab ${current === id ? 'active' : ''}`}
              onClick={() => { setScreen(id); window.scrollTo(0, 0); }}>
              {label}{countKey && counts[countKey] ? <> <span className="n">{counts[countKey]}</span></> : null}
            </button>
          ))}
        </div>
        <div className="demo-bar">
          {role !== 'chief' && dbUp && (
            <>
              <span>Demo date <b>{fmt(today)}</b></span>
              {role === 'coordinator' && (
                <>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => demo(() => post('demo/morning-run'), (r) =>
                      `Morning check: ${r.gap_check.new_gaps.length} new gaps, ${r.actions.filter((a) => a.outcome === 'sent').length} reminders sent, ${r.escalations.length} escalations`)}>
                    Run morning check (rules + agent)
                  </button>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => demo(() => post('clock/advance', { days: 1 }), (r) => `Demo date is now ${fmt(r.today)}`)}>
                    Advance 1 day
                  </button>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => demo(() => post('clock/advance', { days: 7 }), (r) => `Demo date is now ${fmt(r.today)}`)}>
                    Advance 7 days
                  </button>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => window.confirm('Reset the demo? All demo data and the audit trail go back to the starting state.')
                      && demo(() => post('demo/reset'), (r) => `Demo reset: ${r.patients} patients, demo date ${fmt(r.clock)}`)}>
                    Reset demo
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>

      <div className="wrap">{body}</div>

      <div className="footer-note">
        <span>⚠️</span>
        <span>
          Prototype — all patients, records, documents and messages are synthetic; uploads and hospital feeds are mocked.
          CarePilot organises, reminds, tracks and routes. It never diagnoses, prescribes, scores clinical risk, interprets
          results or advises patients; people decide. The breast cancer follow-up plan shown is illustrative, pending
          clinical sign-off.
        </span>
      </div>
      <Modal modal={modal} onClose={() => setModal(null)} />
      <div id="toast" className={toastText ? 'show' : ''}>{toastText}</div>
    </UI.Provider>
  );
}
