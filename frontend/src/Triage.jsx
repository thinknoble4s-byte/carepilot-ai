// Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.2): the call pane in the phone simulator (what the AI
// heard and the questions the router built), the "Why this tier" panel, the Triage Room and the Pre-OPD brief.
// The call is simulated: Bulbul speaks the fixed lines in the patient's language (Tamil, Hindi or Telugu; text only
// in simulated mode), the tester answers by voice, words or on-screen buttons, and plain rules give Red, Amber or Green. Every screen says SYNTHETIC DEMO.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, blobUrl, post, upload } from './api.js';
import { openMessages, toWav } from './Modals.jsx';
import { PatientLink } from './PatientCard.jsx';
import { fmt, Pill, ScreenHead, useUI, VoiceClip } from './ui.jsx';

const TIER = { TIER_1_RED: ['Red', 'bad'], TIER_2_AMBER: ['Amber', 'warn'], TIER_3_GREEN: ['Green', 'good'] };
const KIND = { CONFIRMED: 'CONFIRMED', NO_ANSWER: 'NO ANSWER', UNCONFIRMED: 'UNCONFIRMED' };
const ED = [['REACHED', 'Reached (with hospital name)'], ['NOT_YET', 'Not yet'], ['REFUSED', 'Refused'], ['NOT_REACHABLE', 'Not reachable']];
const ORDER = { physio: 'Physiotherapy referral', bloods: 'Bring forward blood tests', none: 'No order needed' };
const hm = (ts) => (ts ? String(ts).slice(11, 16) : '');
const when = (ts) => (ts ? `${fmt(String(ts).slice(0, 10))} ${hm(ts)}` : '');
const show = (v) => (v === null || v === undefined ? '—' : typeof v === 'number' && Number.isInteger(v) ? String(v) : String(v).replaceAll('_', ' '));

export function TierPill({ ep }) {
  if (!ep?.tier) return null;
  const [label, tone] = TIER[ep.tier];
  return <Pill tone={tone}>{label}{ep.red_kind ? ` · ${KIND[ep.red_kind]}` : ''}</Pill>;
}

// The answer buttons and the follow-up line in the call's language (drafts, like the call's lines).
const OWN = {
  Tamil: { YES: 'ஆம்', NO: 'இல்லை', UNKNOWN: 'தெரியவில்லை', followup: 'வணக்கம். … இன்று எப்படி இருக்கிறீர்கள்: நேற்றை விட நன்றாக, அதே போல, அல்லது மோசமாக?' },
  Hindi: { YES: 'हाँ', NO: 'नहीं', UNKNOWN: 'पता नहीं', followup: 'नमस्ते। … आज आप कैसी हैं: कल से बेहतर, वैसी ही, या ज़्यादा खराब?' },
  Telugu: { YES: 'అవును', NO: 'లేదు', UNKNOWN: 'తెలియదు', followup: 'నమస్కారం. … ఈ రోజు మీరు ఎలా ఉన్నారు: నిన్నటి కంటే బాగా, అలాగే, లేదా ఇంకా బాగోలేదా?' },
};
const answerLabel = (lang, v, en) => (OWN[lang] ? `${en} · ${OWN[lang][v]}` : en);

// Testing round 31 Oct, defect 4: every message she sent in this episode, with the date and time, most recent first.
export function PatientNotes({ ep }) {
  const notes = ep.patient_notes || [];
  if (!notes.length) return null;
  return (
    <div className="vt-notes">
      <div className="small-note"><b>Patient voice note{notes.length > 1 ? 's' : ''}</b> (most recent first):</div>
      {notes.map((n) => (
        <div key={n.inbound_id} className="card-flat" style={{ padding: '6px 10px', margin: '6px 0' }}>
          <div className="small-note">{fmt(n.received_on)}{n.received_time ? ` · ${String(n.received_time).slice(0, 5)}` : ''}{n.kind === 'voice' ? ' · voice note' : ' · message'}</div>
          {n.kind === 'voice' ? <VoiceClip inboundId={n.inbound_id} /> : <div>{n.text}</div>}
          {(n.translation || n.transcript) && <div className="small-note">{n.translation ? `AI translation: ${n.translation}` : `Transcript: ${n.transcript}`}</div>}
        </div>
      ))}
    </div>
  );
}

// Defect 12: her last closed episode, as context for whoever handles this one (built from its record).
function Previous({ ep }) {
  const [open, setOpen] = useState(false);
  const prev = ep.previous;
  if (!prev) return null;
  return (
    <div className="card-flat" style={{ margin: '8px 0', padding: '8px 12px' }}>
      <button type="button" className="btn sm ghost" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? '▾' : '▸'} Previous episode</button>
      {' '}<span className="small-note">{prev.summary}</span>
      {open && <ul className="vt-sum">{prev.details.map((d, i) => <li key={i}>{d}</li>)}</ul>}
    </div>
  );
}

export function Synthetic() {
  return (
    <div className="vt-synth">SYNTHETIC DEMO · Voice Triage MVP-lite v1.2 · DRAFT_UNSIGNED — thresholds, the new rules and the
      Tamil, Hindi and Telugu lines are clinician-reviewed drafts until Dr. Sekhar signs them. AI listens, code decides, people decide care.</div>
  );
}

