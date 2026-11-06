// R5 — Intake & verification (prototype v7.2 scrIntake): upload → preview → apply; exceptions.
import React, { useCallback, useEffect, useState } from 'react';
import { api, post, upload } from './api.js';
import { PatientLink } from './PatientCard.jsx';
import { fmt, Placeholder, ScreenHead, useUI } from './ui.jsx';

const TABS = [['docs', 'Documents'], ['appt', 'Appointment list'], ['reg', 'Follow-up register']];
const REGISTER_COLUMNS = [['mrn', 'MRN (unique key, SYN-0000)'], ['full_name', 'Name'], ['phone', 'Phone (+91…)'],
  ['caregiver_name', 'Caregiver name'], ['caregiver_phone', 'Caregiver phone'], ['preferred_language', 'Preferred language (Kannada, Hindi, Tamil, Telugu, English)']];
const APPT_COLUMNS = [['mrn', 'MRN (must match a patient)'], ['appointment_date', 'Date (YYYY-MM-DD)'], ['department', 'Department']];

function Upload({ source }) {
  const ui = useUI();
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState('');
  const send = async (dry) => {
    const form = new FormData();
    form.append('source_type', source);
    form.append('file', file);
    setError('');
    try {
      const r = await upload(`imports${dry ? '?preview=true' : ''}`, form);
      if (dry) setPreview(r);
      else {
        setPreview(null);
        ui.toast(`Applied: ${r.rows_created} created, ${r.rows_skipped_identical} already present, ${r.rows_exception} exceptions`);
        ui.refresh();
      }
    } catch (e) {
      setError(e.message);
    }
  };
  return (
    <>
      <div className="btn-row">
        <input type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files[0] || null); setPreview(null); }} />
        <button type="button" className="btn primary" disabled={!file} onClick={() => send(true)}>⬆ Upload and preview</button>
      </div>
      {preview && (
        <div className="v4-item soon" style={{ marginTop: 10 }}>
          <div className="t">Preview — nothing is saved yet</div>
          <div className="d">{preview.rows_read} rows read · <b>{preview.rows_created}</b> would be created · {preview.rows_skipped_identical} already present · <b>{preview.rows_exception}</b> exceptions</div>
          {preview.exceptions.length > 0 && (
            <table className="v4-table" style={{ marginTop: 6 }}>
              <thead><tr><th>Line</th><th>Reason</th><th>Row</th></tr></thead>
              <tbody>{preview.exceptions.map((x) => (
                <tr key={x.row_number}><td>{x.row_number}</td><td><span className="tag live">{x.reason_code}</span></td><td><code>{x.raw_row}</code></td></tr>
              ))}</tbody>
            </table>
          )}
          <div className="btn-row">
            <button type="button" className="btn good" onClick={() => send(false)}>Apply</button>
            <button type="button" className="btn ghost" onClick={() => setPreview(null)}>Cancel</button>
          </div>
        </div>
      )}
      {error && <div className="error">{error}</div>}
    </>
  );
}

// How each appointment reached the list (5 Nov: patients book from the WhatsApp reminder too).
const APPT_SOURCE = { appointment_list: 'Hospital appointment list', patient_button: 'Patient tapped Booked',
  patient_elsewhere: 'Patient tapped Booked elsewhere', patient_slot: 'Patient chose a slot on WhatsApp' };

