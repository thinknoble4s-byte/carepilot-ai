// R4 — Needs my decision (prototype v7.2 scrDecide).
import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { InstructionForm, PatientWords } from './Modals.jsx';
import { PatientLink } from './PatientCard.jsx';
import { ReportReviews, RoutingSettings, TestVerifications } from './TestCycle.jsx';
import { Dl, fmt, ScreenHead, useUI } from './ui.jsx';

export default function Decisions() {
  const ui = useUI();
  const [queue, setQueue] = useState([]);
  const [recent, setRecent] = useState([]);
  const [error, setError] = useState('');
  const [choice, setChoice] = useState({});
  const load = useCallback(() => {
    api('doctor/queue').then(setQueue).catch((e) => setError(e.message));
    api('decisions/recent').then(setRecent).catch(() => {});
  }, []);
  useEffect(() => load(), [load]);

  // The doctor chooses, then submits (UC2a step 9); nothing is pre-selected and CarePilot suggests nothing.
  const decide = async (item, decision) => {
    try {
      await post(`handovers/${item.handover_id}/gate2`, { decision });
      ui.toast(decision === 'urgent' ? 'Urgent — the coordinator calls the patient today' : 'Routine — recorded');
      ui.refresh();
    } catch (e) {
      ui.toast(e.message);
    }
  };
  const open = queue.filter((q) => q.stage === 'awaiting_doctor');
  const pending = queue.filter((q) => q.stage === 'awaiting_instruction');

  return (
    <>
      <ScreenHead eyebrow={`Oncologist · ${ui.user?.person || ''}`} title="Needs my decision"
        sub="Only what needs you: concerns patients reported (confirmed by the coordinator) and differences between sources flagged for the care team. One tap records your decision; the instruction itself stays in your hospital record." />
      <div className="v4-guard">Route, don't judge: CarePilot applies no thresholds, grades or risk scores to what patients report.</div>
      {error && <div className="card"><div className="error">{error}</div></div>}
      <div className="card-title" style={{ margin: '6px 0 8px' }}>Concerns waiting for a decision ({open.length})</div>
      {open.map((c) => (
        <div key={c.handover_id} className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="v4-name">
              <PatientLink mrn={c.mrn} name={c.full_name} />{' '}
              <span className="v4-meta">· {c.mrn} · {c.preferred_language}</span>
            </div>
            <Dl due={c.due_date} label="Decision" today={ui.today} />
          </div>
          <div style={{ marginTop: 8 }}><PatientWords h={c} language={c.preferred_language} /></div>
          <div className="small-note" style={{ marginTop: 4 }}>
            Source: {c.source} · confirmed by {ui.name(c.gate1_by)}{c.call_note ? ` · coordinator's call: "${c.call_note}"` : ''}. CarePilot does not grade concerns — you decide.
          </div>
          <div className="btn-row" role="radiogroup" aria-label="Your decision">
            {[['urgent', 'Urgent — call today'], ['routine', 'Routine']].map(([v, l]) => (
              <label key={v} className="v4-check" style={{ marginRight: 14 }}>
                <input type="radio" name={`g2-${c.handover_id}`} checked={choice[c.handover_id] === v}
                  onChange={() => setChoice({ ...choice, [c.handover_id]: v })} /> {l}
              </label>
            ))}
          </div>
          <div className="btn-row">
            <button type="button" className="btn primary" disabled={!choice[c.handover_id]}
              onClick={() => decide(c, choice[c.handover_id])}>Submit</button>
            <button type="button" className="btn ghost" onClick={() => ui.nav('plan', c.mrn)}>Open patient</button>
          </div>
        </div>
      ))}
      {!open.length && <div className="card"><div className="empty">Nothing waiting. As the coordinator, send a patient's concern to the doctor to see it arrive here.</div></div>}

      {pending.length > 0 && (
        <div className="card">
          <div className="card-title">Decided — waiting for your instruction in the record</div>
          {pending.map((c) => (
            <div key={c.handover_id} className="v4-item req">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <div className="t">{c.full_name} — {c.gate2_decision === 'urgent' ? 'Urgent' : 'Routine'}</div>
                <Dl due={c.due_date} label="Instruction" today={ui.today} />
              </div>
              {c.call_line && <div className="d"><span className="tag live">Urgent</span> Call: <b>{c.call_line}</b> <span className="small-note">(read-only)</span></div>}
              <div className="d">Write your instruction in the hospital record as usual — CarePilot picks it up and creates the card.</div>
              <InstructionForm item={c} inline />
            </div>
          ))}
        </div>
      )}

      <TestVerifications />

      <ReportReviews />

      <RoutingSettings />

      <div className="card">
        <div className="card-title">Sources disagree — flagged for the care team (0)</div>
        <div className="small-note">Nothing flagged. Comparing outside prescriptions with the hospital list arrives with document intake (a later increment).</div>
      </div>

      {recent.length > 0 && (
        <div className="card">
          <div className="card-title">Recent decisions</div>
          <div className="v4-scroll">
            <table className="v4-table">
              <thead><tr><th>Patient</th><th>Decision</th><th>Instruction</th><th>Call (read-only)</th></tr></thead>
              <tbody>{recent.map((d) => (
                <tr key={d.handover_id}><td>{d.full_name}</td>
                  <td>{d.gate2_decision === 'urgent' ? 'Urgent' : 'Routine'} · {fmt(d.gate2_on)}</td>
                  <td>{d.instruction || 'Awaiting instruction'}</td><td>{d.call_line || '—'}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}
    </>
  );
}
