import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, post, setUser } from './api.js';
import AuditTrail from './AuditTrail.jsx';
import CarePlan from './CarePlan.jsx';
import Decisions from './Decisions.jsx';
import Indicators from './Indicators.jsx';
import Intake from './Intake.jsx';
import PatientPage from './PatientPage.jsx';
import Timeline from './Timeline.jsx';
import Worklist from './Worklist.jsx';
import { fmt, Modal, Scope, UI } from './ui.jsx';

// Tabs per role, as in prototype v7.2: [id, label, count key].
const TABS = {
  coordinator: [['worklist', 'Workspace', 'workspace'], ['journey', 'Patient journey'], ['intake', 'Intake & verification', 'exceptions'],
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

function ResetChoice({ onReset }) {
  return (
    <div className="grid-2">
      <div className="card-flat" style={{ gridColumn: '1 / -1' }}>
        <div className="small-note" style={{ lineHeight: 1.7 }}><b>No plan is seeded in any data set.</b> On each Patient page the
          coordinator chooses one of the three care plans: <b>T1</b> hormone receptor positive, <b>T2</b> hormone receptor negative or
          <b> T3</b> HER2 positive, with the treatment end date. Visits fall at months 3, 6, 9 and 12, and each visit's tests are due
          7 days before it. The blood test goes Due → Booked → Completed → Report received → Report verified (the system verifies a
          hospital report by MRN and test code, a coordinator an outside one); imaging goes Due → Booked → Completed → Verified, set by
          the doctor.</div>
      </div>
      <div className="card-flat">
        <div className="card-title">Use Case 1 data (default)</div>
        <div className="small-note" style={{ lineHeight: 1.7 }}>TP001–TP003 at Sanjeevini Clinic; Anita (owner) and Ravi (cover).
          G0 only, plan visit cards only, one message to the patient, replies saved without action, no tasks.</div>
        <div className="btn-row"><button type="button" className="btn primary" onClick={() => onReset('uc1')}>Reset to Use Case 1</button></div>
      </div>
      <div className="card-flat">
        <div className="card-title">After Use Case 1 (Use Case 2a)</div>
        <div className="small-note" style={{ lineHeight: 1.7 }}>Meena (TP001) and Kavita (TP004), English; clock Mon 9 Nov 10:15.
          Replies on: taps update cards, voice notes and text go through the AI health flag (Sarvam). Send block, hold while a
          proposal waits, tasks for people.</div>
        <div className="btn-row"><button type="button" className="btn" onClick={() => onReset('uc2a')}>Reset to after Use Case 1</button></div>
      </div>
      <div className="card-flat">
        <div className="card-title">Use Case 2b — button replies and HELP</div>
        <div className="small-note" style={{ lineHeight: 1.7 }}>Meena (TP001), Kavita (TP004), Lakshmi (TP005), Farida (TP006) and
          Rekha (TP007); clock Mon 9 Nov 10:15. "Please help me book", "I can't come" and a typed HELP each create a task for
          Anita (cover Ravi) with no AI and nothing sent to the patient; routine reminders are held while the task is open.</div>
        <div className="btn-row"><button type="button" className="btn" onClick={() => onReset('uc2b')}>Reset to Use Case 2b</button></div>
      </div>
      <div className="card-flat">
        <div className="card-title">Use Case 2c — urgent: call the patient today</div>
        <div className="small-note" style={{ lineHeight: 1.7 }}>Meena (TP001), Kavita (TP004), Lakshmi (TP005), Rekha (TP007) and
          Farida (TP006); clock Tue 10 Nov 09:30. When the doctor picks Urgent on a handover, a call-today task goes to Anita by
          16:00, then Ravi by 18:00, then Overdue.</div>
        <div className="btn-row"><button type="button" className="btn" onClick={() => onReset('uc2c')}>Reset to Use Case 2c</button></div>
      </div>
      <div className="card-flat">
        <div className="card-title">Use Case 2T — test cycle</div>
        <div className="small-note" style={{ lineHeight: 1.7 }}>Meena (TP001), Kavita (TP004), Lakshmi (TP005), Farida (TP006),
          Rekha (TP007), Geeta (TP008), Shobha (TP009) and Asha (TP010); clock D0, Mon 2 Nov. Test reminders, the report check
          after the due date, the SIMULATED appointment list and lab, outside-lab files scored by plain rules (95 or more goes to the
          doctor as auto-matched, Live mode), Copies for your information, and the doctor's "Report ready for review".</div>
        <div className="btn-row"><button type="button" className="btn" onClick={() => onReset('uc2t')}>Reset to Use Case 2T</button></div>
      </div>
      <div className="card-flat">
        <div className="card-title">Golden Path data</div>
        <div className="small-note" style={{ lineHeight: 1.7 }}>The 25 Step A patients (Sunita Verma is the demo patient); Priya and
          Meera. Everything switched on: G0 and G1, visit and test cards, caregiver copy, button replies and handovers,
          tasks for people.</div>
        <div className="btn-row"><button type="button" className="btn" onClick={() => onReset('step_a')}>Reset to Golden Path</button></div>
      </div>
    </div>
  );
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
  const [pid, setPid] = useState('SYN-0007');
  const [auditMrn, setAuditMrn] = useState('');
  const [today, setToday] = useState(null);
  const [now, setNow] = useState(null);
  const [demoSettings, setDemoSettings] = useState(null);
  const [counts, setCounts] = useState({});
  const [version, setVersion] = useState(0);
  const [modal, setModal] = useState(null);
  const [toastText, setToastText] = useState('');
  const [busy, setBusy] = useState(false);

  const user = users.find((u) => u.id === userId);
  const role = user?.role || 'coordinator';
  const dbUp = health?.db === 'available';
  const allowed = (s) => TABS[role].some((t) => t[0] === s) || (s === 'plan' && role !== 'chief');
  const current = screen && allowed(screen) ? screen : HOME[role];

  const toast = useCallback((text) => {
    setToastText(text);
    clearTimeout(window.__cpToast);
    window.__cpToast = setTimeout(() => setToastText(''), 2800);
  }, []);

  const loadShell = useCallback(async () => {
    try {
      const c = await api('clock');
      setToday(c.today);
      setNow(c.time);
      const next = {};
      if (role === 'coordinator') {
        const d = await api('demo/settings');
        setDemoSettings(d);
        const w = await api('worklist');
        next.workspace = w.patients.filter((p) => p.main === 'action').length;
      }
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

  const nav = useCallback((next, mrn) => {
    if (next === 'audit') setAuditMrn(mrn || '');
    else if (mrn) setPid(mrn);
    setScreen(next);
    setModal(null);
    window.scrollTo(0, 0);
  }, []);

  const ui = useMemo(() => ({
    toast,
    showModal: (title, sub, body, wide) => setModal({ title, sub, body, wide }),
    closeModal: () => setModal(null),
    refresh,
    nav,
    today,
    now,
    role,
    user,
    name: (id) => users.find((u) => u.id === id)?.person || id,
  }), [toast, refresh, nav, today, now, role, user, users]);

  useEffect(() => {
    api('users').then(setUsers).catch(() => {});
    api('health').then(setHealth).catch(() => setHealth({ api: 'down' }));
  }, []);

  useEffect(() => {
    if (dbUp) loadShell();
  }, [dbUp, loadShell, userId]);

  // The loaded data set decides who can be picked; fall back to its first coordinator.
  useEffect(() => {
    if (users.length && !users.some((u) => u.id === userId)) {
      const first = users.find((u) => u.role === 'coordinator') || users[0];
      setUserId(first.id);
      setUser(first.id);
    }
  }, [users, userId]);

  const [dateInput, setDateInput] = useState('');
  useEffect(() => { if (today) setDateInput(String(today).slice(0, 10)); }, [today]);
  const [timeInput, setTimeInput] = useState('');
  useEffect(() => { if (now) setTimeInput(now); }, [now]);

  const reloadUsers = () => api('users').then(setUsers).catch(() => {});

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
    setVersion((v) => v + 1); // same screen for another coordinator (Anita -> Ravi): reload, the lists differ (Use Case 2c)
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
  } else if (!user) body = <div className="card"><div className="empty">Loading…</div></div>;
  else if (current === 'worklist') body = <Worklist key={version} />;
  else if (current === 'plan') body = <PatientPage key={`${version}-${pid}`} mrn={pid} />;
  else if (current === 'decide') body = <Decisions key={version} />;
  else if (current === 'journey') body = <Timeline key={`${version}-${pid}`} mrn={pid} />;
  else if (current === 'intake') body = <Intake key={version} />;
  else if (current === 'audit') body = <AuditTrail key={`${version}-${auditMrn}`} mrn={auditMrn} users={users} />;
  else if (role === 'chief') body = <Indicators key={version} />;
  else body = <CarePlan key={version} />;

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
            <button key={id} type="button" className={`navtab ${current === id || (current === 'plan' && id === HOME[role]) ? 'active' : ''}`}
              onClick={() => nav(id)}>
              {label}{countKey && counts[countKey] ? <> <span className="n">{counts[countKey]}</span></> : null}
            </button>
          ))}
        </div>
        <div className="demo-bar">
          {role !== 'chief' && dbUp && (
            <>
              <span>Demo date <b>{fmt(today)}</b>{now ? <> · <b>{now}</b></> : null}</span>
              {role === 'coordinator' && (
                <>
                  <span className="demo-date-set">
                    <input type="date" aria-label="Demo date" value={dateInput} onChange={(e) => setDateInput(e.target.value)} />
                    <input type="time" aria-label="Demo time" value={timeInput} onChange={(e) => setTimeInput(e.target.value)} />
                    <button type="button" className="scope-btn" disabled={busy || !dateInput || (dateInput === String(today).slice(0, 10) && timeInput === now)}
                      onClick={() => demo(() => post('clock/set', { date: dateInput, time: timeInput || null }), (r) => `Demo clock set to ${fmt(r.today)} ${r.time}`)}>
                      Set date
                    </button>
                  </span>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => demo(() => post('rules/gap-check'), (r) =>
                      `07:00 gap check: ${r.new_gaps.length} new gap${r.new_gaps.length === 1 ? '' : 's'}${r.suppressed ? `, ${r.suppressed} duplicate${r.suppressed === 1 ? '' : 's'} suppressed` : ''}`)}>
                    Run 07:00 gap check
                  </button>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => demo(() => post('agent/run'), (r) =>
                      `07:30 reminders: ${r.actions.filter((a) => a.outcome === 'sent').length} sent, ${r.actions.filter((a) => a.outcome === 'blocked').length} blocked`)}>
                    Run 07:30 reminders
                  </button>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => demo(() => post('clock/advance', { days: 1 }), (r) => `Demo date is now ${fmt(r.today)}`)}>
                    Advance 1 day
                  </button>
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => demo(() => post('clock/advance', { days: 7 }), (r) => `Demo date is now ${fmt(r.today)}`)}>
                    Advance 7 days
                  </button>
                  {demoSettings?.act_on_replies && (
                    <label className="scope-btn" title="Test UC2a-T4: with Sarvam off every AI call fails and replies take the safe path"
                      style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                      <input type="checkbox" checked={demoSettings.sarvam_enabled} disabled={busy}
                        onChange={(e) => demo(() => post('demo/sarvam', { enabled: e.target.checked }),
                          (r) => (r.sarvam_enabled ? 'Sarvam switched on' : 'Sarvam switched off — AI calls fail, replies go to a person'))} />
                      Sarvam {demoSettings.ai_provider === 'fake' ? '(simulated)' : ''}
                    </label>
                  )}
                  <button type="button" className="scope-btn" disabled={busy}
                    onClick={() => ui.showModal('Reset demo', 'All demo data and the audit trail go back to a starting state. Choose the data set.',
                      <ResetChoice onReset={(scenario) => { ui.closeModal(); demo(() => post('demo/reset', { scenario }), (r) => { reloadUsers(); return `Demo reset (${{ uc1: 'Use Case 1', uc2a: 'after Use Case 1', uc2b: 'Use Case 2b', uc2c: 'Use Case 2c', uc2t: 'Use Case 2T', step_a: 'Golden Path' }[r.scenario]}): ${r.patients} patients, demo date ${fmt(r.clock)}`; }); }} />)}>
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
