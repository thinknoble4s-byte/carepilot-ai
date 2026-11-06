// Pop-ups from prototype v7.2: handover review, log a call, pause, messages (WhatsApp, simulated),
// the doctor's instruction in the hospital record, and the morning department lists.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, blobUrl, post, upload } from './api.js';
import { CallPane } from './Triage.jsx';
import { fmt, tapLabel, useUI, VoiceClip } from './ui.jsx';

export const CLOSE_REASONS = ['Nothing clinical', 'Travel or booking plans only', 'Patient called; matter resolved',
  'Doctor instruction done, patient messaged', 'Review booked; doctor\'s plan in place',
  'Patient reached, visit booked', 'Patient reached by cover'];

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

// ---- Voice notes recorded (or uploaded) in the simulator ----
// Converted in the browser to 16 kHz mono WAV, the format Sarvam reads most reliably, and kept under 30 seconds.
const MAX_SECONDS = 30;

export async function toWav(blob) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  const ctx = new Ctx();
  let decoded;
  try {
    decoded = await ctx.decodeAudioData(await blob.arrayBuffer());
  } finally {
    ctx.close();
  }
  const rate = 16000;
  const offline = new OfflineAudioContext(1, Math.max(1, Math.ceil(decoded.duration * rate)), rate);
  const src = offline.createBufferSource();
  src.buffer = decoded;
  src.connect(offline.destination);
  src.start();
  const pcm = (await offline.startRendering()).getChannelData(0);
  const view = new DataView(new ArrayBuffer(44 + pcm.length * 2));
  const str = (o, s) => [...s].forEach((ch, i) => view.setUint8(o + i, ch.charCodeAt(0)));
  str(0, 'RIFF'); view.setUint32(4, 36 + pcm.length * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  str(36, 'data'); view.setUint32(40, pcm.length * 2, true);
  pcm.forEach((v, i) => view.setInt16(44 + i * 2, Math.max(-1, Math.min(1, v)) * 0x7fff, true));
  return { blob: new Blob([view], { type: 'audio/wav' }), seconds: decoded.duration };
}

