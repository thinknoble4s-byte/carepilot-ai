// Pop-ups from prototype v7.2: handover review, log a call, pause, messages (WhatsApp, simulated),
// the doctor's instruction in the hospital record, and the morning department lists.
import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { fmt, useUI } from './ui.jsx';

function useAct() {
  const { toast, refresh, closeModal } = useUI();
  const [error, setError] = useState('');
  const act = async (fn, done, keepOpen) => {
    setError('');
    try {
      const r = await fn();
      if (done) toast(typeof done === 'function' ? done(r) : done);
      if (!keepOpen) closeModal();
      refresh();
      return r;
    } catch (e) {
      setError(e.message);
      return null;
    }
  };
  return [act, error];
}

const addDays = (iso, n) => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
};

export function openHandover(ui, patient) {
  ui.showModal(`Agent handover — ${patient.full_name}`,
    'The agent stopped and handed this to you. It did not respond to the content.',
    <HandoverReview patient={patient} />);
}

function HandoverReview({ patient }) {
  const ui = useUI();
  const [h, setH] = useState(null);
  const [closing, setClosing] = useState(false);
  const [reason, setReason] = useState('');
  const [act, error] = useAct();
  useEffect(() => {
    api(`handovers/${patient.handover_id}`).then(setH).catch(() => {});
  }, [patient.handover_id]);
  if (!h) return <div className="small-note">Loading…</div>;
  const id = h.handover_id;
  const gate1 = (choice, done) => act(() => post(`handovers/${id}/gate1`, { choice }), done, true);
  return (
    <>
      <div className="msgprev" style={{ maxWidth: 'none' }}>
        <span>{h.patient_words || '—'}</span>
        <span className="small-note">{h.translation ? `English: ${h.translation}` : ''} · {h.source}</span>
      </div>
      <div className="small-note" style={{ marginTop: 8, lineHeight: 1.7 }}>
        What the agent did: {h.trigger === 'manual' ? 'nothing — a person opened this handover' :
          'logged any button reply, sent the fixed acknowledgement, stopped all automated messages and handed the conversation to you'}.
      </div>
      {h.stage === 'awaiting_coordinator' ? (
        <>
          <div className="btn-row">
            <button type="button" className="btn primary"
              onClick={async () => (await gate1('send_to_doctor', 'Sent to the doctor as a concern')) && ui.closeModal()}>
              Send to the doctor as a concern
            </button>
            <button type="button" className="btn"
              onClick={async () => (await gate1('call_first', 'Call the patient first')) && openCall(ui, patient)}>
              Call the patient first
            </button>
            <button type="button" className="btn ghost" onClick={() => setClosing(true)}>Nothing clinical — close</button>
          </div>
          {closing && (
            <>
              <label className="v3-field" htmlFor="hcR">Reason for closing (your name is recorded)</label>
              <input id="hcR" className="v3-in" value={reason} onChange={(e) => setReason(e.target.value)}
                placeholder="e.g. Travel plans only; nothing clinical" />
              <div className="btn-row">
                <button type="button" className="btn primary" disabled={!reason.trim()}
                  onClick={async () => {
                    if (await gate1('nothing_clinical')) {
                      await act(() => post(`handovers/${id}/close`, { reason }), 'Handover closed — follow-up resumes');
                    }
                  }}>Close the handover</button>
              </div>
            </>
          )}
        </>
      ) : (
        <div className="small-note" style={{ marginTop: 10 }}>Stage: {h.stage.replaceAll('_', ' ')}. Continue on the patient's page.</div>
      )}
      {error && <div className="error">{error}</div>}
    </>
  );
}

const OUTCOMES = [
  ['noans', 'No answer'], ['book', 'Spoke — will come; rebook the missed or due items'],
  ['concern', 'Spoke — reported a concern'], ['away', 'Spoke — away or unavailable until a date'],
  ['ok', 'Spoke — all well, nothing to do'], ['wrong', 'Wrong or unreachable number'],
];

export function openCall(ui, patient) {
  ui.showModal(`Log a call — ${patient.full_name}`, 'Pick the outcome; CarePilot starts the next step. Logged with your name.',
    <CallForm patient={patient} />);
}