function useRun(after) {
  const ui = useUI();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const run = async (fn, done) => {
    setError('');
    setBusy(true);
    try {
      const r = await fn();
      if (done) ui.toast(typeof done === 'function' ? done(r) : done);
      if (after) after(r);
      ui.refresh();
      return r;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  };
  return [run, error, busy];
}

async function openPdf(docId) {
  const url = await blobUrl(`triage/documents/${docId}`);
  window.open(url, '_blank', 'noopener');
}

export function WhyPanel({ ep }) {
  if (!ep?.result) return null;
  const r = ep.result;
  return (
    <div className={`vt-why ${TIER[ep.result.tier][1]}`}>
      <div className="card-title">Why this tier <TierPill ep={{ tier: r.tier, red_kind: r.red_kind }} />
        {ep.tier !== r.tier && <span className="small-note">· now {TIER[ep.tier][0]} (changed by a person; see Set by)</span>}</div>
      <ul>{r.why.map((w) => <li key={w}>{w}</li>)}</ul>
      <div className="small-note">Rules: {r.reason_codes.join(', ') || 'none fired'} · action {r.action_code} ·
        {' '}{r.protocol_profile} / {r.protocol_status}{r.stopped_after_danger_sign ? ' · the call stopped at the first danger sign' : ''}.
        Every line is a fixed sentence from a rule and her stored history; no AI opinion.</div>
    </div>
  );
}

const MODULE = { core: 'core', history_arm: 'history', dizziness_or_vision: 'dizzy/vision', fever_chills: 'fever/chills',
  headache: 'headache', nausea_vomiting: 'nausea', sleepiness: 'sleepy', arm_pain: 'arm pain',
  arm_swelling_or_redness: 'arm swelling', tiredness: 'tired' };

function AnswerTable({ table }) {
  return (
    <table className="vt-table">
      <thead><tr><th>#</th><th>Question</th><th>Value</th></tr></thead>
      <tbody>
        {table.map((q, i) => (
          <tr key={q.id} className={q.asked ? '' : 'off'}>
            <td>{i + 1}{q.danger && <span className="vt-danger" title="Red question: an unclear answer gives Red (unconfirmed)">!</span>}</td>
            <td>{q.en}{q.module && <span className={`tag vt-mod ${q.module === 'core' ? 'core' : ''}`}>{MODULE[q.module] || q.module}</span>}{q.history && <span className="tag">history</span>}</td>
            <td><b>{q.asked ? show(q.value) : q.skipped ? 'skipped' : 'not asked'}</b></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// One voice at a time on the page, as on a real call: a new step (the caller answers before the question has
// finished) or a click on a line stops the voice that is playing, and a line still being fetched for an earlier
// step is dropped instead of played late.
const voicePlayer = { audio: null, url: null, gen: 0 };

function stopVoice() {
  voicePlayer.gen += 1;
  if (voicePlayer.audio) voicePlayer.audio.pause();
  if (voicePlayer.url) URL.revokeObjectURL(voicePlayer.url);
  voicePlayer.audio = null;
  voicePlayer.url = null;
  return voicePlayer.gen;
}

// Plays one line; resolves true when it ends, false when it was stopped or a newer step took over.
async function playVoice(path, gen) {
  const url = await blobUrl(path);
  if (gen !== voicePlayer.gen) {
    URL.revokeObjectURL(url);
    return false;
  }
  if (voicePlayer.url) URL.revokeObjectURL(voicePlayer.url);
  const audio = new Audio(url);
  voicePlayer.audio = audio;
  voicePlayer.url = url;
  return new Promise((resolve) => {
    const done = () => resolve(gen === voicePlayer.gen);  // 'pause' also fires at the natural end
    audio.onpause = done;
    audio.onerror = done;
    audio.play().catch(done);
  });
}

function Turns({ ep, own = true }) {
  const box = useRef(null);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [ep.turns.length]);
  useEffect(() => () => stopVoice(), []);  // closing the pop-up stops the voice
  const play = (i) => {
    // Simulated mode has no audio: the text is shown.
    playVoice(`triage/episodes/${ep.episode_id}/turns/${i}/audio`, stopVoice()).catch(() => {});
  };
  return (
    <div className="vt-turns" ref={box}>
      {ep.turns.map((t) => (
        <div key={t.i} className={`vt-turn ${t.who}`}>
          <span className="vt-at">{t.at}</span>
          {t.who === 'system' ? <i>{t.en}</i> : (
            <div>
              {t.who === 'agent' && <button type="button" className="vt-play" title="Play (Bulbul)" onClick={() => play(t.i)}>🔊</button>}
              {t.who === 'patient' && t.audio_id && <button type="button" className="vt-play" title="Her recorded answer" onClick={() => play(t.i)}>▶</button>}
              {own && t.ta && <span className="v31-kn">{t.ta}</span>}
              {(t.en && (!own || t.en !== t.ta)) && <div className="vt-en">{t.en}</div>}
              {t.who === 'patient' && t.value !== undefined && <div className="vt-val">→ <b>{show(t.value)}</b> <span>({t.how})</span></div>}
              {t.history && <span className="tag">history</span>}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

const NUMBERS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
const TEMPS = [98.4, 99.5, 100.3, 100.4, 101.2];

function AnswerRecorder({ onSend, disabled }) {
  const [rec, setRec] = useState(null);
  const [err, setErr] = useState('');
  const can = typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia
    && typeof MediaRecorder !== 'undefined';
  if (!can) return <span className="small-note">Microphone needs https or localhost; use the buttons.</span>;
  const start = async () => {
    setErr('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chunks = [];
      const r = new MediaRecorder(stream);
      r.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setRec(null);
        try {
          const wav = await toWav(new Blob(chunks, { type: r.mimeType || 'audio/webm' }));
          onSend(wav.blob);
        } catch (e) {
          setErr('The recording could not be read.');
        }
      };
      r.start();
      setRec(r);
    } catch (e) {
      setErr('Microphone not available.');
    }
  };
  return (
    <>
      {rec ? <button type="button" className="btn warn sm" onClick={() => rec.stop()}>■ Stop and send the answer</button>
        : <button type="button" className="btn sm" disabled={disabled} onClick={start}>● Speak the answer</button>}
      {err && <span className="error"> {err}</span>}
    </>
  );
}

// What the AI heard in her message (tags from fixed lists) and what the rules built from it: the danger core, the
// bangles question, the modules she mentioned, and the floors her own words set. The AI chose nothing else.
export function PlanChips({ ep }) {
  const p = ep?.plan;
  if (!p) return null;
  return (
    <div className="vt-plan">
      <div><span className="small-note">AI heard:</span>{' '}
        {p.tagged && p.tags_valid ? (p.heard.length ? p.heard.map((h) => <span key={h} className="vt-chip">{h}</span>)
          : <span className="vt-chip">{p.health_related === 'UNSURE' ? 'unsure' : 'no symptom named'}</span>)
          : <span className="vt-chip off">{p.tagged ? 'tags unusable' : 'not tagged'}</span>}</div>
      <div className="small-note">Rules built <b>{p.question_ids.length} questions</b>: {p.modules.map((m) => m === 'fallback' ? 'the fixed 12 (the AI could not tell)' : (MODULE[m] || m)).join(' + ')}
        {p.floors.length > 0 && <> · floor: {p.floors.map((f) => f === 'AMBER_PATIENT_SAYS_SEVERE' ? 'at least Amber (she said “severe”)' : 'at least Amber, a nurse calls (a problem we have no questions for)').join('; ')}</>}.
        {' '}The five danger questions come first and are never skipped.</div>
    </div>
  );
}

// The call pane in the phone simulator (coordinator plays the patient). onChange reloads the chat beside it, where
// the call's messages (the Green advice, the family's Red message with the PDF) appear.
export function CallPane({ patient, onChange }) {
  const ui = useUI();
  const [ep, setEp] = useState(null);
  const [loaded, setLoaded] = useState(false);
  const [voice, setVoice] = useState(true);
  const [provider, setProvider] = useState('fake');
  const [scenario, setScenario] = useState('amber');
  const [text, setText] = useState('');
  const seen = useRef(0);

  const speak = useCallback(async (e, from) => {
    const gen = stopVoice();
    if (!voice || provider === 'fake') return;
    for (const t of e.turns.slice(from).filter((x) => x.who === 'agent')) {
      try {
        if (!(await playVoice(`triage/episodes/${e.episode_id}/turns/${t.i}/audio`, gen))) return;
      } catch (err) {
        // No audio for this line: the text is on screen.
      }
    }
  }, [voice, provider]);
  useEffect(() => () => stopVoice(), []);

  // Show the new state and speak the lines the call has added since the last one.
  const show_ = useCallback((e, say) => {
    const from = ep && e.episode_id === ep.episode_id ? seen.current : 0;
    seen.current = e.turns.length;
    setEp(e);
    if (say) speak(e, from);
  }, [ep, speak]);

  const load = useCallback(async () => {
    const list = await api(`triage/episodes?mrn=${encodeURIComponent(patient.mrn)}&status=all`).catch(() => []);
    const e = list.length ? await api(`triage/episodes/${list[0].episode_id}`) : null;
    seen.current = e ? e.turns.length : 0;
    setEp(e);
    setLoaded(true);
  }, [patient.mrn]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { api('demo/settings').then((s) => setProvider(s.ai_provider)).catch(() => {}); }, []);
  // A voice note sent from the chat opens an episode: look again when the screen refreshes.
  useEffect(() => { const t = setInterval(() => { if (!ep || ep.status === 'closed') load(); }, 4000); return () => clearInterval(t); }, [ep, load]);

  const [run, error, busy] = useRun();
  const shown = (r) => { if (r) { show_(r, true); if (onChange) onChange(); } };
  // The agent stops talking the moment she answers, not when the server replies.
  const act = (path, body, done) => { stopVoice(); return run(() => post(`triage/episodes/${ep.episode_id}/${path}`, body), done).then(shown); };
  const sendVoice = (blob) => {
    stopVoice();
    const fd = new FormData();
    fd.append('file', blob, 'answer.wav');
    run(() => upload(`triage/episodes/${ep.episode_id}/answer-voice`, fd)).then(shown);
  };

  if (!loaded) return null;
  const q = ep?.current;
  return (
    <div className="card-flat vt-call">
      <div className="card-title">📞 Automated triage call {ep && <TierPill ep={ep} />}
        {ep && <span className="small-note">#{ep.episode_id} · {ep.call_status.replace('_', ' ')}{ep.status === 'closed' ? ' · closed' : ''}</span>}</div>
      <Synthetic />
      {!ep && <div className="small-note">No call yet. Send a voice note about her health (e.g. TA-01 below): the AI tags it, the rules build
        the questions, the fixed acknowledgement goes out and the call is due within 2 minutes. A message that is clearly not about health
        (e.g. “I want to go shopping tomorrow”) gets one standard reply and no call.</div>}
      {ep && (
        <div className="small-note">Her message: “{ep.translation || ep.patient_words}” · consent: WhatsApp, voice call and recording, family updates (lite profile).
          <label style={{ marginLeft: 8 }}><input type="checkbox" checked={voice} onChange={(e) => { if (!e.target.checked) stopVoice(); setVoice(e.target.checked); }} /> Play Bulbul voice{provider === 'fake' ? ' (simulated mode: text only)' : ''}</label></div>
      )}
      {ep && <PlanChips ep={ep} />}
      {ep?.call_status === 'due' && ep.status === 'open' && (
        <div className="vt-ring">
          <div><b>Incoming call — {ep.attempts ? 'second call' : `due by ${hm(ep.call_due_at)}`}</b><span className="small-note"> · from {ui.user?.person ? 'the clinic' : 'CarePilot'} (automated)</span></div>
          <div className="btn-row">
            <button type="button" className="btn primary" disabled={busy} onClick={() => act('pickup', {}, 'Call answered')}>Pick up</button>
            <button type="button" className="btn" disabled={busy} onClick={() => act('no-answer', {}, (r) => (r.tier ? 'Second call not answered: Red (no answer)' : 'Not answered; the next call is in 5 minutes (clock moved)'))}>She does not answer</button>
          </div>
        </div>
      )}
      {ep?.call_status === 'in_call' && q && (
        <div className="vt-q">
          <div className="small-note">Question {ep.table.findIndex((x) => x.id === q.id) + 1} of {ep.table.length}{q.history && <span className="tag">history</span>}{q.danger && <span className="tag">Red question</span>}</div>
          <div className="v31-kn vt-qta">{q.ta}</div>
          <div className="vt-en">{q.en}</div>
          <div className="btn-row">
            {q.kind === 'yesno' && [['YES', 'Yes'], ['NO', 'No'], ['UNKNOWN', "Don't know"]].map(([v, en]) => [v, answerLabel(ep.language, v, en)]).map(([v, l]) => (
              <button key={v} type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: v })}>{l}</button>))}
            {q.kind === 'number' && NUMBERS.map((n) => <button key={n} type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: n })}>{n}</button>)}
            {q.kind === 'number' && <button type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: 'UNKNOWN' })}>Don't know</button>}
            {q.kind === 'temperature' && TEMPS.map((n) => <button key={n} type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: n })}>{n} °F</button>)}
            {q.kind === 'temperature' && <button type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: 'NO_THERMOMETER' })}>No thermometer</button>}
          </div>
          <div className="btn-row">
            <input className="v3-in" style={{ flex: 1, minWidth: 160 }} value={text} placeholder={`Or type what she says (${ep.language === 'English' ? 'English' : `${ep.language} or English`})`}
              onChange={(e) => setText(e.target.value)} />
            <button type="button" className="btn sm primary" disabled={busy || !text.trim()} onClick={() => { act('answer', { text }); setText(''); }}>Send words</button>
            {provider !== 'fake' && <AnswerRecorder disabled={busy} onSend={sendVoice} />}
            <button type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { key: '0' }, 'Pressed 0: nurse')}>Press 0 (nurse)</button>
            <button type="button" className="btn sm ghost" disabled={busy} onClick={() => act('answer', { silence: true })}>Silence</button>
          </div>
        </div>
      )}
      {ep && ep.status === 'open' && ['due', 'in_call'].includes(ep.call_status) || (ep && ['done', 'no_answer'].includes(ep.call_status)) ? (
        <div className="btn-row">
          <select className="v3-in" style={{ width: 'auto' }} value={scenario} onChange={(e) => setScenario(e.target.value)}>
            {Object.entries(ep.scenarios).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <button type="button" className="btn sm" disabled={busy} title={`The guide's scripted answers (in ${ep.language}) go through the same parsing and rules`}
            onClick={() => act('play', { scenario }, (r) => `Played as ${scenario}: ${TIER[r.tier]?.[0] || r.call_status}`)}>
            {['done', 'no_answer'].includes(ep.call_status) ? 'Replay the call as' : 'Play as'}</button>
        </div>
      ) : null}
      {ep && ep.turns.length > 0 && <Turns ep={ep} />}
      {ep && <AnswerTable table={ep.table} />}
      {ep?.result && <WhyPanel ep={ep} />}
      {ep?.result && <Outcome ep={ep} />}
      {ep?.status === 'open' && ep.followup_due_on && !ep.followup && (
        ep.followup_due ? (
          <div className="vt-q">
            <div className="card-title">Follow-up call (next day)</div>
            {OWN[ep.language] && <div className="v31-kn">{OWN[ep.language].followup}</div>}
            <div className="vt-en">How are you today: better than yesterday, the same, or worse?</div>
            <div className="btn-row">
              {['BETTER', 'SAME', 'WORSE'].map((a) => <button key={a} type="button" className="btn sm" disabled={busy}
                onClick={() => act('followup', { answer: a }, `Follow-up: ${a.toLowerCase()}`)}>{a.charAt(0) + a.slice(1).toLowerCase()}</button>)}
            </div>
          </div>
        ) : <div className="small-note">Follow-up call due on {fmt(ep.followup_due_on)} (advance the demo clock 1 day).</div>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function Outcome({ ep }) {
  const ui = useUI();
  if (ep.tier === 'TIER_1_RED') {
    return (
      <div className="small-note vt-out">
        {ep.package_sent_at ? <>Emergency package sent {hm(ep.package_sent_at)}{ep.package_sent_by && !ep.package_sent_by.startsWith('CarePilot') ? ` by ${ui.name(ep.package_sent_by)}` : ' automatically'}:
          family message{ep.caregiver_name ? ` to ${ep.caregiver_name}` : ''} with the summary PDF{' '}
          {ep.pdf_doc_id && <button type="button" className="btn sm ghost" onClick={() => openPdf(ep.pdf_doc_id)}>Open the PDF</button>} · ER Desk, Doctor on Duty and Senior Oncologist alerted · coordinator to confirm the emergency visit by {hm(ep.task?.due_at_demo)}.</>
          : <>Red (unconfirmed): no family message. A nurse calls back by {hm(ep.callback_due_at)}; a person may press “Send emergency package”.</>}
      </div>
    );
  }
  if (ep.tier === 'TIER_2_AMBER') {
    return <div className="small-note vt-out">SBAR card to the Doctor on Duty and the coordinator · callback by <b>{hm(ep.callback_due_at)}</b>
      {ep.doctor_order ? ` · doctor's order: ${ORDER[ep.doctor_order.order]}` : ''}.</div>;
  }
  return <div className="small-note vt-out">Green: a digest line for the coordinator, no alert{ep.result?.repeat_advice ? '; the doctor’s own signed sentence was repeated (she reported nausea)' : ''}. Follow-up call on {fmt(ep.followup_due_on)}.</div>;
}

// The call put back together (Bulbul's lines and her spoken answers in call order) so a doctor or coordinator can
// check what the transcript says she said. A tone marks an answer given on screen; listening is audited.
export function CallRecording({ ep }) {
  const [src, setSrc] = useState(null);
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => () => { if (src) URL.revokeObjectURL(src); }, [src]);
  if (!ep.has_call) return null;
  if (src) {
    return (
      <div className="vt-rec">
        <audio controls autoPlay preload="auto" src={src} onPlay={() => stopVoice()} />
        <span className="small-note">Call recording: Bulbul's lines and her spoken answers, in call order. ♪ a short
          tone = an answer given on screen (button or typed), no voice recorded.</span>
      </div>
    );
  }
  const open = async () => {
    setBusy(true);
    setErr('');
    try {
      setSrc(await blobUrl(`triage/episodes/${ep.episode_id}/recording`));
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <button type="button" className="btn sm ghost" disabled={busy} onClick={open}>{busy ? 'Preparing the recording…' : '▶ Call recording'}</button>
      {err && <span className="error"> {err}</span>}
    </>
  );
}

// ---- Episode pop-up: transcript, answers, Why, sources ----
export function openEpisode(ui, id) {
  ui.showModal(`Triage call #${id}`, 'The call as it happened: every line spoken (fixed templates), every answer heard and the value it became.',
    <EpisodeView id={id} />, true);
}

function EpisodeView({ id }) {
  const [ep, setEp] = useState(null);
  useEffect(() => { api(`triage/episodes/${id}`).then(setEp).catch(() => {}); }, [id]);
  if (!ep) return <div className="empty">Loading…</div>;
  return (
    <>
      <Synthetic />
      <div className="btn-row"><CallRecording ep={{ ...ep, has_call: ep.turns.some((t) => t.who === 'agent') }} /></div>
      <div className="grid-2">
        <div><Turns ep={ep} /></div>
        <div><AnswerTable table={ep.table} /><WhyPanel ep={ep} />
          <PatientNotes ep={ep} /></div>
      </div>
    </>
  );
}

// ---- Pre-OPD brief ----
export function openBrief(ui, mrn) {
  ui.showModal('Pre-OPD brief', 'The last 90 days from the record and the triage episodes; every line links to its source.', <Brief mrn={mrn} />, true);
}

function Brief({ mrn }) {
  const ui = useUI();
  const [b, setB] = useState(null);
  const [note, setNote] = useState(null);
  useEffect(() => { api(`triage/brief/${mrn}`).then(setB).catch(() => {}); }, [mrn]);
  if (!b) return <div className="empty">Loading…</div>;
  const source = (s, line) => {
    if (s.kind === 'audio') return <VoiceClip key="a" inboundId={s.inbound_id} />;
    if (s.kind === 'pdf') return <button key="p" type="button" className="btn sm ghost" onClick={() => openPdf(s.doc_id)}>{s.label}</button>;
    if (s.kind === 'recording') return <CallRecording key="r" ep={{ episode_id: s.episode_id, has_call: true }} />;
    if (s.kind === 'transcript') return <button key="t" type="button" className="btn sm ghost" onClick={() => openEpisode(ui, s.episode_id)}>{s.label}</button>;
    if (s.kind === 'note') return <button key="n" type="button" className="btn sm ghost" onClick={() => api(`triage/episodes/${s.episode_id}`).then((e) => setNote(e.doctor_note))}>{s.label}</button>;
    if (s.kind === 'card') return <button key="c" type="button" className="btn sm ghost" onClick={() => ui.nav('plan', mrn)}>{s.label}</button>;
    return <span key={line} className="tag">{s.label}</span>;
  };
  return (
    <>
      <Synthetic />
      {b.open_red && <div className="vt-banner">RED alert open for {b.title}. The ER Desk has the doctor note and the summary PDF.</div>}
      <div className="card-flat">
        <div className="card-title">{b.title} · {b.mrn}</div>
        {b.clinical && <div className="small-note">{b.clinical.diagnosis} · {b.clinical.treatment_done.join('; ')} · now: {b.clinical.current_phase}</div>}
        <div className="small-note">Medicines: {b.medicines.map((m) => `${m.name} (${m.schedule})`).join('; ') || '—'}</div>
        {b.advice && <div className="small-note">Signed advice ({b.advice.by}, {fmt(b.advice.date)}): “{b.advice.en}”</div>}
      </div>
      <table className="vt-table">
        <thead><tr><th>When</th><th>What happened</th><th>Source</th></tr></thead>
        <tbody>
          {b.lines.map((l, i) => (
            <tr key={i}>
              <td style={{ whiteSpace: 'nowrap' }}>{fmt(l.at)}{l.at.length > 10 ? ` ${l.at.slice(11, 16)}` : ''}</td>
              <td>{l.tier && <TierPill ep={{ tier: l.tier }} />} {l.text}</td>
              <td>{l.sources.map((s) => source(s, i))}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {note && <div className="card-flat"><div className="card-title">Doctor note</div><div className="small-note">{note}</div></div>}
    </>
  );
}

// ---- Screens ----
function useEpisodes(status = 'open') {
  const [list, setList] = useState(null);
  const [error, setError] = useState('');
  const load = useCallback(() => { api(`triage/episodes?status=${status}`).then(setList).catch((e) => setError(e.message)); }, [status]);
  useEffect(() => load(), [load]);
  return [list, load, error];
}

function Head({ ep, children }) {
  const ui = useUI();
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
      <div className="v4-name">
        <PatientLink mrn={ep.mrn} name={ep.full_name} />{' '}
        <span className="v4-meta">· {ep.mrn} · {ep.preferred_language} · call #{ep.episode_id} at {hm(ep.result_at || ep.opened_at)}</span>
      </div>
      <div><TierPill ep={ep} /> {children}</div>
    </div>
  );
}

function Sources({ ep, brief }) {
  const ui = useUI();
  return (
    <div className="btn-row">
      <button type="button" className="btn sm ghost" onClick={() => openEpisode(ui, ep.episode_id)}>Call transcript</button>
      <CallRecording ep={ep} />
      {ep.pdf_doc_id && <button type="button" className="btn sm ghost" onClick={() => openPdf(ep.pdf_doc_id)}>Emergency summary PDF</button>}
      {brief && <button type="button" className="btn sm ghost" onClick={() => openBrief(ui, ep.mrn)}>Pre-OPD brief</button>}
    </div>
  );
}

function EdCallForm({ ep }) {
  const [outcome, setOutcome] = useState('');
  const [hospital, setHospital] = useState('');
  const [note, setNote] = useState('');
  const [run, error, busy] = useRun();
  return (
    <>
      <div className="btn-row">
        <select className="v3-in" style={{ width: 'auto' }} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
          <option value="">Log call result…</option>
          {ED.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        {outcome === 'REACHED' && <input className="v3-in" style={{ width: 200 }} placeholder="Hospital name" value={hospital} onChange={(e) => setHospital(e.target.value)} />}
        <input className="v3-in" style={{ flex: 1, minWidth: 140 }} placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
        <button type="button" className="btn sm primary" disabled={busy || !outcome || (outcome === 'REACHED' && !hospital.trim())}
          onClick={() => run(() => post(`triage/episodes/${ep.episode_id}/ed-call`, { outcome, hospital, note }), `Call logged: ${outcome.replace('_', ' ')}`)}>Log</button>
      </div>
      {error && <div className="error">{error}</div>}
    </>
  );
}

function NoteAction({ label, path, ep, placeholder, done, tone = '' }) {
  const [note, setNote] = useState('');
  const [run, error, busy] = useRun();
  return (
    <>
      <div className="btn-row">
        <input className="v3-in" style={{ flex: 1, minWidth: 160 }} placeholder={placeholder} value={note} onChange={(e) => setNote(e.target.value)} />
        <button type="button" className={`btn sm ${tone}`} disabled={busy || !note.trim()}
          onClick={() => run(() => post(`triage/episodes/${ep.episode_id}/${path}`, { note }), done)}>{label}</button>
      </div>
      {error && <div className="error">{error}</div>}
    </>
  );
}

function Simple({ label, path, ep, done, tone = '', disabled, title }) {
  const [run, error, busy] = useRun();
  return (
    <>
      <button type="button" className={`btn sm ${tone}`} disabled={busy || disabled} title={title}
        onClick={() => run(() => post(`triage/episodes/${ep.episode_id}/${path}`, {}), done)}>{label}</button>
      {error && <span className="error"> {error}</span>}
    </>
  );
}

// Confirm closure (Amber): the Doctor on Duty or the coordinator, only once the 24-hour call proposed it.
function ConfirmClosure({ ep }) {
  if (ep.tier !== 'TIER_2_AMBER' || !(ep.doctor_order || ep.nurse_callback)) return null;
  if (ep.state !== 'CLOSURE_PROPOSED') {
    return <div className="btn-row"><button type="button" className="btn sm" disabled
      title="An Amber ticket is closed only after the 24-hour call proposes closure">Confirm closure</button>
      <span className="small-note">after the 24-hour call says “better” (now: {ep.state_label})</span></div>;
  }
  return <NoteAction label="Confirm closure" path="confirm-closure" ep={ep} tone="primary"
    placeholder={ep.followup ? `Closing note (follow-up: ${ep.followup.answer.toLowerCase()})` : 'Closing note'} done="Closure confirmed; routine follow-up resumes" />;
}

// Acknowledge: the Amber card (Doctor on Duty, nurse or coordinator).
function Ack({ ep }) {
  const ui = useUI();
  if (ep.tier !== 'TIER_2_AMBER' || ep.status !== 'open') return null;
  if (ep.coord_ack_by) return <span className="small-note">✓ Acknowledged by {ui.name(ep.coord_ack_by)}</span>;
  return <Simple label="Acknowledge" path="acknowledge" ep={ep} done="Acknowledged" />;
}

function DoctorActions({ ep }) {
  const [note, setNote] = useState('');
  const [run, error, busy] = useRun();
  return (
    <>
      {ep.tier === 'TIER_2_AMBER' && !ep.doctor_order && !ep.nurse_asked && (
        <div className="btn-row">
          <input className="v3-in" style={{ flex: 1, minWidth: 160 }} placeholder="Outcome of your call (optional), e.g. pause Neurobion Forte for 5 days" value={note} onChange={(e) => setNote(e.target.value)} />
          {Object.entries(ORDER).map(([k, l]) => (
            <button key={k} type="button" className={`btn sm ${k === 'none' ? '' : 'primary'}`} disabled={busy}
              onClick={() => run(() => post(`triage/episodes/${ep.episode_id}/order`, { order: k, note }), k === 'none' ? 'No order needed — recorded' : `${l}: order card created, booking message sent`)}>{l}</button>
          ))}
        </div>
      )}
      {ep.doctor_order && <div className="small-note">Order: <b>{ORDER[ep.doctor_order.order]}</b>{ep.doctor_order.note ? ` — “${ep.doctor_order.note}”` : ''}</div>}
      <ConfirmClosure ep={ep} />
      {error && <div className="error">{error}</div>}
    </>
  );
}

// ---- The Triage Room: one staff-only screen, one ticket per case (it replaces the Voice triage and ER Desk
// screens). The role switch shows each person their own next action; the server checks who may press what.
const RANKS = { TIER_3_GREEN: 0, TIER_2_AMBER: 1, TIER_1_RED: 2 };
const STATES = [['IN_DIGEST', 'In digest'], ['OPEN', 'Open'], ['ACKNOWLEDGED', 'Acknowledged'], ['OUTCOME_LOGGED', 'Outcome logged'],
  ['CLOSURE_PROPOSED', 'Closure proposed'], ['CLOSED', 'Closed'], ['REOPENED', 'Reopened']];
const ROLE_CARDS = [['ai', 'AI'], ['doctor', 'Doctor'], ['nurse', 'Nurse'], ['coordinator', 'Coordinator']];
const VIEWS = [['coordinator', 'Coordinator'], ['doctor', 'Doctor'], ['nurse', 'Nurse (ER Desk)'], ['ai', 'AI']];
// The screen is rebuilt after every action: the open ticket, the view and the tab are kept here.
const room = { user: null, open: null, view: null, tab: 'needs' };

export function myView(role, userId, cfg) {
  if (role === 'oncologist') return 'doctor';
  if (role === 'er_desk') return userId && userId === cfg?.er_doctor ? 'doctor' : 'nurse';
  return 'coordinator';
}

// Does this ticket wait on this role? The doctor card is the ER doctor's on Red and the oncologists' otherwise.
export function waitsOn(ep, view, role) {
  if (ep.status !== 'open') return false;
  if (view === 'doctor' && role === 'er_desk' && ep.tier !== 'TIER_1_RED') return false;
  if (view === 'doctor' && role === 'oncologist' && ep.tier === 'TIER_1_RED') return false;
  if (view === 'coordinator' && (['due', 'in_call'].includes(ep.call_status) || ep.followup_due)) return true;
  const c = ep.next?.[view];
  return !!c && !c.done && c.text !== 'Nothing';
}

function countdown(ep) {
  if (!ep.due) return null;
  const mins = Math.round((new Date(ep.due.at) - new Date(ep.now)) / 60000);
  const abs = Math.abs(mins);
  const span = abs >= 1440 ? `${Math.floor(abs / 1440)} d ${Math.floor((abs % 1440) / 60)} h`
    : abs >= 60 ? `${Math.floor(abs / 60)} h ${abs % 60} min` : `${abs} min`;
  return { late: mins < 0, text: mins < 0 ? `overdue ${span}` : `in ${span}` };
}

function Countdown({ ep }) {
  const c = countdown(ep);
  if (!c) return <span className="small-note">{ep.status === 'closed' ? 'Closed' : '—'}</span>;
  return (
    <span className={`vt-due ${c.late ? 'late' : ''}`} title={`${ep.due.what} by ${when(ep.due.at)} (demo clock)`}>
      ⏱ {c.text}<span className="vt-due-what"> · {ep.due.what} by {hm(ep.due.at)}</span></span>
  );
}

const mmss = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

function TierChange({ ep, cfg }) {
  const ui = useUI();
  const [to, setTo] = useState('');
  const [reason, setReason] = useState('');
  const [run, error, busy] = useRun();
  if (!['coordinator', 'oncologist', 'er_desk'].includes(ui.role)) return null;
  const canLower = ep.tier === 'TIER_1_RED' ? ui.user?.id === cfg?.er_doctor : ui.role === 'oncologist';
  const options = Object.keys(TIER).filter((t) => t !== ep.tier && (RANKS[t] > RANKS[ep.tier] || canLower));
  return (
    <div className="vt-tierchg">
      <div className="small-note"><b>Changing the tier:</b> {ep.tier_rule}</div>
      {options.length > 0 ? (
        <div className="btn-row">
          <select className="v3-in" style={{ width: 'auto' }} value={to} onChange={(e) => setTo(e.target.value)} aria-label="Change tier">
            <option value="">Change tier…</option>
            {options.map((t) => <option key={t} value={t}>{RANKS[t] > RANKS[ep.tier] ? 'Raise' : 'Lower'} to {TIER[t][0]}</option>)}
          </select>
          <input className="v3-in" style={{ flex: 1, minWidth: 140 }} placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button type="button" className="btn sm" disabled={busy || !to || !reason.trim()}
            onClick={() => run(() => post(`triage/episodes/${ep.episode_id}/tier`, { tier: to, reason }), `Tier ${RANKS[to] > RANKS[ep.tier] ? 'raised' : 'lowered'} to ${TIER[to][0]}, with the reason recorded`)}>Change tier</button>
        </div>
      ) : <div className="small-note">Red is lowered only by the ER doctor.</div>}
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function NextCard({ k, label, ep, cfg, selected, view }) {
  const ui = useUI();
  const c = ep.next?.[k] || { text: 'Nothing', done: true };
  const role = ui.role;
  const isErDoctor = ui.user?.id === cfg?.er_doctor;
  const open = ep.status === 'open';
  const red = ep.tier === 'TIER_1_RED';
  let actions = null;
  if (open && k === 'ai' && role === 'coordinator' && (['due', 'in_call'].includes(ep.call_status) || ep.followup_due)) {
    actions = <div className="btn-row"><button type="button" className="btn sm primary" onClick={() => openMessages(ui, ep)}>Open the phone simulator</button></div>;
  }
  if (open && k === 'doctor') {
    if (role === 'oncologist' && ep.tier === 'TIER_2_AMBER') actions = <><div className="btn-row"><Ack ep={ep} /></div><DoctorActions ep={ep} /></>;
    if (red && role === 'er_desk') {
      const logged = ['OUTCOME_LOGGED', 'CLOSURE_PROPOSED'].includes(ep.state);
      actions = (
        <>
          {!ep.er_log && <NoteAction label="Log what was done" path="er-log" ep={ep} placeholder="What was done (e.g. seen in ED, admitted)" done="Outcome logged" />}
          {isErDoctor && logged
            ? <NoteAction label="Close case" path="er-close" ep={ep} tone="primary" placeholder="ER doctor's closing note" done="Red case closed by the ER doctor" />
            : <div className="btn-row"><button type="button" className="btn sm" disabled title={isErDoctor ? 'Acknowledge the alert and log what was done first' : 'Only the ER doctor can close a Red case'}>Close case</button>
              <span className="small-note">{isErDoctor ? 'after the alert is acknowledged and the outcome logged' : 'only the ER doctor closes a Red case'}</span></div>}
        </>
      );
    }
  }
  if (open && k === 'nurse' && role === 'er_desk') {
    actions = (
      <>
        {red && !ep.er_ack_by && <div className="btn-row"><Simple label="Acknowledge the alert" path="acknowledge" ep={ep} done="Acknowledged" /></div>}
        {ep.tier === 'TIER_2_AMBER' && <div className="btn-row"><Ack ep={ep} /></div>}
        {ep.nurse_asked && !ep.nurse_callback && <NoteAction label="Record nurse callback" path="nurse-callback" ep={ep} placeholder="What the nurse found on the call" done="Nurse callback recorded" />}
        {red && ep.red_kind === 'UNCONFIRMED' && !ep.package_sent_at && <div className="btn-row"><Simple label="Send emergency package" path="send-package" ep={ep} tone="warn"
          done="Emergency package sent: family message with the PDF, doctor note, coordinator task" /></div>}
      </>
    );
  }
  if (k === 'coordinator' && role === 'coordinator') {
    if (open && red) {
      actions = (
        <>
          {ep.task?.status === 'open' && <EdCallForm ep={ep} />}
          {ep.red_kind === 'UNCONFIRMED' && !ep.package_sent_at && <div className="btn-row"><Simple label="Send emergency package" path="send-package" ep={ep} tone="warn"
            done="Emergency package sent: family message with the PDF, doctor note, coordinator task" /></div>}
        </>
      );
    } else if (open && ep.tier === 'TIER_2_AMBER') {
      actions = (
        <>
          <div className="btn-row">
            <Ack ep={ep} />
            {ep.doctor_order?.card_id && <button type="button" className="btn sm" onClick={() => ui.nav('plan', ep.mrn)}>Open her plan to book</button>}
          </div>
          <ConfirmClosure ep={ep} />
        </>
      );
    } else if (open && ep.tier === 'TIER_3_GREEN' && !ep.digest_ack_by) {
      actions = (
        <>
          <div className="btn-row"><Simple label="Acknowledge the digest" path="digest-ack" ep={ep} tone="primary" done="Digest acknowledged" /></div>
          <NoteAction label="Reopen as Amber" path="reopen" ep={ep} placeholder="Reason to reopen (it becomes an open Amber ticket)" done="Reopened: now an open Amber ticket" />
        </>
      );
    }
  }
  // Any staff reopens a closed ticket, with a reason (on their own card).
  if (!open && k === view && ['coordinator', 'oncologist', 'er_desk'].includes(role)) {
    actions = <NoteAction label="Reopen" path="reopen" ep={ep} placeholder="Reason to reopen" done="Ticket reopened" />;
  }
  const nothing = c.text === 'Nothing';
  return (
    <div className={`vt-ncard ${selected ? 'mine' : ''} ${c.done && !nothing ? 'done' : ''}`}>
      <div className="vt-nrole">{label}</div>
      <div className={nothing ? 'small-note' : ''}>{c.done && !nothing ? '✓ ' : ''}{c.text}</div>
      {c.due_at && !c.done && <div className="small-note">Due {hm(c.due_at)}</div>}
      {actions}
    </div>
  );
}

function Ticket({ id, view, cfg }) {
  const ui = useUI();
  const [ep, setEp] = useState(null);
  const [src, setSrc] = useState(null);
  const [recBusy, setRecBusy] = useState(false);
  const [recErr, setRecErr] = useState('');
  const player = useRef(null);
  const seekTo = useRef(null);
  const stopAt = useRef(null);
  useEffect(() => { api(`triage/episodes/${id}`).then(setEp).catch((e) => setRecErr(e.message)); }, [id]);
  useEffect(() => () => { if (src) URL.revokeObjectURL(src); }, [src]);
  if (!ep) return <div className="empty">{recErr || 'Loading…'}</div>;

  const openRecording = async () => {
    setRecBusy(true);
    setRecErr('');
    try {
      setSrc(await blobUrl(`triage/episodes/${id}/recording`));
      const fresh = await api(`triage/episodes/${id}`);  // where each answer starts in the recording
      setEp(fresh);
      return fresh;
    } catch (e) {
      setRecErr(e.message);
      return null;
    } finally {
      setRecBusy(false);
    }
  };
  // A summary line plays just that question and her answer, then stops (testing round 31 Oct, defect 3). Pressing
  // the player's own play button afterwards plays on through the call as before.
  const jump = async (turn) => {
    let spans = ep.rec_spans;
    if (!src) {
      const fresh = await openRecording();
      if (!fresh) return;
      spans = fresh.rec_spans;
    }
    const span = spans?.[String(turn)];
    if (!span) return;
    stopVoice();
    stopAt.current = span[1];
    const a = player.current;
    if (a && a.readyState >= 1) {
      a.currentTime = span[0];
      a.play().catch(() => {});
    } else seekTo.current = span[0];
  };
  const onLoaded = () => {
    if (seekTo.current === null || !player.current) return;
    player.current.currentTime = seekTo.current;
    seekTo.current = null;
    player.current.play().catch(() => {});
  };
  const onTime = () => {
    const a = player.current;
    if (a && stopAt.current !== null && a.currentTime >= stopAt.current) {
      a.pause();
      stopAt.current = null;
    }
  };
  const onSeek = () => {  // moved past the answer by hand: play on
    if (player.current && stopAt.current !== null && player.current.currentTime > stopAt.current) stopAt.current = null;
  };

  return (
    <div className="vt-ticket">
      <div className="vt-thead">
        <div>
          <div className="vt-tid">{ep.ticket_id}</div>
          <div><PatientLink mrn={ep.mrn} name={ep.full_name} />
            <span className="v4-meta"> · {ep.mrn} · {ep.preferred_language} · opened {when(ep.opened_at)}</span></div>
        </div>
        <div className="vt-chipbig">{ep.tier ? <TierPill ep={ep} /> : <Pill tone="brand">Call {ep.call_status === 'due' ? 'due' : 'in progress'}</Pill>}
          {' '}<span className={`vt-state s-${ep.state}`}>{ep.state_label}</span></div>
        <Countdown ep={ep} />
      </div>
      {ep.owner && <div className="small-note">Waiting on: <b>{ep.owner}</b>{ep.state === 'REOPENED' ? ` · reopened ${when(ep.reopened_at)}` : ''}</div>}
      <PlanChips ep={ep} />
      <Previous ep={ep} />
      {ep.status === 'closed' && <div className="small-note">Closed by {ui.name(ep.closed_by)} {when(ep.closed_at)}: “{ep.close_note}”</div>}
      {ep.proposed && <div className="vt-proposed">AI proposes (a person decides): {ep.proposed.replace(/^Proposed: /, '')}</div>}
      {ep.cover_alerted_at && ep.status === 'open' && <div className="vt-proposed late">Nobody acted on this Amber ticket in {cfg?.cover_alert_minutes || 90} minutes: the cover ({ep.cover_task?.owner_name}) was alerted at {hm(ep.cover_alerted_at)}.</div>}
      <div className="grid-2 vt-tgrid">
        <div>
          <div className="card-title">Conversation</div>
          {ep.has_call ? (
            src ? (
              <div className="vt-rec">
                <audio ref={player} controls preload="auto" src={src} onLoadedMetadata={onLoaded} onPlay={() => stopVoice()}
                  onTimeUpdate={onTime} onSeeking={onSeek} />
                <div className="btn-row"><a className="btn sm ghost" href={src} download={`${ep.ticket_id}.wav`}>⬇ Download the recording</a></div>
              </div>
            ) : <div className="btn-row"><button type="button" className="btn sm" disabled={recBusy} onClick={openRecording}>{recBusy ? 'Preparing the recording…' : '▶ Call recording'}</button></div>
          ) : <div className="small-note">No call audio yet.</div>}
          {recErr && <div className="error">{recErr}</div>}
          <div className="small-note">One file, in call order: the AI's questions and her answers. ♪ a short tone = an answer given on screen. Her language and the English translation:</div>
          {ep.turns.length > 0 && <Turns ep={ep} />}
          {/* Her voice notes are for the coordinator and doctors (the ER Desk has the call recording). */}
          {ui.role !== 'er_desk' && <PatientNotes ep={ep} />}
        </div>
        <div>
          <div className="card-title">Summary <span className="small-note">facts from her answers and the record; no assessment</span></div>
          {ep.summary.length === 0 ? <div className="small-note">No answers yet.</div> : (
            <ul className="vt-sum">
              {ep.summary.map((l, i) => (
                <li key={i}>
                  {l.turn !== null && ep.has_call
                    ? <button type="button" className="vt-jump" title="Play this question and her answer" onClick={() => jump(l.turn)}>▶{ep.rec_spans?.[String(l.turn)] ? ` ${mmss(ep.rec_spans[String(l.turn)][0])}–${mmss(ep.rec_spans[String(l.turn)][1])}` : ''}</button>
                    : <span className="tag">{l.source === 'hospital record' ? 'record' : 'answer'}</span>}
                  {' '}{l.text}
                </li>
              ))}
            </ul>
          )}
          {ep.tier && ep.not_asked?.length > 0 && <div className="small-note">Not asked: {ep.not_asked.join(', ')}.</div>}
          <div className="small-note"><i>Built from her answers. Not a diagnosis.</i></div>
          <div className="card-title">Triage status</div>
          {ep.result ? <WhyPanel ep={ep} /> : <div className="small-note">The call has not finished: no tier yet.</div>}
          {ep.tier_set_by && <div className="small-note">Set by: <b>{ep.tier_set_by}</b></div>}
          {ep.er_log && <div className="small-note">Logged by {ui.name(ep.er_log.by)} at {ep.er_log.at.slice(11, 16)}: “{ep.er_log.note}”</div>}
          {ep.tier_changes.length > 1 && <div className="small-note">Earlier changes: {ep.tier_changes.slice(0, -1).map((c) => `${TIER[c.from][0]} → ${TIER[c.to][0]} by ${ui.name(c.by)} (“${c.reason}”)`).join(' · ')}</div>}
          {ep.status === 'open' && ep.tier && <TierChange ep={ep} cfg={cfg} />}
        </div>
      </div>
      <div className="card-title">Next action</div>
      <div className="vt-next">
        {ROLE_CARDS.map(([k, l]) => <NextCard key={k} k={k} label={l} ep={ep} cfg={cfg} selected={k === view} view={view} />)}
      </div>
      <div className="btn-row">
        {ep.doctor_note && <button type="button" className="btn sm ghost" onClick={() => ui.showModal('Doctor note', ep.ticket_id, <div className="vt-note">{ep.doctor_note}</div>)}>Doctor note</button>}
        {ep.pdf_doc_id && <button type="button" className="btn sm ghost" onClick={() => openPdf(ep.pdf_doc_id)}>Emergency summary PDF</button>}
        {['oncologist', 'er_desk'].includes(ui.role) && <button type="button" className="btn sm ghost" onClick={() => openBrief(ui, ep.mrn)}>Pre-OPD brief</button>}
      </div>
    </div>
  );
}

// Testing round 31 Oct, defect 1: the question bank the calls are built from, with the rules that pick the questions.
function QuestionBank() {
  const [bank, setBank] = useState(null);
  const [error, setError] = useState('');
  const [lang, setLang] = useState('Tamil');
  useEffect(() => { api('triage/questions').then(setBank).catch((e) => setError(e.message)); }, []);
  if (!bank) return <div className="card"><div className="empty">{error || 'Loading…'}</div></div>;
  return (
    <div className="card">
      <div className="card-title">Question bank ({bank.questions.length} questions)</div>
      <ul className="vt-sum">{bank.rules.map((r) => <li key={r}>{r}</li>)}</ul>
      <div className="seg" role="group" aria-label="Language" style={{ margin: '8px 0' }}>
        {['Tamil', 'Hindi', 'Telugu'].map((l) => <button key={l} type="button" className={lang === l ? 'on' : ''} onClick={() => setLang(l)}>{l}</button>)}
      </div>
      <table className="vt-table">
        <thead><tr><th>#</th><th>Question</th><th>Module (asked when)</th><th>Answer</th><th>Rules</th></tr></thead>
        <tbody>
          {bank.questions.map((q, i) => (
            <tr key={q.id}>
              <td className="small-note">{i + 1}<div>{q.id}</div></td>
              <td>{q.text.English}<div className="small-note v31-kn">{q.text[lang]}</div></td>
              <td>{q.module_label}</td>
              <td className="small-note">{{ yesno: 'yes / no', number: 'a number', temperature: 'temperature or “no thermometer”' }[q.kind]}</td>
              <td className="small-note">
                {q.danger && <><span className="tag live">Danger</span> </>}
                {q.history && <><span className="tag hosp">Uses her record</span> </>}
                {q.fixed_12 && <><span className="tag">Fixed 12</span> </>}
                {q.skip && <div>{q.skip}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small-note" style={{ marginTop: 8 }}>&lt;…&gt; is filled from her record (the day she last reported a headache). Tamil, Hindi and Telugu are drafts until Dr. Sekhar signs them.</div>
    </div>
  );
}

export function TriageRoom() {
  const ui = useUI();
  const [list, reload, error] = useEpisodes('open');
  const [closed] = useEpisodes('closed');
  const [cfg, setCfg] = useState(null);
  useEffect(() => { api('demo/settings').then((s) => setCfg(s.voice_triage)).catch(() => {}); }, []);
  // The countdowns follow the demo clock: look again now and then.
  useEffect(() => { const t = setInterval(reload, 15000); return () => clearInterval(t); }, [reload]);
  if (room.user !== ui.user?.id) Object.assign(room, { user: ui.user?.id, open: null, view: null, tab: 'needs' });
  const mine = myView(ui.role, ui.user?.id, cfg);
  const [view, setViewState] = useState(room.view);
  const [tab, setTabState] = useState(room.tab);
  const [open, setOpenState] = useState(room.open);
  const [colour, setColour] = useState('');
  const [stateF, setStateF] = useState('');
  const shown = view || mine;
  const setView = (v) => { room.view = v; setViewState(v); };
  const setTab = (t) => { room.tab = t; setTabState(t); };
  const toggle = (id) => { const next = open === id ? null : id; room.open = next; setOpenState(next); };
  if (!list) return <div className="card"><div className="empty">{error || 'Loading…'}</div></div>;
  const doctor = ui.role === 'oncologist';
  const senior = doctor && cfg && ui.user?.id === cfg.senior_oncologist;
  const red = list.filter((e) => e.tier === 'TIER_1_RED');
  const needs = list.filter((e) => waitsOn(e, shown, ui.role));
  const green = list.filter((e) => e.tier === 'TIER_3_GREEN');
  // Most urgent first: Red, Amber, Green (a call still running counts as Red), then the nearest due time.
  const urgency = (e) => -(e.tier ? RANKS[e.tier] : 2);
  const byUrgency = (xs) => [...xs].sort((a, b) => urgency(a) - urgency(b) || String(a.due?.at || '~').localeCompare(String(b.due?.at || '~')));
  const pick = (xs) => xs.filter((e) => (!colour || e.tier === colour) && (!stateF || e.state === stateF));
  const rows = pick({ needs: byUrgency(needs), open: byUrgency(list), digest: byUrgency(green), closed: closed || [] }[tab] || []);
  const mrns = [...new Map([...list, ...(closed || [])].map((e) => [e.mrn, e])).values()];
  const viewLabel = VIEWS.find(([k]) => k === shown)?.[1];
  return (
    <>
      <ScreenHead eyebrow={`Staff only · ${senior ? 'Senior Oncologist' : doctor ? (ui.user?.id === cfg?.doctor_on_duty ? 'Doctor on Duty' : 'Oncologist') : ui.role === 'er_desk' ? 'ER Desk' : 'Care coordinator'} · ${ui.user?.person || ''}`}
        title="Triage Room" sub="One ticket per case. The rules set Red, Amber or Green; people decide care and close the ticket. The patient never sees this screen." />
      <Synthetic />
      {(doctor || ui.role === 'er_desk') && red.map((e) => (
        <div key={e.episode_id} className="vt-banner">RED {e.ticket_id}: {e.full_name} ({e.mrn}) · {KIND[e.red_kind]} · {e.result?.why?.join('; ')}
          <button type="button" className="btn sm ghost" onClick={() => { setTab('open'); room.open = e.episode_id; setOpenState(e.episode_id); }}>Open ticket</button></div>
      ))}
      <div className="vt-room-bar">
        <div className="seg" role="group" aria-label="Role">
          {VIEWS.map(([k, l]) => <button key={k} type="button" className={shown === k ? 'on' : ''} onClick={() => setView(k)}>{l}{k === mine ? ' (you)' : ''}</button>)}
        </div>
        <div className="seg" role="group" aria-label="Tickets">
          {[['needs', `Needs ${viewLabel} (${needs.length})`], ['open', `All open (${list.length})`], ['digest', `Green digest (${green.length})`],
            ['closed', `Closed (${closed?.length || 0})`], ...(doctor ? [['brief', 'Pre-OPD briefs']] : []), ['bank', 'Question bank']].map(([k, l]) => (
            <button key={k} type="button" className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>))}
        </div>
        <select className="v3-in" style={{ width: 'auto' }} value={colour} onChange={(e) => setColour(e.target.value)} aria-label="Filter by colour">
          <option value="">All colours</option>
          {Object.entries(TIER).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <select className="v3-in" style={{ width: 'auto' }} value={stateF} onChange={(e) => setStateF(e.target.value)} aria-label="Filter by state">
          <option value="">All states</option>
          {STATES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      {tab === 'bank' ? <QuestionBank /> : tab === 'brief' ? (
        <div className="card">
          <div className="card-title">Pre-OPD briefs</div>
          {mrns.map((e) => <div key={e.mrn} className="btn-row"><button type="button" className="btn sm" onClick={() => openBrief(ui, e.mrn)}>{e.full_name} · {e.mrn}</button></div>)}
          {mrns.length === 0 && <div className="small-note">No triage tickets yet.</div>}
        </div>
      ) : (
        <div className="card vt-room">
          {rows.length === 0 ? <div className="empty">{tab === 'needs' ? `Nothing waiting for the ${viewLabel.toLowerCase()}.` : 'No tickets.'}</div> : (
            <table className="vt-table vt-rows">
              <thead><tr><th>Ticket</th><th>Patient</th><th>Time</th><th>Tier · state</th><th>Due</th><th>Owner</th><th>Next action · {viewLabel}</th></tr></thead>
              <tbody>
                {rows.map((e) => (
                  <React.Fragment key={e.episode_id}>
                    <tr className={`vt-row ${open === e.episode_id ? 'on' : ''}`} onClick={() => toggle(e.episode_id)}
                      tabIndex={0} onKeyDown={(k) => { if (k.key === 'Enter') toggle(e.episode_id); }}>
                      <td className="vt-tid">{e.ticket_id}</td>
                      <td><span onClick={(k) => k.stopPropagation()}><PatientLink mrn={e.mrn} name={e.full_name} /></span><div className="small-note">{e.mrn} · {e.preferred_language}</div></td>
                      <td>{hm(e.result_at || e.opened_at)}<div className="small-note">{fmt(String(e.opened_at).slice(0, 10))}</div></td>
                      <td className="vt-chipbig">{e.tier ? <TierPill ep={e} /> : <Pill tone="brand">Call</Pill>}<div className={`vt-state s-${e.state}`}>{e.state_label}</div></td>
                      <td>{e.status === 'closed' ? <span className="small-note">Closed {when(e.closed_at)}</span> : <Countdown ep={e} />}</td>
                      <td className="small-note">{e.owner || '—'}</td>
                      <td>{e.status === 'closed' ? <span className="small-note">{e.close_note}</span>
                        : ['due', 'in_call'].includes(e.call_status) && shown === 'coordinator' ? 'Run the call in the phone simulator'
                          : e.next?.[shown]?.text}</td>
                    </tr>
                    {open === e.episode_id && <tr className="vt-open"><td colSpan={7}><Ticket id={e.episode_id} view={shown} cfg={cfg} /></td></tr>}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </>
  );
}
