// R6 — Care Plan, read only (prototype v7.2 scrRules), rendered from the signed-off config.
import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { fmt, Scope, ScreenHead, useUI } from './ui.jsx';

const TABS = [['plan', 'Plan'], ['rules', 'Gap rules & deadlines'], ['agent', 'Agent autonomy'], ['msgs', 'Messages'], ['scope', 'What CarePilot is']];
const LIFE = { visit: 'Due → booked → attended', specimen: 'Due → scheduled → completed → report received → report verified (doctor)',
  imaging: 'Due → scheduled → completed → verified (doctor)' };
const life = (i) => (i.type === 'visit' ? LIFE.visit : i.specimen === false ? LIFE.imaging : LIFE.specimen);
const GAP_RULES = [
  ['G0', 'Not booked by T-7', 'A visit or test has no booking 7 days before its due date → booking reminder with reply buttons (Booked / Please help me book / I can\'t come) to patient and caregiver. Still not booked at T-3 → task for the coordinator, 2 working days.', true],
  ['G1', 'Missed plan item', 'An item that was never booked is not attended by its due date + 2 days → item overdue; approved reminder sent in the patient\'s language.', true],
  ['G2', 'Booked but not attended', 'A booked date passes with no attendance on the next appointment list → task for the coordinator to confirm with the patient.', false],
  ['G3', 'Refill not collected', 'Hormonal therapy not refilled within 3 days of the supply running out → reminder sent; added to the pharmacy\'s morning list.', false],
  ['G4', 'Not responding', '2 messages in a row unanswered (a message counts as unanswered after 24 hours) → escalated to the named caller; call due within 2 working days.', false],
  ['G5', 'Evidence missing', 'A test completed (or the patient says done) but no report within 7 days → first chase sent automatically; second chase goes to the coordinator.', false],
  ['G6', 'Beyond expected return window', 'No visit attended for 21+ days after a missed item → "Since your last visit, did you receive care anywhere else?" queued.', false],
];
const HANDOFFS = [
  ['Concern review (Gate 1)', 'Agent → coordinator', 'coordinator_review'], ['Concern decision (Gate 2)', 'Coordinator → doctor', 'doctor_decision'],
  ['Instruction in the record (Gate 3)', 'Doctor → hospital record', 'doctor_instruction'], ['Urgent concern call', 'Doctor → coordinator', 'urgent_call'],
  ['Booking from an instruction', 'Order card → department owner', 'booking_from_instruction'], ['Help the patient book (T-3)', 'Agent → coordinator', 'booking_help'],
  ['Contact when the agent cannot', 'Agent → coordinator', 'contact_needed'],
];
const TASK_LABEL = { booking_reminder: 'Booking reminders (T-7, rule G0)', missed_item_reminder: 'Missed-item reminders (rule G1)', log_button_reply: 'Logging button replies ("Booked", "Help me book", "I can\'t come")' };

const Built = ({ yes }) => (yes ? <span className="tag verified">Live</span> : <span className="tag">Not built yet</span>);

