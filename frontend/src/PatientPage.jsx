// R3 — the patient page (prototype v7.2 scrPlan "Follow-up plan and actions").
import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { CloseReason, openCall, openHandover, openMessages, openPause, PatientWords } from './Modals.jsx';
import ProposalCard from './Proposals.jsx';
import { Chip, Dl, fmt, GapState, patientStatusPill, Pill, Reason, ScreenHead, useUI } from './ui.jsx';

const LIFE = { visit: ['Due', 'Booked', 'Attended'], test: ['Due', 'Ordered', 'Scheduled', 'Completed', 'Report received', 'Report verified'] };
const SCOPES = [['whatsapp_patient', 'WhatsApp to patient'], ['whatsapp_caregiver', 'WhatsApp to caregiver'],
  ['automated_messaging', 'Automated messaging'], ['outside_records', 'Outside records'], ['external_care', 'External care']];
const CALL_OUTCOME = { noans: 'No answer', book: 'Will come', concern: 'Reported a concern', away: 'Away', ok: 'All well', wrong: 'Wrong number' };
const toggle = (list, v) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

function Stepper({ card }) {
  const steps = LIFE[card.card_type];
  const at = steps.indexOf(card.state);
  return (
    <div className="v5-steps">
      {steps.map((s, i) => (
        <div key={s} className={`v5-step ${i < at ? 'done' : i === at ? 'cur' : ''}`}>
          <div className="b" /><div className="l">{s}</div>
          <div className="d">{i === 0 ? fmt(card.due_date) : i === at && card.appointment_date ? fmt(card.appointment_date) : ''}</div>
        </div>
      ))}
    </div>
  );
}