function Appointments({ items }) {
  return (
    <div className="card">
      <div className="card-title">Booked appointments ({items.length})</div>
      <div className="card-desc">Every appointment on the list: rows from the hospital's list, and bookings patients made from the WhatsApp reminder. Booked and Booked elsewhere are dated the day of the tap; a slot the patient chose carries its date and time.</div>
      <div style={{ overflowX: 'auto' }}>
        <table className="v4-table">
          <thead><tr><th>Patient</th><th>Appointment</th><th>Date</th><th>Time</th><th>How it was booked</th></tr></thead>
          <tbody>
            {items.map((a) => (
              <tr key={a.appointment_id}>
                <td><PatientLink mrn={a.mrn} name={a.full_name} /> <span className="small-note">{a.mrn}</span></td>
                <td>{a.item || a.department}{a.source === 'patient_elsewhere' ? <span className="small-note"> · outside</span> : null}</td>
                <td>{fmt(a.appointment_date)}</td>
                <td>{a.appointment_time ? String(a.appointment_time).slice(0, 5) : '—'}</td>
                <td>{APPT_SOURCE[a.source] || a.source}{a.batch_id ? <span className="small-note"> · batch #{a.batch_id}</span> : null}</td>
              </tr>
            ))}
            {!items.length && <tr><td colSpan={5} className="small-note">No appointments yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Exceptions({ items }) {
  const ui = useUI();
  const dismiss = async (id) => {
    const reason = window.prompt('Reason for dismissing this exception?');
    if (reason === null) return;
    try {
      await post(`exceptions/${id}/dismiss`, { reason });
      ui.toast('Exception dismissed — reason logged');
      ui.refresh();
    } catch (e) {
      ui.toast(e.message);
    }
  };
  return (
    <div className="card">
      <div className="card-title">Open exceptions ({items.length})</div>
      <div className="card-desc">Rows that could not be used. Nothing is created from them — a person fixes the source or dismisses the row with a reason.</div>
      <div className="v4-scroll">
        <table className="v4-table">
          <thead><tr><th>Line</th><th>Reason</th><th>Row as received</th><th /></tr></thead>
          <tbody>
            {items.map((x) => (
              <tr key={x.exception_id}>
                <td>{x.row_number}</td><td><span className="tag live">No match — {x.reason_code.replaceAll('_', ' ')}</span></td>
                <td><code>{x.raw_row}</code></td>
                <td><button type="button" className="btn sm" onClick={() => dismiss(x.exception_id)}>Dismiss</button></td>
              </tr>
            ))}
            {!items.length && <tr><td colSpan={4} className="small-note">No open exceptions.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Intake() {
  const [tab, setTab] = useState('appt');
  const [exceptions, setExceptions] = useState([]);
  const [summary, setSummary] = useState(null);
  const [appointments, setAppointments] = useState([]);
  const load = useCallback(() => {
    api('exceptions').then(setExceptions).catch(() => {});
    api('appointments').then(setAppointments).catch(() => {});
    api('summary').then(setSummary).catch(() => {});
  }, []);
  useEffect(() => load(), [load]);
  const source = tab === 'reg' ? 'register' : 'appointments';
  const mine = exceptions.filter((x) => x.source_type === source);

  return (
    <>
      <ScreenHead eyebrow="Care coordinator · everything arriving from outside" title="Intake & verification"
        sub="Documents, appointment lists and registers in the form the clinic already has them — photos, PDFs, Excel, WhatsApp. Nothing from outside is treated as fact until a person verifies it." />
      <div className="seg" style={{ marginBottom: 14 }}>
        {TABS.map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>
            {label}{id !== 'docs' && exceptions.filter((x) => x.source_type === (id === 'reg' ? 'register' : 'appointments')).length
              ? ` (${exceptions.filter((x) => x.source_type === (id === 'reg' ? 'register' : 'appointments')).length})` : ''}
          </button>
        ))}
      </div>
      {tab === 'docs' && (
        <Placeholder what="Document intake (photo or PDF, AI reading, verification against two identifiers, never auto-promoted)"
          when="a later increment (AI reading starts in Increment 4)" />
      )}
      {tab === 'appt' && (
        <>
          <div className="card">
            <div className="card-title">Today's appointment list</div>
            <div className="card-desc">Upload the day's list (CSV export of the OPD register). Matched rows are stored; only unmatched rows need a person. You see a preview first and nothing is saved until you apply it.</div>
            <Upload source="appointments" />
          </div>
          <Appointments items={appointments} />
          <div className="card">
            <div className="card-title">Columns expected</div>
            <table className="v4-table"><thead><tr><th>Column</th><th>Meaning</th></tr></thead>
              <tbody>{APPT_COLUMNS.map(([c, m]) => <tr key={c}><td><code>{c}</code></td><td>{m}</td></tr>)}</tbody></table>
          </div>
          <Exceptions items={mine} />
        </>
      )}
      {tab === 'reg' && (
        <>
          <div className="card">
            <div className="card-title">Follow-up register</div>
            <div className="card-desc">How a clinic starts on day one: its existing survivorship register is imported and patients are created. Rows that don't pass the checks go to exceptions — nothing is created silently. Importing the same file again creates nothing.</div>
            <div className="grid-4" style={{ margin: '10px 0' }}>
              <div className="card-flat"><div className="kpi-num">{summary?.patients ?? '—'}</div><div className="small-note">patients on follow-up</div></div>
              <div className="card-flat"><div className="kpi-num">{summary?.plans_confirmed ?? '—'}</div><div className="small-note">plans confirmed</div></div>
              <div className="card-flat"><div className="kpi-num">{Object.values(summary?.cards_by_state || {}).reduce((a, b) => a + b, 0)}</div><div className="small-note">order cards</div></div>
              <div className="card-flat"><div className="kpi-num">{mine.length}</div><div className="small-note">register exceptions open</div></div>
            </div>
            <Upload source="register" />
          </div>
          <div className="card">
            <div className="card-title">Column in the register → CarePilot field</div>
            <table className="v4-table"><thead><tr><th>Column</th><th>Field</th></tr></thead>
              <tbody>{REGISTER_COLUMNS.map(([c, m]) => <tr key={c}><td><code>{c}</code></td><td>{m}</td></tr>)}</tbody></table>
          </div>
          <Exceptions items={mine} />
        </>
      )}
    </>
  );
}
