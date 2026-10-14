import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { GUARD, ScreenHead } from './ui.jsx';

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

function Patients({ items, onOpen }) {
  return (
    <section className="card wide">
      <h2>Patients ({items.length})</h2>
      <div className="tablewrap">
        <table className="dtab clickable">
          <thead>
            <tr><th>MRN</th><th>Name</th><th>Language</th><th>Consent</th><th>Agent</th><th>Plan</th><th>Next due</th></tr>
          </thead>
          <tbody>
            {items.map((p) => (
              <tr key={p.mrn} onClick={() => onOpen(p.mrn)} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onOpen(p.mrn)}>
                <td><code>{p.mrn}</code></td>
                <td>{p.full_name}</td>
                <td>{p.preferred_language}</td>
                <td><span className={`pill ${p.consent_status === 'automated_ok' ? 'good' : 'warn'}`}>{CONSENT_LABEL[p.consent_status]}</span></td>
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

export function CardsByState() {
  const [summary, setSummary] = useState(null);
  useEffect(() => {
    api('summary').then(setSummary).catch(() => {});
  }, []);
  const rows = Object.entries(summary?.cards_by_state || {});
  return (
    <main>
      <section className="card wide">
        <h2>Programme today</h2>
        <Summary summary={summary} />
      </section>
    <section className="card">
      <h2>Order cards by state</h2>
      {rows.length ? (
        <table className="dtab">
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
    </main>
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
                  <button type="button" className="btn sm primary" onClick={() => act(`outbox/${m.message_id}/approve`)}>Approve</button>
                  <button type="button" className="btn sm" onClick={() => act(`outbox/${m.message_id}/reject`)}>Reject</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
      <h3>Tasks ({data.tasks.length})</h3>
      <div className="tablewrap">
        <table className="dtab">
          <thead>
            <tr><th>Task</th><th>Patient</th><th>Why</th><th>Assigned</th><th>Due</th><th /></tr>
          </thead>
          <tbody>
            {data.tasks.map((t) => (
              <tr key={t.task_id}>
                <td>{TASK_LABEL[t.task_type] || t.task_type}</td>
                <td><button type="button" className="link" onClick={() => onOpen(t.mrn)}>{t.full_name}</button></td>
                <td className="muted">{t.reason}</td>
                <td>{t.assigned_name || '—'}{t.escalated_on && <span className="pill warn">escalated</span>}</td>
                <td>{t.due_date || '—'}</td>
                <td>{!t.handover_id && <button type="button" className="btn sm" onClick={() => done(t.task_id)}>Done</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function Workspace({ onOpenPatient, user }) {
  const [summary, setSummary] = useState(null);
  const [patients, setPatients] = useState([]);
  const [needs, setNeeds] = useState(null);
  const [message, setMessage] = useState('');

  const load = useCallback(async () => {
    try {
      setSummary(await api('summary'));
      setPatients(await api('patients'));
      setNeeds(await api('needs-you'));
    } catch (e) {
      setMessage(e.message);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
      <ScreenHead eyebrow={`Care coordinator · ${user?.person || ''}`} title="Workspace"
        sub="Everyone on follow-up. The CarePilot agent runs the repetitive coordination work around the clock; you see what needs a person — exceptions, escalations and conversations — and what the agent has already done. Click a patient to open them." />
      {GUARD}
      <main>
        <section className="card wide">
          <Summary summary={summary} />
          {message && <p className="error">{message}</p>}
        </section>
        {needs && <NeedsYou data={needs} onOpen={onOpenPatient} onChanged={load} />}
        <Patients items={patients} onOpen={onOpenPatient} />
      </main>
    </>
  );
}