function CallForm({ patient }) {
  const ui = useUI();
  const [who, setWho] = useState('patient');
  const [outcome, setOutcome] = useState('noans');
  const [note, setNote] = useState('');
  const [act, error] = useAct();
  const save = async () => {
    const r = await act(() => post(`patients/${patient.mrn}/calls`, { who, outcome, note }),
      outcome === 'concern' ? 'Call logged — handover opened for the concern' : 'Call logged', outcome === 'away');
    if (r && outcome === 'away') openPause(ui, patient, 'patient, by phone');
  };
  return (
    <>
      <label className="v3-field" htmlFor="clW">Who did you call?</label>
      <select id="clW" className="v3-in" value={who} onChange={(e) => setWho(e.target.value)}>
        <option value="patient">Patient{patient.phone ? ` — ${patient.phone}` : ''}</option>
        {patient.caregiver_name && <option value="caregiver">Caregiver — {patient.caregiver_name}</option>}
      </select>
      <label className="v3-field">Outcome</label>
      <div className="outs">
        {OUTCOMES.map(([id, label]) => (
          <label key={id} className="v4-check">
            <input type="radio" name="clO" checked={outcome === id} onChange={() => setOutcome(id)} /> {label}
          </label>
        ))}
      </div>
      <label className="v3-field" htmlFor="clN">{outcome === 'concern' ? "What the patient said (required)" : 'Note'}</label>
      <input id="clN" className="v3-in" value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional" />
      <div className="btn-row">
        <button type="button" className="btn primary" onClick={save}>Save</button>
        <button type="button" className="btn ghost" onClick={ui.closeModal}>Cancel</button>
      </div>
      {error && <div className="error">{error}</div>}
    </>
  );
}

export function openPause(ui, patient, via) {
  ui.showModal(`Pause follow-up — ${patient.full_name}`,
    'Human override: stops reminders and suspends gap rules for this patient until the date you set. A reason is required and logged.',
    <PauseForm patient={patient} via={via} />);
}

function PauseForm({ patient, via }) {
  const ui = useUI();
  const [until, setUntil] = useState(addDays(ui.today, 21));
  const [reason, setReason] = useState('');
  const [act, error] = useAct();
  return (
    <>
      <label className="v3-field" htmlFor="pzU">Pause until</label>
      <input type="date" id="pzU" className="v3-in" value={until} onChange={(e) => setUntil(e.target.value)} />
      <label className="v3-field" htmlFor="pzR">Reason</label>
      <input id="pzR" className="v3-in" value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder={`e.g. Away in native place until mid-month (${via || 'caregiver, by phone'})`} />
      <div className="btn-row">
        <button type="button" className="btn primary"
          onClick={() => act(() => post(`patients/${patient.mrn}/pause`, { until, reason }), `Paused until ${fmt(until)}`)}>
          Pause
        </button>
        <button type="button" className="btn ghost" onClick={ui.closeModal}>Cancel</button>
      </div>
      {error && <div className="error">{error}</div>}
    </>
  );
}

export function openMessages(ui, patient) {
  ui.showModal(`Messages — ${patient.full_name}`,
    'Approved templates in the patient\'s language. Button replies are logged automatically; free text always goes to a person.',
    <Messages patient={patient} />, true);
}

