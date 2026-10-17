// Pop-ups from prototype v7.2: handover review, log a call, pause, messages (WhatsApp, simulated),
// the doctor's instruction in the hospital record, and the morning department lists.
import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { fmt, useUI, VoiceClip } from './ui.jsx';

export const CLOSE_REASONS = ['Nothing clinical', 'Travel or booking plans only', 'Patient called; matter resolved',
  'Doctor instruction done, patient messaged', 'Review booked; doctor\'s plan in place'];

// Close reasons from a pick-list, with "Other" for anything else (UC2a step 11).
export function CloseReason({ value, onChange, id }) {
  const [other, setOther] = useState(false);
  return (
    <>
      <select id={id} className="v3-in" value={other ? '__other' : value}
        onChange={(e) => { const v = e.target.value; setOther(v === '__other'); onChange(v === '__other' ? '' : v); }}>
        <option value="">Choose a reason…</option>
        {CLOSE_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
        <option value="__other">Other (write it)</option>
      </select>
      {other && <input className="v3-in" style={{ marginTop: 6 }} value={value} onChange={(e) => onChange(e.target.value)} placeholder="Write the reason" />}
    </>
  );
}

// What the patient said: original words, the AI translation (labelled), the audio, and an AI failure note.
export function PatientWords({ h, language }) {
  return (
    <div className="msgprev" style={{ maxWidth: 'none' }}>
      {h.inbound_kind === 'voice' && <span className="small-note">🎙 Voice note{language ? ` · profile language ${language}` : ''}</span>}
      <span>{h.patient_words || '—'}</span>
      {h.translation && <span className="small-note">English (AI translation, Sarvam — check before relying on it): {h.translation}</span>}
      {h.inbound_kind === 'voice' && h.inbound_id && <VoiceClip inboundId={h.inbound_id} />}
      {h.ai_failed && <span className="small-note"><b>The AI could not read this message</b> — listen to the audio. It was handed to you as a precaution.</span>}
      <span className="small-note">{h.source}</span>
    </div>
  );
}

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
      <PatientWords h={h} language={patient.preferred_language} />
      <div className="small-note" style={{ marginTop: 8, lineHeight: 1.7 }}>
        What the agent did: {h.trigger === 'manual' ? 'nothing — a person opened this handover' :
          h.trigger === 'health_matter' ? 'the AI flagged a possible health mention, so the agent sent the fixed acknowledgement, stopped all routine messages (send block on) and handed the conversation to you. It did not reply to the content'
            : 'logged any button reply, sent the fixed acknowledgement, stopped all automated messages and handed the conversation to you'}.
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
              <CloseReason id="hcR" value={reason} onChange={setReason} />
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
  const [clips, setClips] = useState([]);
  const [clip, setClip] = useState('');
  const [lastEvent, setLastEvent] = useState(null);
  const [act, error] = useAct();
  useEffect(() => {
    api('simulator/clips').then((list) => {
      setClips(list);
      const mineFirst = list.find((c) => c.patient === patient.mrn) || list[0];
      if (mineFirst) setClip(mineFirst.id);
    }).catch(() => {});
  }, [patient.mrn]);
  const load = useCallback(() => {
    api(`patients/${patient.mrn}/messages`).then(setThread).catch(() => {});
  }, [patient.mrn]);
  useEffect(() => {
    api('care-plan').then((c) => setSettings(c.settings)).catch(() => {});
  }, []);
  const actOnReplies = settings?.act_on_replies !== false;
  useEffect(() => load(), [load]);
  const reply = async (body) => {
    // Every simulated delivery carries a WhatsApp-style event id; resending the same id must change nothing (REQ-20).
    const sent = { event_id: `wa-${Date.now()}`, ...body };
    if (await act(() => post(`simulator/${patient.mrn}/reply`, sent), (r) => `Reply handled: ${r.handling.join('; ')}`, true)) {
      setLastEvent(sent);
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
                {m.kind === 'voice' && (
                  <>
                    <span className="v31-en"><b>🎙 Voice note ({m.clip_id})</b></span>
                    <VoiceClip inboundId={m.id} />
                    {m.transcript && <span className="v31-kn">{m.transcript}</span>}
                    {english && m.translation && <span className="v31-en">AI translation: {m.translation}</span>}
                  </>
                )}
                {m.direction === 'in' && m.kind === 'text' && english && m.translation && m.translation !== m.body
                  && <span className="v31-en">AI translation: {m.translation}</span>}
                {m.direction === 'in' && m.ai_flag && (
                  <span className="v31-en">{m.ai_flag === 'yes' ? `Health mention flagged${m.ai_failed ? ' (AI failed — safe path)' : ''} → handed to a person` : 'No health mention → proposal for the coordinator'}</span>
                )}
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
                  {fmt(m.day)}{m.at ? ` ${String(m.at).slice(0, 5)}` : ''} · {m.direction === 'out' ? `to ${m.party}` : `from ${m.party}`}
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
                ? 'Tap a button on any message in the phone, or reply to one here. Buttons update the workflow directly, with no AI. Text and voice notes go to the AI (Sarvam) for one yes/no question: does it mention health? Yes, or any doubt or AI failure, hands it to a person; no makes a proposal for you to approve.'
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
            {actOnReplies && clips.length > 0 && (
              <>
                <label className="v3-field" htmlFor="msgV">Record voice note (pre-recorded clip)</label>
                <select id="msgV" className="v3-in" value={clip} onChange={(e) => setClip(e.target.value)}>
                  {clips.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
                <div className="small-note">{clips.find((c) => c.id === clip)?.note}</div>
                <div className="btn-row">
                  <button type="button" className="btn primary" disabled={!clip}
                    onClick={() => reply({ clip_id: clip, in_reply_to: lastSent?.id })}>Send voice note</button>
                  <button type="button" className="btn ghost" disabled={!lastEvent}
                    title="Test UC2a-T6: the same delivery arrives twice"
                    onClick={() => act(() => post(`simulator/${patient.mrn}/reply`, lastEvent), (r) => `Replay: ${r.handling.join('; ')}`, true).then(load)}>
                    Resend the last event (replay)
                  </button>
                </div>
              </>
            )}
          </>
        ) : <div className="small-note">Read only.</div>}
        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}

export function openInstruction(ui, item) {
  ui.showModal('SIMULATED hospital record — doctor\'s instruction',
    'In the pilot this is picked up from the hospital record. The demo has no EMR, so enter what the doctor wrote there.',
    <InstructionForm item={item} />);
}

// Gate 3 (REQ-12): instruction text + number of days. Rules create one neutral "Doctor-requested visit" card.
export function InstructionForm({ item, inline }) {
  const ui = useUI();
  const [text, setText] = useState('Review visit within 7 days');
  const [days, setDays] = useState('7');
  const [withCard, setWithCard] = useState(true);
  const [act, error] = useAct();
  const n = Number(days);
  const okDays = Number.isInteger(n) && n >= 1 && n <= 365;
  return (
    <div className={inline ? 'card-flat' : ''} style={inline ? { marginTop: 8 } : undefined}>
      {inline && <div className="card-title">SIMULATED hospital record</div>}
      <label className="v3-field" htmlFor={`inT${item.handover_id}`}>Instruction</label>
      <input id={`inT${item.handover_id}`} className="v3-in" value={text} onChange={(e) => setText(e.target.value)} />
      <label className="v4-check" style={{ marginTop: 10 }}>
        <input type="checkbox" checked={withCard} onChange={(e) => setWithCard(e.target.checked)} /> A visit is needed
      </label>
      {withCard && (
        <>
          <label className="v3-field" htmlFor={`inN${item.handover_id}`}>Within how many days</label>
          <input id={`inN${item.handover_id}`} type="number" min="1" max="365" className="v3-in" value={days} onChange={(e) => setDays(e.target.value)} />
          {okDays && <div className="small-note">Creates a "Doctor-requested visit" card due {fmt(addDays(ui.today, n))}; the patient gets the booking message today.</div>}
        </>
      )}
      <div className="btn-row">
        <button type="button" className="btn primary" disabled={!text.trim() || (withCard && !okDays)}
          onClick={() => act(() => post(`handovers/${item.handover_id}/instruction`, { text, days: withCard ? n : null }),
            withCard ? 'Instruction picked up — card created and the patient told' : 'Instruction picked up', inline)}>
          Save to the record
        </button>
        {!inline && <button type="button" className="btn ghost" onClick={ui.closeModal}>Cancel</button>}
      </div>
      {error && <div className="error">{error}</div>}
    </div>
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
