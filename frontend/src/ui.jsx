// Shared building blocks that mirror the prototype (v7.2): screen heading, pop-up, notification, dates.
import React, { createContext, useContext, useEffect, useState } from 'react';
import { blobUrl } from './api.js';

export const UI = createContext({
  toast: () => {}, showModal: () => {}, closeModal: () => {}, refresh: () => {}, nav: () => {}, today: null, role: null, user: null,
  name: (id) => id,
});
export const useUI = () => useContext(UI);

// The prototype's fmt(): "22 Sept 2026".
export function fmt(iso) {
  if (!iso) return '—';
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function ScreenHead({ eyebrow, title, sub }) {
  return (
    <div className="screen-head">
      <div>
        <div className="screen-eyebrow">{eyebrow}</div>
        <h1 className="screen-title">{title}</h1>
        {sub && <div className="screen-sub">{sub}</div>}
      </div>
    </div>
  );
}

export const GUARD = (
  <div className="v4-guard">
    CarePilot organises, reminds, tracks and routes. It does not diagnose, prescribe, score clinical risk, interpret
    results or advise patients. Every AI suggestion is confirmed by a named person, and every step is logged.
  </div>
);

export function Modal({ modal, onClose }) {
  if (!modal) return null;
  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wrap" role="dialog" aria-modal="true" style={modal.wide ? { maxWidth: 920 } : undefined}>
        <button type="button" className="modal-close" aria-label="Close" onClick={onClose}>&times;</button>
        <h3>{modal.title}</h3>
        {modal.sub && <div className="modal-sub">{modal.sub}</div>}
        {modal.body}
      </div>
    </div>
  );
}

export function Scope() {
  return (
    <div className="grid-2">
      <div className="card-flat">
        <div className="card-title">It does</div>
        <div className="small-note" style={{ lineHeight: 1.8 }}>
          Tracks the follow-up plan as order cards with named owners · checks for gaps every morning, including before
          anything is missed · sends approved messages to patients and caregivers in their language · logs button
          replies and structures free text for a person to confirm · holds outside documents unverified until a person
          checks · asks about care elsewhere · routes concerns to the doctor with a deadline · picks up instructions
          from the hospital record or a paper note · runs repetitive coordination through an AI agent within an
          autonomy policy, stopping and handing over the moment a patient mentions a health matter · logs every step
        </div>
      </div>
      <div className="card-flat">
        <div className="card-title">It does not</div>
        <div className="small-note" style={{ lineHeight: 1.8 }}>
          Diagnose · prescribe · recommend treatment · score clinical risk · interpret results or symptoms · advise
          patients · resolve differences between sources on its own · ask doctors to enter orders into another system
        </div>
      </div>
    </div>
  );
}

export function Placeholder({ what, when }) {
  return (
    <div className="card">
      <div className="empty">
        {what} arrives in {when}. Nothing here is mocked: this screen fills from real records once that step is built.
      </div>
    </div>
  );
}

// Worklist statuses (prototype STATUS): [pill colour, label].
export const STATUS = {
  action: ['bad', 'Coordinator action required'], noresp: ['warn', 'No response'], overdue: ['warn', 'Overdue'],
  verify: ['ai', 'Awaiting verification'], approval: ['ai', 'Approval waiting'], agent: ['hybrid', 'Agent handling'],
  duesoon: ['brand', 'Due soon'], watch: ['hybrid', 'Early warning'], ontrack: ['good', 'On track'],
  paused: ['gray', 'Paused'], closed: ['gray', 'Closed'],
};

export function Chip({ k }) {
  const s = STATUS[k] || ['gray', k];
  return <span className={`pill ${s[0]}`}><span className="dot" />{s[1]}</span>;
}

export function Pill({ tone, children }) {
  return <span className={`pill ${tone}`}><span className="dot" />{children}</span>;
}

const days = (a, b) => Math.round((new Date(`${a}T00:00:00`) - new Date(`${b}T00:00:00`)) / 864e5);

// The prototype's deadline label: "⏱ Due by 10 Nov 2026" / "Due today" / "Overdue since …".
export function Dl({ due, label, today }) {
  if (!due) return null;
  const d = days(String(due).slice(0, 10), String(today || due).slice(0, 10));
  const cls = d < 0 ? 'bad' : d === 0 ? 'warn' : '';
  const text = label
    ? `${label}${d < 0 ? ` overdue since ${fmt(due)}` : d === 0 ? ' due today' : ` due by ${fmt(due)}`}`
    : d < 0 ? `Overdue since ${fmt(due)}` : d === 0 ? 'Due today' : `Due by ${fmt(due)}`;
  return <span className={`dl ${cls}`}>⏱ {text}</span>;
}

const MARKS = {
  health_mention: ['Health mention', 'unverified'], voice: ['🎙 Voice note', 'hosp'], ai_failed: ['AI could not read it', 'unverified'],
  urgent: ['Urgent', 'live'],
  task_help_booking: ['Help booking', 'hosp'], task_cant_come: ["Can't come", 'hosp'], task_help_request: ['Help request', 'hosp'],
  overdue: ['Overdue', 'unverified'],
};

// The patient's reply buttons, as the patient sees them (English labels of the approved template).
export const BUTTON_LABEL = { booked: 'Booked', help_book: 'Please help me book', cant_come: "I can't come" };
export const buttonLabel = (id) => BUTTON_LABEL[id] || id.replaceAll('_', ' ');