function VoiceRecorder({ patient, onSend }) {
  const [status, setStatus] = useState('idle');  // idle | recording | ready
  const [secs, setSecs] = useState(0);
  const [note, setNote] = useState(null);  // { blob, seconds, url, source }
  const [spoken, setSpoken] = useState('');
  const [err, setErr] = useState('');
  const rec = useRef(null);
  const timer = useRef(null);
  const canRecord = typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia
    && typeof MediaRecorder !== 'undefined';

  useEffect(() => () => {
    clearInterval(timer.current);
    if (rec.current?.state === 'recording') rec.current.stop();
  }, []);
  useEffect(() => () => { if (note?.url) URL.revokeObjectURL(note.url); }, [note]);

  const keep = async (blob, source) => {
    try {
      const wav = await toWav(blob);
      if (wav.seconds > MAX_SECONDS + 0.5) {
        setErr(`Keep the voice note under ${MAX_SECONDS} seconds (this one is ${Math.round(wav.seconds)} s).`);
        setStatus('idle');
        return;
      }
      setNote({ ...wav, url: URL.createObjectURL(wav.blob), source });
      setStatus('ready');
    } catch (e) {
      setErr('This audio could not be read. Try another recording or a WAV, MP3 or M4A file.');
      setStatus('idle');
    }
  };

  const start = async () => {
    setErr('');
    setNote(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      const r = new MediaRecorder(stream);
      r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      r.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        clearInterval(timer.current);
        keep(new Blob(chunks, { type: r.mimeType || 'audio/webm' }), 'recorded');
      };
      rec.current = r;
      r.start();
      setSecs(0);
      setStatus('recording');
      timer.current = setInterval(() => setSecs((s) => {
        if (s + 1 >= MAX_SECONDS && rec.current?.state === 'recording') rec.current.stop();
        return s + 1;
      }), 1000);
    } catch (e) {
      setErr('Microphone not available: allow microphone access in the browser, or upload an audio file instead.');
    }
  };
  const stop = () => { if (rec.current?.state === 'recording') rec.current.stop(); };

  return (
    <>
      <label className="v3-field">Record a voice note (stored in the database)</label>
      {canRecord ? (
        <div className="btn-row" style={{ marginTop: 0 }}>
          {status === 'recording'
            ? <button type="button" className="btn warn" onClick={stop}>■ Stop ({secs}s / {MAX_SECONDS}s)</button>
            : <button type="button" className="btn" onClick={start}>● Start recording</button>}
        </div>
      ) : (
        <div className="small-note">Recording needs a secure page (https) or this computer (localhost); this demo address is
          plain http, so the browser blocks the microphone. Upload an audio file instead.</div>
      )}
      <label className="v3-field" htmlFor="msgF">…or upload an audio file (up to {MAX_SECONDS} seconds)</label>
      <input id="msgF" type="file" accept="audio/*" className="v3-in"
        onChange={(e) => { const f = e.target.files[0]; e.target.value = ''; if (f) { setErr(''); keep(f, f.name); } }} />
      {status === 'ready' && note && (
        <>
          <div className="small-note" style={{ marginTop: 6 }}>{note.source === 'recorded' ? 'Your recording' : note.source} · {note.seconds.toFixed(1)} s</div>
          <audio controls src={note.url} style={{ width: '100%', maxWidth: 300, height: 34 }} />
          <label className="v3-field" htmlFor="msgL">Language spoken</label>
          <select id="msgL" className="v3-in" value={spoken} onChange={(e) => setSpoken(e.target.value)}>
            <option value="">Patient's language ({patient.preferred_language || patient.language})</option>
            <option value="Hindi">Hindi</option>
            <option value="Kannada">Kannada</option>
            <option value="Tamil">Tamil</option>
            <option value="Telugu">Telugu</option>
            <option value="English">English</option>
            <option value="auto">Detect automatically</option>
          </select>
          <div className="btn-row">
            <button type="button" className="btn primary"
              onClick={async () => { if (await onSend(note.blob, spoken)) { setNote(null); setStatus('idle'); } }}>Send recording as the patient</button>
            <button type="button" className="btn ghost" onClick={() => { setNote(null); setStatus('idle'); }}>Discard</button>
          </div>
        </>
      )}
      {err && <div className="error">{err}</div>}
    </>
  );
}

export function openMessages(ui, patient) {
  ui.showModal(`Messages — ${patient.full_name}`,
    'Approved templates in the patient\'s language. Button replies are logged automatically; free text always goes to a person.',
    <Messages patient={patient} />, true);
}

