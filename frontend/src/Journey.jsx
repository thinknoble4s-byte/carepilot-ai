import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import HandoverPanel from './HandoverPanel.jsx';
import Simulator from './Simulator.jsx';
import { ScreenHead } from './ui.jsx';

const SCOPES = [
  ['whatsapp_patient', 'WhatsApp to patient'],
  ['whatsapp_caregiver', 'WhatsApp to caregiver'],
  ['automated_messaging', 'Automated messaging'],
  ['outside_records', 'Outside records'],
  ['external_care', 'External care'],
];
const SCOPE_LABEL = Object.fromEntries(SCOPES);
const IDENTIFIERS = [
  ['name', 'Name'],
  ['mrn', 'MRN'],
  ['phone', 'Phone'],
];

function toggle(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function ConsentForm({ mrn, today, onDone }) {
  const [reference, setReference] = useState('');
  const [scopes, setScopes] = useState([]);
  const [recordedOn, setRecordedOn] = useState(today || '');
  const [error, setError] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    try {
      await post(`patients/${mrn}/consent`, { consent_reference: reference, scopes, recorded_on: recordedOn });
      onDone();
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <label>Consent reference <input value={reference} onChange={(e) => setReference(e.target.value)} placeholder="CONS-0000" /></label>
      <fieldset>
        <legend>Scopes the patient agreed to</legend>
        {SCOPES.map(([id, label]) => (
          <label key={id} className="check">
            <input type="checkbox" checked={scopes.includes(id)} onChange={() => setScopes(toggle(scopes, id))} /> {label}
          </label>
        ))}
      </fieldset>
      <label>Recorded on <input type="date" value={recordedOn} onChange={(e) => setRecordedOn(e.target.value)} /></label>
      <button type="submit" className="btn primary">Record consent</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

function PlanForm({ mrn, onDone }) {
  const [therapy, setTherapy] = useState('');
  const [endDate, setEndDate] = useState('');
  const [identifiers, setIdentifiers] = useState([]);
  const [error, setError] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    try {
      await post(`patients/${mrn}/plan/confirm`, {
        template_id: 'BC-SURV-1',
        therapy,
        treatment_end_date: endDate,
        identifiers_checked: identifiers,
      });
      onDone();
    } catch (err) {
      setError(err.message);
    }
  };
  return (
    <form className="stack" onSubmit={submit}>
      <p className="muted">Template BC-SURV-1, breast cancer survivorship follow-up (mock, not clinical advice).</p>
      <label>Therapy <input value={therapy} onChange={(e) => setTherapy(e.target.value)} placeholder="e.g. Surgery and endocrine therapy" /></label>
      <label>Treatment end date <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></label>
      <fieldset>
        <legend>Identifiers I checked against the patient record (two needed)</legend>
        {IDENTIFIERS.map(([id, label]) => (
          <label key={id} className="check">
            <input type="checkbox" checked={identifiers.includes(id)} onChange={() => setIdentifiers(toggle(identifiers, id))} /> {label}
          </label>
        ))}
      </fieldset>
      <button type="submit" className="btn primary">Confirm plan and create order cards</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default function Journey({ role, mrn, onSelect }) {
  const [patients, setPatients] = useState([]);
  const [detail, setDetail] = useState(null);
  const [cards, setCards] = useState([]);
  const [messages, setMessages] = useState([]);
  const [today, setToday] = useState('');
  const [error, setError] = useState('');
  const canEdit = role === 'coordinator';

  const load = useCallback(async () => {
    try {
      setError('');
      setDetail(await api(`patients/${mrn}`));
      setCards(await api(`patients/${mrn}/cards`));
      setMessages(await api(`patients/${mrn}/messages`));
    } catch (e) {
      setDetail(null);
      setError(e.message);
    }
  }, [mrn]);

  useEffect(() => {
    api('patients').then(setPatients).catch(() => {});
    api('clock').then((c) => setToday(c.today)).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <>
    <ScreenHead eyebrow={canEdit ? 'Patient journey' : 'Oncologist · read only'} title="Patient journey"
      sub="The longitudinal story of care: diagnosis, treatment, follow-up and care elsewhere. Every event links to its source; anything without an origin is shown as a gap, not assumed. Every step is on the Audit trail." />
    <main>
      <section className="card wide">
        <label className="viewing">
          Patient
          <select value={mrn} onChange={(e) => onSelect(e.target.value)}>
            {patients.map((p) => (
              <option key={p.mrn} value={p.mrn}>{p.mrn} — {p.full_name}</option>
            ))}
          </select>
        </label>
        {error && <p className="error">{error}</p>}
      </section>

      {detail && (
        <>
          <section className="card wide next-action">
            <span className="muted">Next planned action</span>
            <strong>{detail.next_planned_action || 'Routine follow-up'}</strong>
            {canEdit && <StatusControls mrn={mrn} status={detail.status} onDone={load} />}
          </section>

          <section className="card">
            <h2>{detail.full_name}</h2>
            <dl className="facts">
              <dt>MRN</dt><dd><code>{detail.mrn}</code></dd>
              <dt>Phone</dt><dd>{detail.phone}</dd>
              <dt>Language</dt><dd>{detail.preferred_language}</dd>
              <dt>Caregiver</dt><dd>{detail.caregiver_name ? `${detail.caregiver_name} (${detail.caregiver_phone})` : '—'}</dd>
              <dt>Status</dt><dd>{detail.status}</dd>
              <dt>Agent</dt><dd>{detail.agent_on ? 'On' : 'Off'}</dd>
            </dl>
            {detail.open_tasks.length > 0 && (
              <ul className="plain">
                {detail.open_tasks.map((t) => (
                  <li key={t.task_id} className="warn-text">
                    {t.reason}
                    {t.due_date && (
                      <span className="muted"> · {t.assigned_name}, due {t.due_date}{t.escalated_on ? ' (escalated)' : ''}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {detail.appointments.length > 0 && (
              <p className="muted">
                Appointments: {detail.appointments.map((a) => `${a.appointment_date} ${a.department}`).join(' · ')}
              </p>
            )}
          </section>

          <section className="card">
            <h2>Consent</h2>
            {detail.consent ? (
              <>
                <p>
                  {detail.consent.scopes.map((s) => (
                    <span key={s} className={`pill ${s === 'automated_messaging' ? 'good' : 'gray'}`}>{SCOPE_LABEL[s] || s}</span>
                  ))}
                </p>
                <p className="muted">
                  {detail.consent.consent_reference} · recorded {detail.consent.recorded_on} by {detail.consent.recorded_by}
                </p>
              </>
            ) : (
              <p className="warn-text">No consent on record. The agent stays off.</p>
            )}
            {canEdit && (
              <details>
                <summary>Record new consent</summary>
                <ConsentForm mrn={mrn} today={today} onDone={load} />
              </details>
            )}
          </section>

          <section className="card">
            <h2>Care plan</h2>
            {detail.plan ? (
              <dl className="facts">
                <dt>Template</dt><dd>{detail.plan.template_id} v{detail.plan.template_version}</dd>
                <dt>Therapy</dt><dd>{detail.plan.therapy}</dd>
                <dt>Treatment end</dt><dd>{detail.plan.treatment_end_date}</dd>
                <dt>Identifiers checked</dt><dd>{detail.plan.identifiers_checked.join(', ')}</dd>
                <dt>Confirmed by</dt><dd>{detail.plan.confirmed_by}</dd>
              </dl>
            ) : canEdit ? (
              <PlanForm mrn={mrn} onDone={load} />
            ) : (
              <p className="muted">No plan confirmed yet. A coordinator confirms it.</p>
            )}
          </section>

          <HandoverPanel mrn={mrn} handover={detail.handover} tasks={detail.open_tasks} role={role} onChanged={load} />

          <section className="card wide">
            <h2>Order cards ({cards.length})</h2>
            {cards.length ? (
              <div className="tablewrap">
                <table className="dtab">
                  <thead>
                    <tr><th>Item</th><th>Type</th><th>Name</th><th>Due</th><th>State</th><th>Owner</th><th>Cover</th></tr>
                  </thead>
                  <tbody>
                    {cards.map((c) => (
                      <tr key={c.card_id}>
                        <td>{c.item_code}{c.item_code.startsWith('X') ? ' (instruction)' : ''}</td>
                        <td>{c.card_type}</td>
                        <td>{c.name}</td>
                        <td>{c.due_date}</td>
                        <td>{c.state}</td>
                        <td>{c.owner_name}</td>
                        <td>{c.cover_name}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="muted">No cards yet. They are created when the plan is confirmed.</p>
            )}
          </section>
          <Simulator mrn={mrn} messages={messages} canReply={canEdit} onChanged={load} />
        </>
      )}
    </main>
    </>
  );
}

function StatusControls({ mrn, status, onDone }) {
  const change = async (next) => {
    const reason = next === 'Active' ? '' : window.prompt(`Reason for setting ${next}?`);
    if (reason === null) return;
    try {
      await post(`patients/${mrn}/status`, { status: next, reason });
      onDone();
    } catch (e) {
      window.alert(e.message);
    }
  };
  return (
    <div className="row">
      <span className="muted">Status: {status}</span>
      {status !== 'Active' && <button type="button" className="btn sm" onClick={() => change('Active')}>Resume</button>}
      {status !== 'Paused' && <button type="button" className="btn sm" onClick={() => change('Paused')}>Pause</button>}
      {status !== 'Agent off' && <button type="button" className="btn sm" onClick={() => change('Agent off')}>Agent off</button>}
    </div>
  );
}
