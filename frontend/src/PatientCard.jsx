// Testing round 31 Oct, defect 2: a patient's name, wherever it appears, opens her details in a pop-up card. The
// card is read only; its buttons go on to the full patient page, her journey or her audit trail.
import React, { useEffect, useState } from 'react';
import { api } from './api.js';
import { fmt, patientStatusPill, Pill, useUI } from './ui.jsx';

const TIER_TONE = { TIER_1_RED: ['bad', 'Red'], TIER_2_AMBER: ['warn', 'Amber'], TIER_3_GREEN: ['good', 'Green'] };
const hm = (t) => (t ? String(t).slice(11, 16) : '');

export function openPatientCard(ui, mrn, name) {
  ui.showModal(name || mrn, 'Patient details (synthetic record)', <PatientCard mrn={mrn} />, true);
}

// The name as a link that opens the card.
export function PatientLink({ mrn, name, bold, style }) {
  const ui = useUI();
  return (
    <a role="button" tabIndex={0} style={style} title="Show patient details"
      onClick={() => openPatientCard(ui, mrn, name)} onKeyDown={(e) => { if (e.key === 'Enter') openPatientCard(ui, mrn, name); }}>
      {bold ? <b>{name}</b> : name}
    </a>
  );
}

function Row({ k, children }) {
  if (children === null || children === undefined || children === '') return null;
  return <div className="small-note" style={{ margin: '3px 0' }}><span style={{ display: 'inline-block', minWidth: 132 }}>{k}</span> <b style={{ color: 'var(--ink)' }}>{children}</b></div>;
}

function PatientCard({ mrn }) {
  const ui = useUI();
  const [p, setP] = useState(null);
  const [error, setError] = useState('');
  useEffect(() => { api(`patients/${mrn}/card`).then(setP).catch((e) => setError(e.message)); }, [mrn]);
  if (error) return <div className="error">{error}</div>;
  if (!p) return <div className="empty">Loading…</div>;
  const r = p.record;
  const go = (screen) => { ui.closeModal(); ui.nav(screen, mrn); };
  const staff = ['coordinator', 'oncologist'].includes(ui.role);
  const upcoming = p.appointments.filter((a) => !ui.today || String(a.appointment_date) >= String(ui.today));
  return (
    <>
      <div className="v4-name" style={{ fontSize: 17 }}>{r?.title || p.full_name} {patientStatusPill(p.status, p.pause_until)}</div>
      <div className="v4-meta" style={{ marginBottom: 10 }}>{p.mrn} · {p.preferred_language}{r?.age ? ` · ${r.age} years` : ''}{r?.sex ? ` · ${r.sex}` : ''}</div>
      <div className="grid-2">
        <div className="card-flat">
          <div className="card-title">Contact</div>
          <Row k="Phone">{p.phone}</Row>
          <Row k="Family">{p.caregiver_name ? `${p.caregiver_name}${r?.caregiver_relation ? ` (${r.caregiver_relation})` : ''}` : 'none on file'}</Row>
          <Row k="Family phone">{p.caregiver_phone}</Row>
          <Row k="Coordinator">{p.coordinator}</Row>
          <Row k="Consent">{p.consent ? `${p.consent.consent_reference}: ${p.consent.scopes.map((s) => s.replaceAll('_', ' ')).join(', ')}` : 'not recorded'}</Row>
          <Row k="Automated agent">{p.agent_on ? 'on' : 'off'}</Row>
          {p.pause_until && <Row k="Paused until">{`${fmt(p.pause_until)}${p.pause_reason ? ` (${p.pause_reason})` : ''}`}</Row>}
        </div>
        <div className="card-flat">
          <div className="card-title">Hospital record</div>
          {r ? (
            <>
              <Row k="Diagnosis">{r.diagnosis}</Row>
              <Row k="Treatment">{r.treatment}</Row>
              <Row k="Now">{r.phase}</Row>
              <Row k="Medicines">{r.medicines?.join('; ')}</Row>
              <Row k="Left arm">{r.arm_baseline && `${r.arm_baseline.status} (${fmt(r.arm_baseline.date)})`}</Row>
              <Row k="Last headache">{r.last_headache && `${r.last_headache.score} out of 10 (${fmt(r.last_headache.date)})`}</Row>
            </>
          ) : <div className="small-note">No triage record on file for this patient.</div>}
          <Row k="Follow-up plan">{p.plan ? `${p.plan.template_id}${p.plan.therapy ? ` · ${p.plan.therapy}` : ''}` : 'not confirmed yet'}</Row>
          {p.plan?.treatment_end_date && <Row k="Treatment ended">{fmt(p.plan.treatment_end_date)}</Row>}
        </div>
      </div>
      <div className="grid-2" style={{ marginTop: 12 }}>
        <div className="card-flat">
          <div className="card-title">Next</div>
          <Row k="Next planned action">{p.next_planned_action || '—'}</Row>
          <Row k="Open tasks">{String(p.open_tasks)}</Row>
          {p.due.map((c) => <Row key={c.item_code} k={`${c.item_code} · ${c.state}`}>{`${c.name}, due ${fmt(c.due_date)}`}</Row>)}
          {upcoming.map((a) => <Row key={`${a.appointment_date}-${a.department}`} k="Appointment">{`${a.department}, ${fmt(a.appointment_date)}`}</Row>)}
          {!p.due.length && !upcoming.length && <div className="small-note">Nothing due.</div>}
        </div>
        <div className="card-flat">
          <div className="card-title">Triage tickets</div>
          {p.triage.length === 0 ? <div className="small-note">None.</div> : p.triage.map((e) => (
            <div key={e.episode_id} className="small-note" style={{ margin: '4px 0' }}>
              <b style={{ color: 'var(--ink)' }}>{e.ticket_id}</b>{' '}
              {e.tier ? <Pill tone={TIER_TONE[e.tier][0]}>{TIER_TONE[e.tier][1]}</Pill> : <Pill tone="brand">Call</Pill>}{' '}
              {e.status === 'closed' ? `closed ${fmt(e.closed_at)} ${hm(e.closed_at)}` : `open since ${fmt(e.opened_at)} ${hm(e.opened_at)}`}
            </div>
          ))}
        </div>
      </div>
      <div className="btn-row" style={{ marginTop: 12 }}>
        {staff && <button type="button" className="btn primary" onClick={() => go('plan')}>Open the patient page</button>}
        {staff && <button type="button" className="btn" onClick={() => go('journey')}>Patient journey</button>}
        {staff && <button type="button" className="btn ghost" onClick={() => go('audit')}>Audit trail</button>}
      </div>
    </>
  );
}