// The simulated WhatsApp chat (look and feel only: colours, bubbles, ticks, quick replies). Not the real app —
// no WhatsApp branding; replies come from the demo controls (or a tap on a quick-reply button).
const WA_REASON = { no_whatsapp_consent: 'no WhatsApp consent', no_automated_messaging_consent: 'no automated-messaging consent' };
const waDay = (d) => new Date(`${String(d).slice(0, 10)}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
const waTime = (m) => (m.at ? String(m.at).slice(0, 5) : '');
const initials = (name) => (name || 'H').split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join('');

// What CarePilot did with her message, for staff (testing round 31 Oct, defects 9 and 10): the standard reply, the
// triage ticket and, once the call is done, who has her case and when hospital staff will call.
function handledNote(m) {
  if (m.direction !== 'in' || !m.ai_flag) return null;
  const handling = String(m.handling || '');
  if (handling.includes('standard_reply')) return 'CarePilot: no health mention → standard reply sent (no call, no ticket, no task)';
  const lead = handling.includes('empty_message') ? 'CarePilot: empty message (no words), treated as a health message'
    : m.ai_flag === 'yes' ? `CarePilot: health mention flagged${m.ai_failed ? ' (AI failed — safe path)' : ''}` : 'CarePilot: no health mention, but a ticket is open';
  if (m.ep_ticket) {
    if (handling.includes('episode_updated')) return `${lead} → added to the open triage ticket ${m.ep_ticket}`;
    if (!m.ep_tier) return `${lead} → triage ticket ${m.ep_ticket}: automated call within 2 minutes`;
    const unconfirmed = m.ep_red_kind === 'UNCONFIRMED';
    // Testing round 1 Nov (Worksheet2): nobody answered the calls after an empty message
    if (m.ep_action === 'STAFF_CALL_15_MIN' && m.ep_tier === 'TIER_2_AMBER') return `${lead} → ${m.ep_ticket}: two calls not answered; hospital staff will call her within 15 minutes`;
    if (m.ep_red_kind === 'NO_ANSWER') return `${lead} → ${m.ep_ticket} Red: she could not be reached; her family was sent the emergency message with the PDF`;
    if (m.ep_tier === 'TIER_3_GREEN') return `${lead} → ${m.ep_ticket} Green: no doctor's call needed; the coordinator reviews it`;
    if (unconfirmed) return `${lead} → ${m.ep_ticket} Red (unconfirmed): the care coordinator will call her within 15 minutes`;
    if (m.ep_tier === 'TIER_1_RED') return `${lead} → ${m.ep_ticket} Red: go to the nearest emergency department now; her family was sent the message with the PDF; the Doctor on Duty is alerted`;
    return `${lead} → ${m.ep_ticket} Amber: handed to the duty doctor, who will call within 2 hours`;
  }
  return m.ai_flag === 'yes' ? `${lead} → handed to a Hospital Care Coordinator` : 'CarePilot: no health mention → proposal for the coordinator';
}

// A tap names the order it is about (defect 6): "Physiotherapy referral: Booked".
const withOrder = (m, label) => (m.order_name ? `${m.order_name}: ${label}` : label);

function WaWave() {
  return (
    <span className="wa-wave" aria-hidden="true">
      {[6, 12, 9, 16, 7, 13, 18, 10, 6, 14, 9, 17, 8, 12, 6, 15, 10, 7, 13, 9].map((h, i) => <i key={i} style={{ height: h }} />)}
    </span>
  );
}

