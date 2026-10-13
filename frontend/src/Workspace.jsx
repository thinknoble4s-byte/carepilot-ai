import React, { useCallback, useEffect, useState } from 'react';
import { api, post, upload } from './api.js';

const CONSENT_LABEL = {
  automated_ok: 'Automated messaging allowed',
  no_automated_messaging: 'No automated messaging',
  none: 'No consent on record',
};

function Stat({ label, value }) {
  return (
    <div className="stat">
      <strong>{value ?? '—'}</strong>
      <span>{label}</span>
    </div>
  );
}

function Summary({ summary }) {
  return (
    <div className="stats">
      <Stat label="Patients" value={summary?.patients} />
      <Stat label="Agent on" value={summary?.agent_on} />
      <Stat label="Open exceptions" value={summary?.open_exceptions} />
      <Stat label="Plans confirmed" value={summary?.plans_confirmed} />
      <Stat label="Open tasks" value={summary?.open_tasks} />
      <Stat label="Demo date" value={summary?.today} />
    </div>
  );
}

function DemoControls({ onChanged, setMessage }) {
  const [busy, setBusy] = useState(false);
  const run = async (fn, done) => {
    setBusy(true);
    try {
      const r = await fn();
      setMessage(done(r));
      onChanged();
    } catch (e) {
      setMessage(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="row">
      <button type="button" className="secondary" disabled={busy}
        onClick={() => run(() => post('clock/advance', { days: 7 }), (r) => `Demo date is now ${r.today}.`)}>
        Advance 7 days
      </button>
      <button type="button" className="secondary" disabled={busy}
        onClick={() => {
          if (!window.confirm('Reset the demo? All demo data and the audit trail go back to the starting state.')) return;
          run(() => post('demo/reset'), (r) => `Demo reset: ${r.patients} patients, ${r.open_exceptions} open exceptions, demo date ${r.clock}.`);
        }}>
        Reset demo
      </button>
    </div>
  );
}

function ImportPanel({ onChanged }) {
  const [sourceType, setSourceType] = useState('register');
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append('source_type', sourceType);
    form.append('file', file);
    setError('');
    try {
      setResult(await upload('imports', form));
      onChanged();
    } catch (err) {
      setResult(null);
      setError(err.message);
    }
  };

  return (
    <section className="card">
      <h2>Import a file</h2>
      <form className="stack" onSubmit={submit}>
        <label>
          File type
          <select value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
            <option value="register">Patient register</option>
            <option value="appointments">Appointment list</option>
          </select>
        </label>
        <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files[0] || null)} />
        <button type="submit" disabled={!file}>Import</button>
      </form>
      {result && (
        <p className="muted">
          {result.rows_read} rows read · {result.rows_created} created · {result.rows_skipped_identical} already
          present · {result.rows_exception} exceptions
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function Exceptions({ items, onChanged }) {
  const dismiss = async (id) => {
    const reason = window.prompt('Reason for dismissing this exception?');
    if (reason === null) return;
    try {
      await post(`exceptions/${id}/dismiss`, { reason });
      onChanged();
    } catch (e) {
      window.alert(e.message);
    }
  };
  return (
    <section className="card wide">
      <h2>Open exceptions ({items.length})</h2>
      <p className="muted">Rows that could not be used. Nothing is created from them until someone fixes the source.</p>
      <div className="tablewrap">
        <table>
          <thead>
            <tr><th>File</th><th>Line</th><th>Reason</th><th>Row</th><th /></tr>
          </thead>
          <tbody>
            {items.map((x) => (
              <tr key={x.exception_id}>
                <td>{x.source_type}</td>
                <td>{x.row_number}</td>
                <td><code>{x.reason_code}</code></td>
                <td><code>{x.raw_row}</code></td>
                <td><button type="button" className="secondary small" onClick={() => dismiss(x.exception_id)}>Dismiss</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function Patients({ items, onOpen }) {
  return (
    <section className="card wide">
      <h2>Patients ({items.length})</h2>
      <div className="tablewrap">
        <table className="clickable">
          <thead>
            <tr><th>MRN</th><th>Name</th><th>Language</th><th>Consent</th><th>Agent</th><th>Plan</th><th>Next due</th></tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.mrn} onClick={() => onOpen(p.mrn)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onOpen(p.mrn)}>
                <td><code>{p.mrn}</code></td>
                <td>{p.full_name}</td>
                <td>{p.preferred_language}</td>
                <td><span className={`chip ${p.consent_status === 'automated_ok' ? 'ok' : 'warn'}`}>{CONSENT_LABEL[p.consent_status]}</span></td>
                <td>{p.agent_on ? 'On' : 'Off'}</td>
                <td>{p.has_plan ? 'Confirmed' : <span className="muted">Needs plan</span>}</td>
                <td>{p.next_due || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CardsByState({ byState }) {
  const rows = Object.entries(byState || {});
  return (
    <section className="card">
      <h2>Order cards by state</h2>
      {rows.length ? (
        <table>
          <tbody>
            {rows.map(([state, n]) => (
              <tr key={state}><td>{state}</td><td>{n}</td></tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted">No cards yet. Cards appear when a coordinator confirms a plan.</p>
      )}
      <p className="muted">Aggregates only: no names, phone numbers or MRNs.</p>
    </section>
  );
}

export default function Workspace({ role, onOpenPatient }) {
  const [summary, setSummary] = useState(null);
  const [patients, setPatients] = useState([]);
  const [exceptions, setExceptions] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      setSummary(await api('summary'));
      if (role !== 'chief') setPatients(await api('patients'));
      if (role === 'coordinator') {
        setExceptions(await api('exceptions'));
        setTasks(await api('tasks'));
      }
    } catch (e) {
      setMessage(e.message);
    }
  }, [role]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <main>
      <section className="card wide">
        <h2>{role === 'chief' ? 'Summary' : 'Workspace'}</h2>
        <Summary summary={summary} />
        {role === 'coordinator' && <DemoControls onChanged={load} setMessage={setMessage} />}
        {message && <p className="muted">{message}</p>}
      </section>

      {role === 'chief' && <CardsByState byState={summary?.cards_by_state} />}

      {role === 'coordinator' && (
        <>
          <ImportPanel onChanged={load} />
          <section className="card">
            <h2>Open tasks ({tasks.length})</h2>
            {tasks.length ? (
              <ul className="plain">
                {tasks.map((t) => (
                  <li key={t.task_id}>
                    <button type="button" className="link" onClick={() => onOpenPatient(t.mrn)}>{t.full_name} ({t.mrn})</button>
                    <div className="muted">{t.reason}</div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No open tasks.</p>
            )}
          </section>
          <Exceptions items={exceptions} onChanged={load} />
        </>
      )}

      {role !== 'chief' && <Patients items={patients} onOpen={onOpenPatient} />}
    </main>
  );
}