export function Marks({ marks }) {
  return (marks || []).map((m) => <React.Fragment key={m}> <span className={`tag ${MARKS[m]?.[1] || ''}`}>{MARKS[m]?.[0] || m}</span></React.Fragment>);
}

// A stored voice note (UC2a): played from the database through the API.
export function VoiceClip({ inboundId }) {
  const [src, setSrc] = useState(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    let url;
    blobUrl(`inbound/${inboundId}/audio`).then((u) => { url = u; setSrc(u); }).catch((e) => setErr(e.message));
    return () => { if (url) URL.revokeObjectURL(url); };
  }, [inboundId]);
  if (err) return <span className="small-note">Audio not available ({err})</span>;
  if (!src) return <span className="small-note">Loading audio…</span>;
  return <audio controls preload="metadata" src={src} style={{ width: '100%', maxWidth: 300, height: 34 }} />;
}

export function Reason({ r, today }) {
  return (
    <div className={r.tag === 'watch' ? 'early' : r.tag === 'agent' ? 'byagent' : ''}>
      {r.tag === 'agent' && <><span className="v4-k agent mini">Agent</span> </>}
      {r.txt}<Marks marks={r.marks} />
      {r.due && <> <Dl due={r.due} today={today} /></>}
    </div>
  );
}

export function patientStatusPill(status, pauseUntil) {
  if (status === 'Paused') return <Pill tone="gray">Paused{pauseUntil ? ` until ${fmt(pauseUntil)}` : ''}</Pill>;
  if (status === 'Agent off') return <Pill tone="hybrid">On follow-up · agent off</Pill>;
  if (status === 'Lost to follow-up') return <Pill tone="bad">Lost to follow-up</Pill>;
  if (status === 'Followed up elsewhere') return <Pill tone="gray">Followed up elsewhere</Pill>;
  return <Pill tone="brand">On follow-up</Pill>;
}

// Plain-language audit lines (the ledger stores codes).
const ACTION_TEXT = {
  demo_reset: 'Demo reset to the starting state', register_imported: 'Imported the follow-up register',
  appointments_imported: 'Imported the appointment list', exception_dismissed: 'Dismissed an import exception',
  consent_recorded: 'Recorded consent', plan_confirmed: 'Confirmed the follow-up plan',
  cards_generated: 'Created order cards from the plan', clock_advanced: 'Advanced the demo date',
  card_state_changed: 'Moved an order card', gap_detected: 'Morning gap check raised a gap',
  agent_decision: 'Agent decision', message_sent: 'Sent an approved message', message_blocked: 'Send-guard blocked a message',
  message_drafted: 'Drafted a message for approval', message_approved: 'Approved a drafted message',
  message_rejected: 'Rejected a drafted message', reply_received: 'Reply received on WhatsApp',
  handover_opened: 'Handover opened; automated messages stopped', handover_reply_added: 'Reply added to the open handover',
  gate1_decided: 'Gate 1 decision', gate2_decided: "Doctor's decision (Gate 2)", instruction_recorded: "Doctor's instruction (Gate 3)",
  patient_called: 'Call recorded on the handover', handover_closed: 'Handover closed', task_created: 'Task created',
  task_done: 'Task done', task_escalated: 'Deadline missed: escalated to cover', task_reminder_sent: 'Deadline missed: reminder sent',
  patient_status_changed: 'Follow-up status changed', pause_ended: 'Pause ended; follow-up resumed', call_logged: 'Call logged',
  'session.user_selected': 'Switched user', 'config.activated': 'Configuration activated',
  card_created: 'Order card created from the plan', gap_check_run: '07:00 gap check run', reminder_run: '07:30 reminder run',
  duplicate_suppressed: 'Duplicate suppressed (already on file)', clock_set: 'Set the demo date',
  policy_mode_changed: 'Agent task mode changed',
};

export function describe(e) {
  const d = e.details || {};
  const extra = [
    e.rule && `rule ${e.rule}`, e.reason_code && `reason ${e.reason_code}`, e.outcome && `outcome ${e.outcome}`,
    d.task_type && d.task_type.replaceAll('_', ' '), d.from && d.to && `${d.from} → ${d.to}`, d.reason && `"${d.reason}"`,
    d.note && `"${d.note}"`, d.text && `"${d.text}"`, d.button && `button ${d.button}`,
  ].filter(Boolean);
  return `${ACTION_TEXT[e.action] || e.action}${extra.length ? ` — ${extra.join(' · ')}` : ''}`;
}

export const KIND = { agent: 'agent', person: 'human', system: 'system', ai: 'ai', ai_suggestion: 'ai' };
export const KIND_LABEL = { agent: 'Agent', human: 'Human', system: 'System', ai: 'AI suggestion' };

// A card's reminder gap: open, reminder sent, blocked (with the reason code), resolved.
export function GapState({ status, reason, rule }) {
  if (!status) return <span className="small-note">—</span>;
  const tone = { open: 'brand', reminder_sent: 'good', blocked: 'bad', resolved: 'gray' }[status] || 'gray';
  const label = { open: 'open', reminder_sent: 'reminder sent', blocked: 'blocked', resolved: 'resolved' }[status] || status;
  return <Pill tone={tone}>{rule} {label}{reason ? ` — ${reason}` : ''}</Pill>;
}