export default function CarePlan() {
  const ui = useUI();
  const [cfg, setCfg] = useState(null);
  const [tab, setTab] = useState('plan');
  const [pick, setPick] = useState(0);
  const load = useCallback(() => { api('care-plan').then(setCfg).catch(() => {}); }, []);
  useEffect(() => load(), [load]);
  if (!cfg) return <div className="card"><div className="empty">Loading…</div></div>;
  const tmpl = cfg.plan_templates[pick] || cfg.plan_templates[0];
  const t = tmpl?.body;
  const auto = cfg.autonomy_policy[0];
  const dl = cfg.deadline_policy[0]?.body;
  const setMode = async (task, mode) => {
    try {
      const r = await post(`care-plan/autonomy/${task}`, { mode });
      ui.toast(`${TASK_LABEL[task]} → ${mode} (policy v${r.version})`);
      load();
    } catch (e) {
      ui.toast(e.message);
    }
  };
  const when = (i) => (t.due_date_entered ? 'Due date entered by the coordinator at confirmation'
    : `month ${i.months}${i.days ? ` ${i.days > 0 ? '+' : '−'} ${Math.abs(i.days)} days` : ''}`);
  const st = cfg.settings;
  const unit = (n) => (n === 0 ? 'Same day' : `${n} working day${n > 1 ? 's' : ''}`);

  let body;
  if (tab === 'plan') {
    body = (
      <>
        {cfg.plan_templates.length > 1 && (
          <div className="seg" style={{ marginBottom: 10 }}>
            {cfg.plan_templates.map((x, n) => (
              <button key={x.name} type="button" className={n === pick ? 'on' : ''} onClick={() => setPick(n)}>{x.body.short_name || x.body.template_id}</button>
            ))}
          </div>
        )}
        <div className="card">
          <div className="kv"><b>Template</b><div>{t.name} ({t.template_id} v{t.version})</div></div>
          <div className="kv"><b>Status</b><div><span className="tag unverified">{t.signed_off_by}</span> · effective {fmt(t.effective_from)}</div></div>
          <div className="kv"><b>Scope</b><div>Breast cancer survivorship only in this version. The coordinator chooses the plan for each patient. Due dates move from a weekend to the next Monday.</div></div>
          {t.note && <div className="kv"><b>Tests</b><div>{t.note}</div></div>}
        </div>
        <div className="card">
          <div className="card-title">Follow-up plan (from the end of active treatment)</div>
          <div className="v4-scroll"><table className="v4-table">
            <thead><tr><th>Item</th><th>When</th><th>Named owner</th><th>Order card lifecycle</th></tr></thead>
            <tbody>{t.items.map((i) => (
              <tr key={i.item_code}><td>{i.item_code} — {i.name}</td><td>{when(i)}</td>
                <td>{t.departments[i.department].owner}<div className="small-note">cover: {t.departments[i.department].cover} · {i.department}</div></td>
                <td>{life(i)}{i.type === 'test' && <div className="small-note">{i.specimen === false ? 'Non-specimen' : 'Specimen-based'}</div>}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
      </>
    );
  } else if (tab === 'rules') {
    body = (
      <>
        <div className="card">
          <div className="card-title">Gap rules (checked every morning)</div>
          <div className="v4-scroll"><table className="v4-table">
            <thead><tr><th /><th>Gap</th><th>Rule and action</th><th /></tr></thead>
            <tbody>{GAP_RULES.map(([id, name, rule, built]) => (
              <tr key={id}><td><b>{id}</b></td><td>{name}</td><td>{rule}</td>
                <td>{built ? (st.gap_rules.includes(id) ? <span className="tag verified">Live</span> : <span className="tag">Off in this data set</span>) : <Built yes={false} />}</td></tr>
            ))}</tbody>
          </table></div>
          <div className="small-note" style={{ marginTop: 6 }}>Thresholds from the signed-off config: T-{cfg.rule_thresholds[0]?.body.g0_booking_days_before_due} for G0, grace {cfg.rule_thresholds[0]?.body.visit_grace_days} days for G1.</div>
        </div>
        <div className="card">
          <div className="card-title">Handoff deadlines</div>
          <div className="v4-scroll"><table className="v4-table">
            <thead><tr><th>Handoff</th><th>From → to</th><th>Deadline</th><th>If missed</th></tr></thead>
            <tbody>{HANDOFFS.map(([name, flow, key]) => (
              <tr key={key}><td>{name}</td><td>{flow}</td><td>{unit(dl.working_days[key])}</td>
                <td>{dl.escalation[key] === 'cover' ? "Escalated to the owner's named cover" : 'Reminder to the owner'}</td></tr>
            ))}
              <tr><td>Patient notified of a new order card</td><td>Order card → patient</td><td>Automatic, same day</td><td>—</td></tr></tbody>
          </table></div>
          <div className="small-note" style={{ marginTop: 6 }}>Covers: {Object.entries(dl.covers).map(([a, b]) => `${a} → ${b}`).join(' · ')}</div>
        </div>
        <div className="card">
          <div className="card-title">Worklist statuses (operational, not clinical)</div>
          <div className="v4-table" style={{ fontSize: 12.5, lineHeight: 1.8 }}>
            <div><b>Coordinator action required</b> — something to do now, often with a deadline.</div>
            <div><b>No response</b> — 2 or more messages in a row unanswered.</div>
            <div><b>Overdue</b> — an item past due and never booked.</div>
            <div><b>Awaiting verification</b> — a plan, decision or instruction pending.</div>
            <div><b>Due soon</b> — an item in the next 14 days.</div>
            <div><b>Early warning</b> — facts that often come before a miss (last message unanswered, agent off). Listed as facts, never added up into a score.</div>
            <div><b>Paused</b> — follow-up paused by a person with a reason and an end date.</div>
          </div>
        </div>
      </>
    );
  } else if (tab === 'agent') {
    body = (
      <>
        <div className="card agent-card">
          <div className="card-title">How the work is shared</div>
          <div className="card-desc">The CarePilot agent runs repetitive coordination continuously. Coordinators handle exceptions, escalations and conversations that need a person. The agent acts only through the same logged actions a coordinator would use; every action can be reviewed, and the agent can be switched off for any patient.</div>
          <div className="v4-flag" style={{ marginTop: 8 }}><div className="ft">Stop-and-hand-over rule</div>
            <div className="fb">The moment a patient or caregiver writes free text, mentions a health matter or replies HELP, the agent stops, sends a fixed acknowledgement, and hands the conversation to the coordinator. It never discusses health matters, and nothing more is sent until a person closes the handover.</div></div>
          <div className="small-note" style={{ marginTop: 8 }}>Agent messages are signed as the clinic's automated assistant. Patients can always reach a person by replying "HELP" (or ಸಹಾಯ / मदद). Automated messaging is covered in the consent record. Policy v{auto.version}, {auto.signed_off_by}.</div>
        </div>
        <div className="card">
          <div className="card-title">Agent does it, logged</div>
          <div className="v4-scroll"><table className="v4-table">
            <thead><tr><th>Task</th><th>Mode</th><th /></tr></thead>
            <tbody>{Object.entries(auto.body.tasks).map(([id, task]) => (
              <tr key={id}><td>{TASK_LABEL[id] || id}</td>
                <td>{task.mode === 'live' ? <span className="tag verified">Live</span> : <span className="tag unverified">Shadow — proposes, you approve</span>}</td>
                <td>{ui.role === 'coordinator' && id !== 'log_button_reply' && (
                  <button type="button" className="btn sm ghost" onClick={() => setMode(id, task.mode === 'live' ? 'shadow' : 'live')}>
                    {task.mode === 'live' ? 'Back to shadow' : 'Promote to live'} (demo)</button>)}</td></tr>
            ))}</tbody>
          </table></div>
        </div>
        <div className="card">
          <div className="card-title">Switches for this demo data set ({st.scenario === 'uc1' ? 'Use Case 1' : 'Golden Path'})</div>
          <div className="card-desc">{st.note}</div>
          <table className="v4-table"><tbody>
            <tr><td>Gap rules that run</td><td>{st.gap_rules.join(', ')}</td></tr>
            <tr><td>Cards that get reminders</td><td>{st.reminder_cards === 'all' ? 'Visit and test cards, including cards from a doctor\'s instruction' : 'Visit cards from the plan only'}</td></tr>
            <tr><td>Copy to the caregiver</td><td>{st.caregiver_copy ? 'On (when the caregiver has consented)' : 'Off — one message per visit, to the patient'}</td></tr>
            <tr><td>Act on replies</td><td>{st.act_on_replies ? 'On — buttons update cards; free text and HELP open a handover' : 'Off — replies are saved in the message history, no action'}</td></tr>
            <tr><td>Tasks for people</td><td>{st.people_tasks ? 'On — T-3 booking help, contact needed, consent scope missing' : 'Off'}</td></tr>
          </tbody></table>
        </div>
        <div className="card"><div className="card-title">Agent prepares, coordinator approves</div>
          <div className="v4-table" style={{ lineHeight: 1.8 }}>
            <div>Reminders while a task is in shadow mode</div>
            <div>Structuring free-text and voice replies <span className="tag">Increment 4 (Sarvam)</span></div>
          </div></div>
        <div className="card"><div className="card-title">Coordinator only</div>
          <div className="v4-table" style={{ lineHeight: 1.8 }}>
            <div>Anything a patient says about symptoms, mood or distress</div><div>Phone calls with patients and caregivers</div>
            <div>Confirming the plan against two identifiers</div><div>Closing a handover or a case, with a reason</div><div>Exceptions to the rules</div>
          </div></div>
        <div className="card"><div className="card-title">Doctor only</div>
          <div className="v4-table" style={{ lineHeight: 1.8 }}>
            <div>Urgent or routine, for every concern</div><div>The instruction in the hospital record</div>
            <div>Setting a test to verified (report verified, or verified for imaging)</div>
            <div>Signing off plan templates and message wording (clinical lead)</div>
          </div></div>
      </>
    );
  } else if (tab === 'msgs') {
    body = cfg.message_templates.map((m) => (
      <div key={m.name} className="card">
        <div className="card-title">{m.body.template_id.replaceAll('_', ' ')} · {m.body.language}{' '}
          <span className="small-note">v{m.body.version} · {m.body.approved_by} · effective {fmt(m.body.effective_from)}</span></div>
        {m.body.note && <div className="card-desc">{m.body.note}</div>}
        <div className="v31-tpl" style={{ whiteSpace: 'pre-line' }}>{m.body.text}{m.body.closing ? `\n\n${m.body.closing}` : ''}</div>
        {m.body.buttons.length > 0 && <div className="rbtns">{m.body.buttons.map((b) => <span key={b.id} className="rbtn">{b.label}</span>)}</div>}
      </div>
    ));
  } else {
    body = <div className="card"><Scope /></div>;
  }

  return (
    <>
      <ScreenHead eyebrow="Configured by the clinical lead · read only here" title="Care Plan"
        sub="The breast cancer follow-up plan and how CarePilot runs it: what is tracked, when it acts, who owns each step, and what the agent may do." />
      <div className="seg" style={{ marginBottom: 14 }}>
        {TABS.map(([id, label]) => <button key={id} type="button" className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>)}
      </div>
      {body}
    </>
  );
}