function OrderCard({ card, today, task }) {
  const d = Math.round((new Date(`${today}T00:00:00`) - new Date(`${card.due_date}T00:00:00`)) / 864e5);
  const done = card.state === 'Attended' || card.state === 'Report verified';
  const instruction = card.item_code.startsWith('X');
  let state;
  if (done) state = <Pill tone="good">Complete</Pill>;
  else if (card.state !== 'Due') state = <Pill tone="good">{card.state}{card.appointment_date ? ` ${fmt(card.appointment_date)}` : ''}</Pill>;
  else if (instruction) state = <Pill tone="brand">From doctor's instruction</Pill>;
  else if (d > 0) state = <Pill tone="bad">{d} days overdue</Pill>;
  else state = <Pill tone="warn">Not booked · due {d === 0 ? 'today' : `in ${-d} days`}</Pill>;
  const over = card.state === 'Due' && d > 0;
  return (
    <div className={`v4-item ${over ? 'over' : instruction ? 'req' : 'soon'}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <div className="t">{card.item_code} — {card.name}</div>{state}
      </div>
      <div className="d">Owner: <b>{card.owner_name}</b> · cover: {card.cover_name} · {card.department}</div>
      <div className="d">Origin: {instruction ? "Doctor's instruction (hospital record)" : `Plan item (plan #${card.care_plan_id}, item ${card.item_code})`}
        {card.booking_source ? ` · booked via ${card.booking_source.replaceAll('_', ' ')}` : ''}</div>
      {card.gap_status && <div className="d">Reminder gap: <GapState status={card.gap_status} reason={card.gap_reason} rule={card.gap_rule} /></div>}
      <Stepper card={card} />
      {task && <div className="d"><Dl due={task.due_date} label={`Booking by ${task.assigned_name}`} today={today} /></div>}
    </div>
  );
}

function ConcernCard({ h, tasks, today, role, patient }) {
  const ui = useUI();
  const [reason, setReason] = useState('');
  const urgentOpen = tasks.some((t) => t.task_type === 'urgent_call');
  const wait = tasks.find((t) => ['coordinator_review', 'call_first', 'doctor_decision', 'doctor_instruction', 'urgent_call'].includes(t.task_type));
  const status = h.instruction ? <Pill tone="good">Instruction received</Pill>
    : h.gate2_decision ? <Pill tone={h.gate2_decision === 'urgent' ? 'bad' : 'brand'}>Doctor: {h.gate2_decision === 'urgent' ? 'Urgent' : 'Routine'}</Pill>
      : h.stage === 'awaiting_doctor' ? <Pill tone="warn">Waiting for the doctor</Pill> : <Pill tone="warn">Waiting for the coordinator</Pill>;
  const chain = [['Reported', h.opened_on], ['Confirmed by coordinator', h.gate1_on], ['Doctor decision', h.gate2_on],
    ['Instruction → order card', h.instruction_on]];
  const close = async () => {
    try {
      await post(`handovers/${h.handover_id}/close`, { reason });
      ui.toast('Handover closed — routine follow-up resumes');
      ui.refresh();
    } catch (e) {
      ui.toast(e.message);
    }
  };
  return (
    <div className={`v4-item ${urgentOpen ? 'over' : 'req'}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <div className="t">Concern — {h.trigger === 'help_keyword' ? 'patient asked for a person' : h.trigger === 'health_matter' ? 'health mention (AI flag)' : 'patient-reported'}</div>{status}
      </div>
      {h.patient_words && <div style={{ marginTop: 6 }}><PatientWords h={h} language={patient.preferred_language} /></div>}
      {h.response_minutes != null && <div className="d">Reviewed by {ui.name(h.reviewed_by)} {h.response_minutes} minutes after the agent stopped.</div>}
      <div className="d">Coordinator: {patient.coordinator || 'Unassigned'} · Doctor: {h.doctor ? ui.name(h.doctor) : 'not yet assigned'}. CarePilot does not interpret or grade concerns.</div>
      <div className="v5-steps" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>
        {chain.map(([l, d]) => (
          <div key={l} className={`v5-step ${d ? 'done' : ''}`}><div className="b" /><div className="l">{l}</div><div className="d">{d ? fmt(d) : ''}</div></div>
        ))}
      </div>
      {wait && <div className="d"><Dl due={wait.due_date} label={`${wait.reason.split(':')[0]} (${wait.assigned_name})`} today={today} />
        {wait.escalated_on && <span className="small-note"> — escalated to cover</span>}</div>}
      {h.gate2_decision && <div className="d">Decision: <b>{h.gate2_decision}</b> by {ui.name(h.gate2_by)}, {fmt(h.gate2_on)}{h.called_on ? ` · patient called ${fmt(h.called_on)}` : ''}</div>}
      {h.instruction && <div className="d">Instruction (hospital record): {h.instruction}</div>}
      {role === 'coordinator' && (
        <div className="btn-row">
          {h.stage === 'awaiting_coordinator' && <button type="button" className="btn sm primary" onClick={() => openHandover(ui, { ...patient, handover_id: h.handover_id })}>Review handover</button>}
          {(urgentOpen || h.stage === 'calling') && <button type="button" className="btn sm primary" onClick={() => openCall(ui, patient)}>Call the patient now</button>}
        </div>
      )}
      {role === 'coordinator' && h.stage === 'ready_to_close' && !urgentOpen && (
        <div style={{ marginTop: 8 }}>
          <label className="v3-field" htmlFor="clr">Close the handover — reason (your name is recorded)</label>
          <CloseReason id="clr" value={reason} onChange={setReason} />
          <div className="btn-row"><button type="button" className="btn primary" disabled={!reason.trim()} onClick={close}>Close handover</button></div>
        </div>
      )}
      {role === 'oncologist' && h.stage === 'awaiting_doctor' && (
        <div className="btn-row"><button type="button" className="btn sm primary" onClick={() => ui.nav('decide')}>Decide on Needs my decision</button></div>
      )}
    </div>
  );
}

function PlanForm({ mrn }) {
  const ui = useUI();
  const [templates, setTemplates] = useState([]);
  const [tid, setTid] = useState('');
  const [therapy, setTherapy] = useState('');
  const [endDate, setEndDate] = useState('');
  const [ids, setIds] = useState([]);
  const [due, setDue] = useState({});
  const [error, setError] = useState('');
  useEffect(() => {
    api('care-plan').then((c) => {
      const list = c.plan_templates.map((t) => t.body);
      setTemplates(list);
      if (list.length) setTid(list[0].template_id);
    }).catch(() => {});
  }, []);
  const t = templates.find((x) => x.template_id === tid);
  const submit = async () => {
    try {
      await post(`patients/${mrn}/plan/confirm`, t?.due_date_entered
        ? { template_id: tid, due_dates: due, identifiers_checked: ids }
        : { template_id: tid, therapy, treatment_end_date: endDate, identifiers_checked: ids });
      ui.toast('Plan confirmed — order cards created');
      ui.refresh();
    } catch (e) {
      setError(e.message);
    }
  };
  if (!t) return null;
  return (
    <div className="card">
      <div className="card-title">Care plan — confirm the follow-up plan</div>
      <div className="card-desc">No order cards exist until the plan is confirmed. Confirming records your name, the date and time, and the template version.</div>
      <label className="v3-field" htmlFor="pfX">Care plan template</label>
      <select id="pfX" className="v3-in" value={tid} onChange={(e) => setTid(e.target.value)}>
        {templates.map((x) => <option key={x.template_id} value={x.template_id}>{x.name} ({x.template_id} v{x.version})</option>)}
      </select>
      {t.due_date_entered ? (
        t.items.map((i) => (
          <div key={i.item_code}>
            <label className="v3-field" htmlFor={`due-${i.item_code}`}>{i.name} — due date</label>
            <input id={`due-${i.item_code}`} type="date" className="v3-in" value={due[i.item_code] || ''}
              onChange={(e) => setDue({ ...due, [i.item_code]: e.target.value })} />
            <div className="small-note">Owner {t.departments[i.department].owner} · cover {t.departments[i.department].cover}</div>
          </div>
        ))
      ) : (
        <div className="grid-2">
          <div><label className="v3-field" htmlFor="pfT">Therapy</label>
            <input id="pfT" className="v3-in" value={therapy} onChange={(e) => setTherapy(e.target.value)} placeholder="e.g. Surgery and endocrine therapy" /></div>
          <div><label className="v3-field" htmlFor="pfE">Treatment end date</label>
            <input id="pfE" type="date" className="v3-in" value={endDate} onChange={(e) => setEndDate(e.target.value)} /></div>
        </div>
      )}
      {t.identifiers_required !== false && (
        <>
          <label className="v3-field">Identifiers I checked against the patient record (two needed)</label>
          <div className="btn-row" style={{ marginTop: 0 }}>
            {[['name', 'Name'], ['mrn', 'MRN'], ['phone', 'Phone']].map(([id, label]) => (
              <label key={id} className="v4-check"><input type="checkbox" checked={ids.includes(id)} onChange={() => setIds(toggle(ids, id))} /> {label}</label>
            ))}
          </div>
        </>
      )}
      <div className="btn-row"><button type="button" className="btn primary" onClick={submit}>Confirm plan</button></div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function ConsentForm({ mrn }) {
  const ui = useUI();
  const [reference, setReference] = useState('');
  const [scopes, setScopes] = useState([]);
  const [recordedOn, setRecordedOn] = useState(ui.today || '');
  const [error, setError] = useState('');
  const submit = async () => {
    try {
      await post(`patients/${mrn}/consent`, { consent_reference: reference, scopes, recorded_on: recordedOn });
      ui.toast('Consent recorded');
      ui.closeModal();
      ui.refresh();
    } catch (e) {
      setError(e.message);
    }
  };
  return (
    <>
      <label className="v3-field" htmlFor="cfR">Consent reference</label>
      <input id="cfR" className="v3-in" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="CONS-0000" />
      <label className="v3-field">Scopes the patient agreed to</label>
      {SCOPES.map(([id, label]) => (
        <label key={id} className="v4-check"><input type="checkbox" checked={scopes.includes(id)} onChange={() => setScopes(toggle(scopes, id))} /> {label}</label>
      ))}
      <label className="v3-field" htmlFor="cfD">Recorded on</label>
      <input id="cfD" type="date" className="v3-in" value={recordedOn} onChange={(e) => setRecordedOn(e.target.value)} />
      <div className="btn-row"><button type="button" className="btn primary" onClick={submit}>Record consent</button></div>
      {error && <div className="error">{error}</div>}
    </>
  );
}

export default function PatientPage({ mrn }) {
  const ui = useUI();
  const ro = ui.role !== 'coordinator';
  const [p, setP] = useState(null);
  const [a, setA] = useState(null);
  const [cards, setCards] = useState([]);
  const [thread, setThread] = useState([]);
  const [hTasks, setHTasks] = useState([]);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      const detail = await api(`patients/${mrn}`);
      setP(detail);
      setA(await api(`patients/${mrn}/assessment`));
      setCards(await api(`patients/${mrn}/cards`));
      setThread(await api(`patients/${mrn}/messages`));
      setHTasks(detail.handover ? (await api(`handovers/${detail.handover.handover_id}`)).tasks.filter((t) => t.status === 'open') : []);
    } catch (e) {
      setError(e.message);
    }
  }, [mrn]);
  useEffect(() => { load(); }, [load]);

  if (error) return <div className="card"><div className="error">{error}</div></div>;
  if (!p || !a) return <div className="card"><div className="empty">Loading…</div></div>;
  const today = a.today;
  const mine = a.reasons.filter((r) => r.tag !== 'agent');
  const agent = a.reasons.filter((r) => r.tag === 'agent');
  const plan = p.plan;
  const month = plan ? Math.max(0, Math.round((new Date(today) - new Date(plan.treatment_end_date)) / 864e5 / 30.4)) : null;
  const horizon = new Date(`${today}T00:00:00`); horizon.setDate(horizon.getDate() + 30);
  const open = cards.filter((c) => !['Attended', 'Report verified'].includes(c.state)
    && (new Date(`${c.due_date}T00:00:00`) <= horizon || c.item_code.startsWith('X')));
  const act = async (fn, msg) => {
    try { await fn(); ui.toast(msg); ui.refresh(); } catch (e) { ui.toast(e.message); }
  };
  const hOpen = p.handover && p.handover.stage !== 'closed';

  return (
    <>
      <a role="button" tabIndex={0} className="backlink" onClick={() => ui.nav(ui.role === 'oncologist' ? 'decide' : 'worklist')}>
        ← Back to {ui.role === 'oncologist' ? 'Needs my decision' : 'Workspace'}
      </a>
      <ScreenHead eyebrow={ro ? 'Oncologist · read only' : 'Workspace · patient'} title="Follow-up plan and actions"
        sub={<>Follow-up plan as order cards — each with a named owner, cover and deadline — plus concerns and contacts.{' '}
          <a role="button" tabIndex={0} onClick={() => ui.nav('journey', mrn)}>Open patient journey</a> ·{' '}
          <a role="button" tabIndex={0} onClick={() => ui.nav('audit', mrn)}>Open audit trail</a></>} />
      <div className="card">
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <div className="v4-name" style={{ fontSize: 17 }}>{p.full_name} {patientStatusPill(p.status, p.pause_until)}</div>
            <div className="v4-meta" style={{ fontSize: 12, marginTop: 4 }}>
              {p.mrn}{plan ? (plan.treatment_end_date ? ` · ${plan.therapy} · treatment completed ${fmt(plan.treatment_end_date)} (month ${month})`
                : ` · plan ${plan.template_id} v${plan.template_version} confirmed by ${ui.name(plan.confirmed_by)}`) : ' · follow-up plan not confirmed yet'}
            </div>
            <div className="v4-meta" style={{ fontSize: 12 }}>Language: <b>{p.preferred_language}</b> · {p.phone} · WhatsApp · Coordinator: <b>{p.coordinator || 'Unassigned'}</b></div>
            <div className="v4-meta" style={{ fontSize: 12 }}>
              {p.caregiver_name ? `Caregiver: ${p.caregiver_name} (${p.caregiver_phone})${p.consent?.scopes.includes('whatsapp_caregiver') ? ' — receives messages with consent' : ' — no WhatsApp consent for the caregiver'}` : 'No caregiver on file'}
            </div>
            <div className="v4-meta" style={{ fontSize: 12 }}>
              Consent: {p.consent ? <><b>given</b> {fmt(p.consent.recorded_on)} ({p.consent.consent_reference}) — {p.consent.scopes.join(', ')}</> : 'not recorded'}
            </div>
            <div className="v4-meta" style={{ fontSize: 12 }}>Next planned action: <b>{p.next_planned_action || 'Routine follow-up'}</b></div>
          </div>
          <div><Chip k={a.main} /></div>
        </div>
      </div>
      {ro && <div className="role-note readonly">Read only. Record decisions and instructions in the hospital record as usual; CarePilot picks them up.</div>}

      <div className="card">
        <div className="need-grid">
          <div><div className="card-title">For you</div>
            <div className="v4-reason">{mine.length ? mine.map((r, i) => <Reason key={i} r={r} today={today} />) : <div>Nothing needs you for this patient.</div>}</div></div>
          <div className="need-agent"><div className="card-title"><span className="v4-k agent">Agent</span> The agent is handling</div>
            <div className="v4-reason">{agent.length ? agent.map((r, i) => <div key={i}>{r.txt}</div>)
              : <div>{!p.agent_on || p.status === 'Agent off' ? 'Agent switched off for this patient.' : 'Nothing in progress.'}</div>}</div></div>
        </div>
        {!ro && (
          <div className="btn-row">
            <button type="button" className="btn primary" onClick={() => openMessages(ui, p)}>Messages</button>
            <button type="button" className="btn" disabled={!cards.some((c) => c.state === 'Due')}
              onClick={() => act(() => post(`patients/${mrn}/remind`), `Approved reminder sent in ${p.preferred_language}`)}>Send reminder</button>
            <button type="button" className="btn" onClick={() => openCall(ui, p)}>Log a call</button>
            <button type="button" className="btn" disabled title="Document intake arrives with AI reading (a later increment)">Upload a document</button>
            {p.status === 'Paused'
              ? <button type="button" className="btn" onClick={() => act(() => post(`patients/${mrn}/status`, { status: 'Active' }), 'Follow-up resumed')}>Resume follow-up</button>
              : <button type="button" className="btn" onClick={() => openPause(ui, p)}>Pause follow-up</button>}
            <button type="button" className="btn ghost" onClick={() => ui.showModal(`Record consent — ${p.full_name}`, 'The latest consent record is the current one. Without automated messaging the agent stays off.', <ConsentForm mrn={mrn} />)}>Record consent</button>
          </div>
        )}
        {hOpen && !ro && p.handover.stage === 'awaiting_coordinator' && (
          <div className="v4-flag" style={{ marginTop: 10 }}>
            <div className="ft">Agent handover — the agent stopped and handed this to you</div>
            {p.handover.patient_words && <div className="fb">"{p.handover.patient_words}" ({p.handover.inbound_kind === 'voice' ? 'voice note' : p.preferred_language})</div>}
            {p.handover.translation && <div className="small-note">English (AI translation): {p.handover.translation}</div>}
            <div className="btn-row"><button type="button" className="btn sm primary" onClick={() => openHandover(ui, { ...p, handover_id: p.handover.handover_id })}>Review handover</button></div>
          </div>
        )}
        {(p.send_block || p.hold) && (
          <div className="role-note" style={{ marginTop: 10 }}>
            {p.send_block ? <><b>Send block on</b> — a handover is open. Only the acknowledgement and the doctor's booking message can go out, whoever sends.</>
              : <><b>Routine reminders on hold</b> — a proposal is waiting for you. Approve or reject it to lift the hold.</>}
          </div>
        )}
        {!ro && (p.proposals || []).filter((x) => x.status === 'drafted').map((x) => <div key={x.proposal_id} style={{ marginTop: 10 }}><ProposalCard p={x} /></div>)}
        <div className="agent-line"><span className="v4-k agent">Agent</span>{' '}
          {p.status === 'Agent off' ? 'Switched off for this patient — you handle all contact.'
            : 'On — sends reminders and booking help for this patient within the autonomy policy; stops and hands over if a health matter comes up.'}
          {!ro && p.status !== 'Paused' && (
            <> <button type="button" className="btn sm ghost"
              onClick={() => {
                if (p.status === 'Agent off') act(() => post(`patients/${mrn}/status`, { status: 'Active' }), 'Agent switched on');
                else {
                  const reason = window.prompt('Why switch the agent off for this patient?');
                  if (reason) act(() => post(`patients/${mrn}/status`, { status: 'Agent off', reason }), 'Agent switched off');
                }
              }}>{p.status === 'Agent off' ? 'Switch agent on' : 'Switch agent off for this patient'}</button></>
          )}
        </div>
      </div>

      {!plan && !ro && <PlanForm mrn={mrn} />}

      {p.handover && (hOpen || p.handover.closed_on === today) && (
        <div className="card">
          <div className="card-title">Patient-reported concerns</div>
          <div className="card-desc">Routed to the doctor, who decides urgent or routine and writes the instruction in the hospital record. Every handoff has an owner and a deadline.</div>
          <ConcernCard h={p.handover} tasks={hTasks} today={today} role={ui.role} patient={p} />
        </div>
      )}

      <div className="card">
        <div className="card-title">Order cards — open follow-up items</div>
        <div className="card-desc">Overdue, due in the next 30 days, booked, or from a doctor's instruction. Each card runs from due to verified evidence.</div>
        {open.length ? open.map((c) => (
          <OrderCard key={c.card_id} card={c} today={today}
            task={p.open_tasks.find((t) => t.task_type === 'booking_from_instruction' && c.item_code.startsWith('X'))} />
        )) : <div className="small-note">{plan ? 'Nothing open in the next 30 days.' : 'No order cards until the plan is confirmed.'}</div>}
      </div>

      <div className="card">
        <div className="card-title">Messages and contact attempts</div>
        <div className="v4-scroll">
          <table className="v4-table">
            <thead><tr><th>Date</th><th>Channel</th><th>To / from</th><th>What</th><th>Outcome</th></tr></thead>
            <tbody>
              {[...thread].reverse().map((m) => (
                <tr key={`${m.direction}-${m.id}`}>
                  <td>{fmt(m.day)}</td>
                  <td>{m.direction === 'call' ? 'Call' : 'WhatsApp'}</td>
                  <td>{m.direction === 'out' ? `To ${m.party}` : m.direction === 'in' ? `From ${m.party}` : `${m.party} (by ${ui.name(m.actor)})`}</td>
                  <td>{m.direction === 'out' ? <>{m.template_id.replaceAll('_', ' ')}<div className="small-note">{m.body_en}</div></>
                    : m.direction === 'in' ? <>{m.button && <b>Tapped “{m.button.replaceAll('_', ' ')}” </b>}{m.kind === 'voice' && <b>Voice note ({m.clip_id}): </b>}{m.transcript || m.body}{m.translation && m.translation !== m.body && <div className="small-note">AI translation: {m.translation}</div>}{m.ai_flag && <div className="small-note">AI health flag: {m.ai_flag}{m.ai_failed ? ' (AI failed — safe path)' : ''}</div>}</> : m.body || '—'}</td>
                  <td>{m.direction === 'out'
                    ? <span className={`tag ${m.status === 'sent' ? 'verified' : 'unverified'}`}>{m.status === 'sent' ? 'Sent' : m.status.replaceAll('_', ' ')}{m.blocked_reason ? `: ${m.blocked_reason}` : ''}</span>
                    : m.direction === 'in' ? <span className="tag verified">Reply</span> : <span className="tag">{CALL_OUTCOME[m.status] || m.status}</span>}</td>
                </tr>
              ))}
              {!thread.length && <tr><td colSpan={5} className="small-note">No contact attempts.</td></tr>}
            </tbody>
          </table>
        </div>
        <div className="small-note" style={{ marginTop: 6 }}>Button replies ("Booked") are logged automatically. Free text always goes to a person.</div>
      </div>

      {cards.length > 0 && (
        <details className="card">
          <summary className="card-title" style={{ cursor: 'pointer', margin: 0 }}>Full plan vs actual ({cards.length} items)</summary>
          <div className="card-desc" style={{ marginTop: 8 }}>Breast cancer survivorship follow-up (BC-SURV-1, mock), confirmed by {ui.name(plan?.confirmed_by)}.</div>
          <div className="v4-scroll">
            <table className="v4-table">
              <thead><tr><th>Item</th><th>Due</th><th>Status</th><th>Owner</th></tr></thead>
              <tbody>{cards.map((c) => (
                <tr key={c.card_id}><td>{c.item_code} — {c.name}</td><td>{fmt(c.due_date)}</td>
                  <td><span className={`tag ${c.state === 'Due' ? '' : 'hosp'}`}>{c.state}</span></td><td>{c.owner_name}</td></tr>
              ))}</tbody>
            </table>
          </div>
        </details>
      )}
      <div className="btn-row"><button type="button" className="btn" onClick={() => ui.nav('audit', mrn)}>Open this patient's audit trail</button></div>
    </>
  );
}