export function WaPhone({ thread, patient, settings, english, coordinator, onTap, now }) {
  const clinic = settings?.clinic_name || 'Hospital A';
  const replied = (m) => thread.some((x) => x.direction === 'in' && x.reply_to === m.id);
  const chat = useRef(null);
  // A triage episode is one cycle; once it is closed its messages are cleared from the chat (still on the patient
  // page and in the audit trail), so the next episode starts clean. "Show" brings one back.
  const [shownEps, setShownEps] = useState([]);
  const cleared = (m) => m.ep_status === 'closed' && !shownEps.includes(m.triage_episode_id);
  const visible = thread.filter((m) => !cleared(m)).length;
  useEffect(() => { if (chat.current) chat.current.scrollTop = chat.current.scrollHeight; }, [visible]);
  const firstOf = {};
  thread.forEach((m) => { if (m.triage_episode_id && firstOf[m.triage_episode_id] === undefined) firstOf[m.triage_episode_id] = m; });
  let lastDay = null;
  return (
    <div className="wa-phone">
      <div className="wa-status"><span>{now ? String(now).slice(0, 5) : '09:00'}</span><span aria-hidden="true">▂▄▆ ᯤ ▮</span></div>
      <div className="wa-head">
        <span className="wa-back" aria-hidden="true">←</span>
        <span className="wa-avatar" aria-hidden="true">{initials(clinic)}</span>
        <div className="wa-title"><b>{clinic}</b><span>Business account · to {patient.full_name} · simulated</span></div>
        <span className="wa-icons" aria-hidden="true">▭ ✆ ⋮</span>
      </div>
      <div className="wa-chat" ref={chat}>
        <div className="wa-notice">🔒 Messages from the clinic use approved templates. Simulated chat — not delivered to a real phone.</div>
        {thread.length === 0 && <div className="wa-pill">No messages yet</div>}
        {thread.map((m) => {
          if (cleared(m)) {
            if (firstOf[m.triage_episode_id] !== m) return null;
            const n = thread.filter((x) => x.triage_episode_id === m.triage_episode_id).length;
            return (
              <div key={`ep-${m.triage_episode_id}`} className="wa-pill">
                Triage ticket {m.ep_ticket} closed{m.ep_closed_at ? ` ${waDay(m.ep_closed_at)}` : ''} · {n} message{n > 1 ? 's' : ''} cleared{' '}
                <button type="button" className="wa-show" onClick={() => setShownEps([...shownEps, m.triage_episode_id])}>Show</button>
              </div>
            );
          }
          const sepEp = m.ep_status === 'closed' && firstOf[m.triage_episode_id] === m
            ? <div className="wa-pill">Closed triage ticket {m.ep_ticket} <button type="button" className="wa-show" onClick={() => setShownEps(shownEps.filter((x) => x !== m.triage_episode_id))}>Hide</button></div> : null;
          const day = String(m.day).slice(0, 10);
          const sep = day !== lastDay ? <div className="wa-pill">{waDay(day)}</div> : null;
          lastDay = day;
          const key = `${m.direction}-${m.id}`;
          if (m.direction === 'call') {
            return (
              <React.Fragment key={key}>{sep}
                <div className="wa-pill wa-call">📞 Call with {m.party} by {m.actor}: {m.status}{m.body ? ` — ${m.body}` : ''}</div>
              </React.Fragment>
            );
          }
          const out = m.direction === 'out';
          const failed = out && m.status && m.status !== 'sent';
          const taps = out && m.buttons?.length > 0;
          return (
            <React.Fragment key={key}>{sep}{sepEp}
              <div className={`wa-row ${out ? 'in' : 'me'}`}>
                <div className={`wa-b ${out ? 'clinic' : 'patient'}${failed ? ' failed' : ''}${taps ? ' has-taps' : ''}`}>
                  {!out && m.party !== 'patient' && <div className="wa-from">{m.party.charAt(0).toUpperCase() + m.party.slice(1)}</div>}
                  {out && m.party !== 'patient' && <div className="wa-from">To the {m.party}</div>}
                  {failed && <div className="wa-failed">⊘ Not delivered: {m.status === 'blocked' ? (WA_REASON[m.blocked_reason] || (m.blocked_reason || 'blocked').replaceAll('_', ' ')) : m.status.replaceAll('_', ' ')}</div>}
                  {m.direction === 'in' && m.button && <div className="wa-text">{withOrder(m, tapLabel(m, thread))}</div>}
                  {m.kind === 'voice' && (
                    <div className="wa-voice">
                      <span className="wa-mic-av" aria-hidden="true">🎙</span>
                      <div className="wa-voice-main"><WaWave /><VoiceClip inboundId={m.id} /></div>
                    </div>
                  )}
                  {m.kind === 'document' && (
                    <div className="wa-doc"><span className="wa-doc-ic" aria-hidden="true">PDF</span>
                      <div><b>{m.body || 'document'}</b><span>Held Unverified until a coordinator checks it</span></div></div>
                  )}
                  {m.body && m.kind !== 'document' && <div className="wa-text v31-kn">{m.body}</div>}
                  {m.direction === 'in' && m.kind === 'empty' && <div className="wa-text wa-empty">(empty message)</div>}
                  {out && m.attachment_doc_id && (
                    <button type="button" className="wa-doc" style={{ cursor: 'pointer', border: 0, textAlign: 'left', width: '100%' }}
                      onClick={async () => window.open(await blobUrl(`triage/documents/${m.attachment_doc_id}`), '_blank', 'noopener')}>
                      <span className="wa-doc-ic" aria-hidden="true">PDF</span>
                      <div><b>emergency_summary.pdf</b><span>Emergency summary for the treating doctor (synthetic)</span></div></button>
                  )}
                  {m.kind === 'voice' && m.transcript && <div className="wa-note">Transcript: {m.transcript}</div>}
                  {english && m.translation && m.translation !== m.body && m.translation !== m.transcript && <div className="wa-note">AI translation: {m.translation}</div>}
                  {english && out && m.body_en && m.body_en !== m.body && <div className="wa-note">English: {m.body_en}</div>}
                  {handledNote(m) && <div className="wa-note">{handledNote(m)}</div>}
                  <span className="wa-meta">{waTime(m)}{out && !failed && <span className={`wa-ticks${replied(m) ? ' read' : ''}`} aria-label={replied(m) ? 'Read' : 'Delivered'}>✓✓</span>}</span>
                </div>
                {taps && (
                  <div className={`wa-taps${failed ? ' failed' : ''}`}>
                    {m.buttons.map((b) => {
                      const tapped = thread.some((x) => x.direction === 'in' && x.reply_to === m.id && x.button === b.id);
                      return coordinator && !failed ? (
                        <button key={b.id} type="button" className={`wa-tap${tapped ? ' tapped' : ''}`} aria-pressed={tapped}
                          title={`Demo: the patient taps "${b.label_en}"`} onClick={() => onTap(m, b)}>
                          {tapped ? '✓ ' : '↩ '}{withOrder(m, b.label)}</button>
                      ) : <span key={b.id} className={`wa-tap${tapped ? ' tapped' : ''}`}>{tapped ? '✓ ' : '↩ '}{withOrder(m, b.label)}</span>;
                    })}
                  </div>
                )}
              </div>
            </React.Fragment>
          );
        })}
      </div>
      <div className="wa-input" title="Use the demo controls to reply as the patient">
        <span aria-hidden="true">☺</span><div className="wa-box">Message</div><span aria-hidden="true">📎</span>
        <span className="wa-send" aria-hidden="true">🎙</span>
      </div>
    </div>
  );
}