function Messages({ patient }) {
  const ui = useUI();
  const [thread, setThread] = useState([]);
  const [english, setEnglish] = useState(true);
  const [text, setText] = useState('');
  const [settings, setSettings] = useState(null);
  const [act, error] = useAct();
  const load = useCallback(() => {
    api(`patients/${patient.mrn}/messages`).then(setThread).catch(() => {});
  }, [patient.mrn]);
  useEffect(() => {
    api('care-plan').then((c) => setSettings(c.settings)).catch(() => {});
  }, []);
  const actOnReplies = settings?.act_on_replies !== false;
  useEffect(() => load(), [load]);
  const reply = async (body) => {
    if (await act(() => post(`simulator/${patient.mrn}/reply`, body), (r) => `Reply handled: ${r.handling.join('; ')}`, true)) {
      setText('');
      load();
    }
  };
  const withButtons = thread.filter((m) => m.direction === 'out' && m.status === 'sent' && m.buttons?.length);
  const [target, setTarget] = useState(null);
  const lastSent = withButtons.find((m) => m.id === target)
    || [...withButtons].reverse().find((m) => m.party === 'patient') || withButtons[withButtons.length - 1];
  const coordinator = ui.role === 'coordinator';
  return (
    <div className="grid-2">
      <div>
        <div className="v31-langrow">
          <span className="small-note">Language: <b style={{ color: 'var(--ink)' }}>{patient.preferred_language || patient.language}</b></span>
          <div className="seg">
            <button type="button" className={!english ? 'on' : ''} onClick={() => setEnglish(false)}>Patient's language</button>
            <button type="button" className={english ? 'on' : ''} onClick={() => setEnglish(true)}>+ English</button>
          </div>
        </div>
        <div className="v31-phone">
          <div className="v31-ph-head"><b>{settings?.clinic_name || 'Hospital A'}</b><span>WhatsApp · to {patient.full_name} · simulated</span></div>
          <div className="v31-chat">
            {thread.length === 0 && <div className="v31-empty">No messages yet. Messages go out only from approved templates.</div>}
            {thread.map((m) => (
              <div key={`${m.direction}-${m.id}`} className={`v31-b ${m.direction === 'out' ? 'hosp' : 'fam'}`}
                style={m.status && m.status !== 'sent' ? { opacity: 0.6, borderStyle: 'dashed' } : undefined}>
                {m.direction === 'in' && m.button && <span className="v31-en"><b>Tapped: {m.button.replaceAll('_', ' ')}</b></span>}
                {m.body && <span className="v31-kn">{m.body}</span>}
                {english && m.body_en && m.body_en !== m.body && <span className="v31-en">{m.body_en}</span>}
                {m.direction === 'out' && m.buttons?.length > 0 && (
                  <div className="rbtns">{m.buttons.map((b) => (coordinator && m.status === 'sent'
                    ? <button key={b.id} type="button" className="rbtn" title={`Demo: the patient taps "${b.label_en}"`}
                      style={{ cursor: 'pointer', font: 'inherit' }}
                      onClick={() => reply({ button: b.id, in_reply_to: m.id })}>{b.label}</button>
                    : <span key={b.id} className="rbtn">{b.label}</span>))}</div>
                )}
                <span className="v31-t">
                  {fmt(m.day)} · {m.direction === 'out' ? `to ${m.party}` : `from ${m.party}`}
                  {m.status && m.status !== 'sent' ? ` · ${m.status.replaceAll('_', ' ')}${m.blocked_reason ? `: ${m.blocked_reason}` : ''}` : ''}
                </span>
              </div>
            ))}
          </div>
        </div>
        {thread.some((m) => m.language && m.language !== 'English') && (
          <div className="small-note" style={{ marginTop: 8 }}>Kannada and Hindi wording are drafts — to be checked by a native speaker.</div>
        )}
      </div>
      <div>
        {coordinator ? (
          <>
            <div className="card-title">Demo: reply as the patient</div>
            <div className="small-note">
              {actOnReplies
                ? 'Tap a button on any message in the phone, or reply to one here. Buttons update the workflow directly, with no AI. Free text has no classifier until Sarvam (Increment 4), so it always opens a handover — the safe path.'
                : 'Use Case 1: a reply (button, text or HELP) is saved in the message history and CarePilot takes no action. Reply handling arrives with Use Case 2.'}
            </div>
            {lastSent && (
              <>
                <label className="v3-field" htmlFor="msgTo">Replying to</label>
                <select id="msgTo" className="v3-in" value={lastSent.id} onChange={(e) => setTarget(Number(e.target.value))}>
                  {withButtons.map((m) => <option key={m.id} value={m.id}>{fmt(m.day)} · to {m.party} · {m.body_en.slice(0, 70)}…</option>)}
                </select>
              </>
            )}
            {lastSent ? (
              <div className="btn-row">
                {lastSent.buttons.map((b) => (
                  <button key={b.id} type="button" className="btn sm" title={b.label_en}
                    onClick={() => reply({ button: b.id, in_reply_to: lastSent.id })}>Tap “{b.label_en}”</button>
                ))}
              </div>
            ) : <div className="small-note" style={{ marginTop: 8 }}>No message with reply buttons yet.</div>}
            <label className="v3-field" htmlFor="msgT">Free-text reply</label>
            <input id="msgT" className="v3-in" value={text} onChange={(e) => setText(e.target.value)}
              placeholder="e.g. मैं शनिवार को आऊँगी। लेकिन ऑपरेशन वाली जगह पर दर्द है।" />
            <div className="btn-row">
              <button type="button" className="btn primary" disabled={!text.trim()}
                onClick={() => reply({ text, in_reply_to: lastSent?.id })}>Send as the patient</button>
              <button type="button" className="btn" disabled={!text.trim() || !lastSent}
                onClick={() => reply({ button: 'booked', text, in_reply_to: lastSent.id })}>Tap “Booked” + send text</button>
              <button type="button" className="btn ghost" onClick={() => reply({ text: 'HELP' })}>Reply HELP</button>
            </div>
          </>
        ) : <div className="small-note">Read only.</div>}
        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}

const DEPARTMENTS = [['Oncology OPD', 'visit', 'Priya Nair'], ['Laboratory', 'test', 'Sunil Kumar'], ['Radiology', 'test', 'Lakshmi Rao']];

export function openInstruction(ui, item) {
  ui.showModal('Demo: instruction appears in the EMR',
    'In the pilot this is picked up from the hospital record. The demo has no EMR, so enter what the doctor wrote there.',
    <InstructionForm item={item} />);
}

function InstructionForm({ item }) {
  const ui = useUI();
  const [dept, setDept] = useState('Oncology OPD');
  const [text, setText] = useState('Surgical-site review within 7 days');
  const [due, setDue] = useState(addDays(ui.today, 7));
  const [withCard, setWithCard] = useState(true);
  const [act, error] = useAct();
  const within = Math.round((new Date(`${due}T00:00:00`) - new Date(`${ui.today}T00:00:00`)) / 864e5);
  const type = DEPARTMENTS.find((d) => d[0] === dept)[1];
  return (
    <>
      <label className="v3-field" htmlFor="inT">Instruction</label>
      <input id="inT" className="v3-in" value={text} onChange={(e) => setText(e.target.value)} />
      <label className="v4-check" style={{ marginTop: 10 }}>
        <input type="checkbox" checked={withCard} onChange={(e) => setWithCard(e.target.checked)} /> Creates an order card
      </label>
      {withCard && (
        <>
          <label className="v3-field" htmlFor="inD">Department</label>
          <select id="inD" className="v3-in" value={dept} onChange={(e) => setDept(e.target.value)}>
            {DEPARTMENTS.map(([d, , owner]) => <option key={d} value={d}>{d} — owner {owner}</option>)}
          </select>
          <label className="v3-field" htmlFor="inDue">Due</label>
          <input type="date" id="inDue" className="v3-in" value={due} onChange={(e) => setDue(e.target.value)} />
        </>
      )}
      <div className="btn-row">
        <button type="button" className="btn primary" disabled={!text.trim() || (withCard && within < 1)}
          onClick={() => act(() => post(`handovers/${item.handover_id}/instruction`, {
            text, order: withCard ? { name: text, department: dept, card_type: type, within_days: within } : null,
          }), withCard ? 'Instruction picked up — order card created and the patient told' : 'Instruction picked up')}>
          Pick up instruction
        </button>
        <button type="button" className="btn ghost" onClick={ui.closeModal}>Cancel</button>
      </div>
      {error && <div className="error">{error}</div>}
    </>
  );
}

export function openDeptLists(ui) {
  ui.showModal('Morning department lists',
    'Everything open in the next 7 days, by department, with the named owner and cover. Sent to each department every morning (live in the autonomy policy).',
    <DeptLists />, true);
}

function DeptLists() {
  const [lists, setLists] = useState(null);
  useEffect(() => {
    api('department-lists').then(setLists).catch(() => setLists([]));
  }, []);
  if (!lists) return <div className="small-note">Loading…</div>;
  if (!lists.length) return <div className="empty">Nothing due in the next 7 days.</div>;
  return lists.map((d) => (
    <div key={d.department} className="card-flat" style={{ marginBottom: 10 }}>
      <div className="card-title">{d.department} <span className="small-note">· owner {d.owner} · cover {d.cover}</span></div>
      <table className="v4-table">
        <thead><tr><th>Patient</th><th>Item</th><th>Due</th><th>State</th></tr></thead>
        <tbody>
          {d.items.map((i) => (
            <tr key={`${i.mrn}-${i.item_code}`}><td>{i.full_name} <span className="small-note">{i.mrn}</span></td>
              <td>{i.name}</td><td>{fmt(i.due_date)}</td><td>{i.state}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  ));
}
