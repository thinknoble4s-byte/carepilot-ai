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
      <Stat label="Open handovers" value={summary?.open_handovers} />
      <Stat label="Messages sent" value={summary?.messages_sent} />
      <Stat label="Sends blocked" value={summary?.messages_blocked} />
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
      <button type="button" disabled={busy}
        onClick={() => run(() => post('demo/morning-run'), (r) =>
          `Morning run: ${r.gap_check.new_gaps.length} new gaps, ${r.actions.length} agent decisions ` +
          `(${r.actions.filter((a) => a.outcome === 'sent').length} reminders sent), ${r.escalations.length} escalations.`)}>
        Run the morning check (07:00 gaps, 07:30 agent)
      </button>
      <button type="button" className="secondary" disabled={busy}
        onClick={() => run(() => post('clock/advance', { days: 1 }), (r) => `Demo date is now ${r.today}.`)}>
        Advance 1 day
      </button>
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

const TASK_LABEL = {
  coordinator_review: 'Gate 1 review',
  call_first: 'Call the patient',
  doctor_decision: "Doctor's decision",
  doctor_instruction: "Doctor's instruction",
  urgent_call: 'Urgent call today',
  booking_from_instruction: 'Book from instruction',
  booking_help: 'Help to book',
  cannot_attend: 'Patient cannot come',
  confirm_test_booking: 'Confirm test date',
  contact_needed: 'Contact needed',
  consent_scope_missing: 'Consent scope missing',
};

function NeedsYou({ data, onOpen, onChanged }) {
  const act = async (path, body) => {
    try {
      await post(path, body);
      onChanged();
    } catch (e) {
      window.alert(e.message);
    }
  };
  const done = (id) => {
    const note = window.prompt('What was done?');
    if (note !== null) act(`tasks/${id}/done`, { note });
  };
  return (
    <section className="card wide">
      <h2>Needs you</h2>
      {data.handovers.length > 0 && (
        <>
          <h3>Handovers ({data.handovers.length})</h3>
          <ul className="plain">
            {data.handovers.map((h) => (
              <li key={h.handover_id} className="alert-line">
                <button type="button" className="link" onClick={() => onOpen(h.mrn)}>{h.full_name} ({h.mrn})</button>
                {' '}· {h.stage.replaceAll('_', ' ')}{h.gate2_decision ? ` · ${h.gate2_decision}` : ''}
                {h.patient_words && <div className="muted">“{h.patient_words}”</div>}
              </li>
            ))}
          </ul>
        </>
      )}
      {data.approvals.length > 0 && (
        <>
          <h3>Agent drafts to approve ({data.approvals.length})</h3>
          <ul className="plain">
            {data.approvals.map((m) => (
              <li key={m.message_id}>
                {m.full_name}: <span className="muted">{m.body_en}</span>
                <div className="row">
                  <button type="button" className="small" onClick={() => act(`outbox/${m.message_id}/approve`)}>Approve</button>
                  <button type="button" className="secondary small" onClick={() => act(`outbox/${m.message_id}/reject`)}>Reject</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      <h3>Tasks ({data.tasks.length})</h3>
      <div className="tablewrap">
        <table>
          <thead>
            <tr><th>Task</th><th>Patient</th><th>Why</th><th>Assigned</th><th>Due</th><th /></tr>
          </thead>
          <tbody>
            {data.tasks.map((t) => (
              <tr key={t.task_id}>
                <td>{TASK_LABEL[t.task_type] || t.task_type}</td>
                <td><button type="button" className="link" onClick={() => onOpen(t.mrn)}>{t.full_name}</button></td>
                <td className="muted">{t.reason}</td>
                <td>{t.assigned_name || '—'}{t.escalated_on && <span className="chip warn">escalated</span>}</td>
                <td>{t.due_date || '—'}</td>
                <td>{!t.handover_id && <button type="button" className="secondary small" onClick={() => done(t.task_id)}>Done</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function Workspace({ role, onOpenPatient }) {
  const [summary, setSummary] = useState(null);
  const [patients, setPatients] = useState([]);
  const [exceptions, setExceptions] = useState([]);
  const [needs, setNeeds] = useState(null);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      setSummary(await api('summary'));
      if (role !== 'chief') setPatients(await api('patients'));
      if (role === 'coordinator') {
        setExceptions(await api('exceptions'));
        setNeeds(await api('needs-you'));
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

      {role === 'coordinator' && needs && <NeedsYou data={needs} onOpen={onOpenPatient} onChanged={load} />}

      {role === 'coordinator' && (
        <>
          <ImportPanel onChanged={load} />
          <Exceptions items={exceptions} onChanged={load} />
        </>
      )}

      {role !== 'chief' && <Patients items={patients} onOpen={onOpenPatient} />}
    </main>
  );
}