function Messages({ patient }) {
  const ui = useUI();
  const [thread, setThread] = useState([]);
  const [english, setEnglish] = useState(true);
  const [text, setText] = useState('');
  const [settings, setSettings] = useState(null);
  const [clips, setClips] = useState([]);
  const [clip, setClip] = useState('');
  const [doc, setDoc] = useState(null);
  const [variant, setVariant] = useState('clear');
  const [fromPhone, setFromPhone] = useState('');
  const [docNote, setDocNote] = useState('');
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
  useEffect(() => {
    load();
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load]);
  const voiceForm = (ev) => {
    const fd = new FormData();
    fd.append('file', ev.blob, 'voice-note.wav');
    fd.append('event_id', ev.event_id);
    if (ev.in_reply_to) fd.append('in_reply_to', String(ev.in_reply_to));
    if (ev.language) fd.append('language', ev.language);
    return fd;
  };
  const sendRecording = async (blob, language) => {
    const ev = { upload: true, blob, language, in_reply_to: lastSent?.id, event_id: `wa-${Date.now()}` };
    const r = await act(() => upload(`simulator/${patient.mrn}/voice`, voiceForm(ev)), (x) => `Voice note handled: ${x.handling.join('; ')}`, true);
    if (r) { setLastEvent(ev); load(); }
    return r;
  };
  const replay = () => act(() => (lastEvent.upload ? upload(`simulator/${patient.mrn}/voice`, voiceForm(lastEvent))
    : post(`simulator/${patient.mrn}/reply`, lastEvent)), (r) => `Replay: ${r.handling.join('; ')}`, true).then(load);
  const reply = async (body) => {
    // Every simulated delivery carries a WhatsApp-style event id; resending the same id must change nothing (REQ-20).
    const sent = { event_id: `wa-${Date.now()}`, ...body };
    if (await act(() => post(`simulator/${patient.mrn}/reply`, sent), (r) => `Reply handled: ${r.handling.join('; ')}`, true)) {
      setLastEvent(sent);
      setText('');
      load();
    }
  };
  // Use Case 2T: a photo or PDF of an outside report; without a file, a synthetic outside-lab PDF (variant).
  const ROUTED = { auto_matched: 'name, MRN and test match: Report verified by the system, sent to the doctor', wrong_report: 'the patient is asked for the right report', verify: 'coordinator to verify' };
  const sendDoc = async (file) => {
    const fd = new FormData();
    if (file) fd.append('file', file, file.name);
    else fd.append('variant', variant);
    if (fromPhone.trim()) fd.append('from_phone', fromPhone.trim());
    if (docNote.trim()) fd.append('note', docNote.trim());
    fd.append('event_id', `wa-${Date.now()}`);
    if (await act(() => upload(`simulator/${patient.mrn}/document`, fd),
      (r) => `Report held Unverified${r.identifiers && Object.keys(r.identifiers).length ? '; identifiers read' : '; the reader could not read it'}`
        + (r.group_id ? `; joined report #${r.group_id} (sent together)` : r.score != null ? `; score ${r.score} — ${ROUTED[r.routing_applied]}` : ''), true)) {
      setDoc(null);
      setDocNote('');
      load();
    }
  };
  const withButtons = thread.filter((m) => m.direction === 'out' && m.status === 'sent' && m.buttons?.length);
  const [target, setTarget] = useState(null);
  const lastSent = withButtons.find((m) => m.id === target)
    || [...withButtons].reverse().find((m) => m.party === 'patient') || withButtons[withButtons.length - 1];
  const coordinator = ui.role === 'coordinator';
  // Waiting for an outside report: the last message to the patient asked for it and no file has come since.
  const askAt = thread.map((m) => m.direction === 'out' && m.status === 'sent'
    && ['report_upload_request', 'report_upload_nudge', 'report_wrong_report'].includes(m.template_id)).lastIndexOf(true);
  const awaiting = askAt >= 0 && !thread.slice(askAt + 1).some((m) => m.direction === 'in' && m.kind === 'document');
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
        <WaPhone thread={thread} patient={patient} settings={settings} english={english} coordinator={coordinator}
          now={ui.now} onTap={(m, b) => reply({ button: b.id, in_reply_to: m.id })} />
        {thread.some((m) => m.language && m.language !== 'English') && (
          <div className="small-note" style={{ marginTop: 8 }}>Kannada, Hindi, Tamil and Telugu wording are drafts — to be checked by a native speaker.</div>
        )}
      </div>
      <div>
        {coordinator && settings?.voice_triage?.enabled && <CallPane patient={patient} onChange={load} />}
        {coordinator ? (
          <>
            <div className={awaiting ? 'card-flat' : ''} style={awaiting ? { borderLeft: '4px solid var(--amber, #d97706)', marginBottom: 14 } : { marginBottom: 14 }}>
              <div className="card-title">Upload the test report (test done at an outside lab or imaging centre)</div>
              {awaiting && <div className="small-note"><b>Waiting for the report:</b> the patient said the test was done at an outside lab and was asked to send it here.</div>}
              <label className="v3-field" htmlFor="msgD">A photo or PDF of the report</label>
              <input id="msgD" type="file" accept="image/*,application/pdf" className="v3-in"
                onChange={(e) => setDoc(e.target.files?.[0] || null)} />
              <label className="v3-field" htmlFor="msgDV">Or a synthetic outside-lab report (PDF)</label>
              <select id="msgDV" className="v3-in" value={variant} onChange={(e) => setVariant(e.target.value)}>
                {[['clear', 'Clear photo of the right report'], ['blurred', 'Slightly blurred (name only partly read)'], ['ct_scan', 'CT scan report instead of the blood test'],
                  ['other_name', "Report in another person's name"], ['no_mrn', 'Report without the hospital MRN'], ['unreadable', 'Too unclear to read'], ['future_date', 'Dated in the future']]
                  .map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <label className="v3-field" htmlFor="msgDP">Sent from another number (optional; an unknown number never passes)</label>
              <input id="msgDP" className="v3-in" value={fromPhone} onChange={(e) => setFromPhone(e.target.value)} placeholder="+919999999999" />
              <label className="v3-field" htmlFor="msgDN">Note sent with the file (optional; goes through the health check)</label>
              <input id="msgDN" className="v3-in" value={docNote} onChange={(e) => setDocNote(e.target.value)} placeholder="e.g. Done at Sunrise lab, it was cheaper" />
              <div className="btn-row">
                <button type="button" className="btn" disabled={!doc} onClick={() => sendDoc(doc)}>Send the file</button>
                <button type="button" className="btn primary" onClick={() => sendDoc(null)}>Send the synthetic report</button>
              </div>
              <div className="small-note">The patient attaches the file in the chat. The AI reads only who and what the report is about (never a result); plain rules then check it. If the name, the MRN and the test all match, the report is clear and the score is 95 or more, the test moves Completed → Report received → Report verified with no person confirming it, and the doctor sees it. Otherwise a coordinator verifies it; the wrong kind of report or an unreadable file brings the "possible wrong report" message.</div>
            </div>
            <div className="card-title">Demo: reply as the patient</div>
            <div className="small-note">
              {actOnReplies
                ? 'Tap a button on any message in the phone, or reply to one here. Buttons update the workflow directly, with no AI: Booked (or Booked elsewhere, on a test) books the card for today; Please help me book offers four slots, and the slot tapped books it for that date and time with a confirmation; I can\'t come creates a task for a person. Typing just HELP (or मदद / ಸಹಾಯ) asks for a person too. Other text and voice notes go to the AI (Sarvam) for one yes/no question: does it mention health? Yes, or any doubt or AI failure, hands it to a person; no makes a proposal for you to approve.'
                : 'Use Case 1: a reply (button, text or HELP) is saved in the message history and CarePilot takes no action. Reply handling arrives with Use Case 2.'}
            </div>
            {lastSent && (
              <>
                <label className="v3-field" htmlFor="msgTo">Replying to</label>
                <select id="msgTo" className="v3-in" value={lastSent.id} onChange={(e) => setTarget(Number(e.target.value))}>
                  {withButtons.map((m) => <option key={m.id} value={m.id}>{fmt(m.day)} · to {m.party} · {m.order_name ? `${m.order_name} · ` : ''}{m.body_en.slice(0, 70)}…</option>)}
                </select>
              </>
            )}
            {lastSent ? (
              <div className="btn-row">
                {lastSent.buttons.map((b) => (
                  <button key={b.id} type="button" className="btn sm" title={b.label_en}
                    onClick={() => reply({ button: b.id, in_reply_to: lastSent.id })}>Tap “{withOrder(lastSent, b.label_en)}”</button>
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
              <button type="button" className="btn ghost" title="Testing round 1 Nov: a message with nothing in it"
                onClick={() => reply({ empty: true, in_reply_to: lastSent?.id })}>Send an empty message</button>
            </div>
            {actOnReplies && clips.length > 0 && (
              <>
                <label className="v3-field" htmlFor="msgV">Send a pre-recorded voice note (synthetic clip)</label>
                <select id="msgV" className="v3-in" value={clip} onChange={(e) => setClip(e.target.value)}>
                  {clips.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
                </select>
                <div className="small-note">{clips.find((c) => c.id === clip)?.note}</div>
                <div className="btn-row">
                  <button type="button" className="btn primary" disabled={!clip}
                    onClick={() => reply({ clip_id: clip, in_reply_to: lastSent?.id })}>Send voice note</button>
                  <button type="button" className="btn ghost" disabled={!lastEvent}
                    title="Test UC2a-T6: the same delivery arrives twice"
                    onClick={replay}>
                    Resend the last event (replay)
                  </button>
                </div>
                <VoiceRecorder patient={patient} onSend={sendRecording} />
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
