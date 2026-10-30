// Voice Triage MVP-lite (CarePilot_Voice_Triage_MVP_Lite_v1.1): the call pane in the phone simulator, the "Why this
// tier" panel, the coordinator's and the doctor's Voice triage screens, the ER Desk and the Pre-OPD brief.
// The call is simulated: Bulbul speaks the fixed Tamil lines (text only in simulated mode), the tester answers by
// voice, words or on-screen buttons, and plain rules give Red, Amber or Green. Every screen says SYNTHETIC DEMO.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, blobUrl, post, upload } from './api.js';
import { openMessages, toWav } from './Modals.jsx';
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

export function Synthetic() {
  return (
    <div className="vt-synth">SYNTHETIC DEMO · Voice Triage MVP-lite · protocol DEMO_LITE · DRAFT_UNSIGNED — thresholds and Tamil
      lines are clinician-reviewed drafts until Dr. Sekhar signs them. AI listens, code decides, people decide care.</div>
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
        {ep.tier !== r.tier && <span className="small-note">· now {TIER[ep.tier][0]} (changed by a doctor)</span>}</div>
      <ul>{r.why.map((w) => <li key={w}>{w}</li>)}</ul>
      <div className="small-note">Rules: {r.reason_codes.join(', ') || 'none fired'} · action {r.action_code} ·
        {' '}{r.protocol_profile} / {r.protocol_status}{r.stopped_after_danger_sign ? ' · the call stopped at the first danger sign' : ''}.
        Every line is a fixed sentence from a rule and her stored history; no AI opinion.</div>
    </div>
  );
}

function AnswerTable({ table }) {
  return (
    <table className="vt-table">
      <thead><tr><th>#</th><th>Question</th><th>Value</th></tr></thead>
      <tbody>
        {table.map((q) => (
          <tr key={q.id} className={q.asked ? '' : 'off'}>
            <td>{q.id.slice(1)}{q.danger && <span className="vt-danger" title="Red question: an unclear answer gives Red (unconfirmed)">!</span>}</td>
            <td>{q.en}{q.history && <span className="tag">history</span>}</td>
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

function Turns({ ep, tamil = true }) {
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
              {tamil && t.ta && <span className="v31-kn">{t.ta}</span>}
              {(t.en && (!tamil || t.en !== t.ta)) && <div className="vt-en">{t.en}</div>}
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
      {!ep && <div className="small-note">No call yet. Send a voice note about her health (e.g. TA-01 below): the fixed acknowledgement goes out and the call is due within 2 minutes.</div>}
      {ep && (
        <div className="small-note">Her message: “{ep.translation || ep.patient_words}” · consent: WhatsApp, voice call and recording, family updates (lite profile).
          <label style={{ marginLeft: 8 }}><input type="checkbox" checked={voice} onChange={(e) => { if (!e.target.checked) stopVoice(); setVoice(e.target.checked); }} /> Play Bulbul voice{provider === 'fake' ? ' (simulated mode: text only)' : ''}</label></div>
      )}
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
          <div className="small-note">Question {q.id.slice(1)}{q.history && <span className="tag">history</span>}{q.danger && <span className="tag">Red question</span>}</div>
          <div className="v31-kn vt-qta">{q.ta}</div>
          <div className="vt-en">{q.en}</div>
          <div className="btn-row">
            {q.kind === 'yesno' && [['YES', 'Yes · ஆம்'], ['NO', 'No · இல்லை'], ['UNKNOWN', "Don't know · தெரியவில்லை"]].map(([v, l]) => (
              <button key={v} type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: v })}>{l}</button>))}
            {q.kind === 'number' && NUMBERS.map((n) => <button key={n} type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: n })}>{n}</button>)}
            {q.kind === 'number' && <button type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: 'UNKNOWN' })}>Don't know</button>}
            {q.kind === 'temperature' && TEMPS.map((n) => <button key={n} type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: n })}>{n} °F</button>)}
            {q.kind === 'temperature' && <button type="button" className="btn sm" disabled={busy} onClick={() => act('answer', { value: 'NO_THERMOMETER' })}>No thermometer</button>}
          </div>
          <div className="btn-row">
            <input className="v3-in" style={{ flex: 1, minWidth: 160 }} value={text} placeholder="Or type what she says (Tamil or English)"
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
          <button type="button" className="btn sm" disabled={busy} title="The guide's scripted Tamil answers go through the same parsing and rules"
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
            <div className="v31-kn">வணக்கம். … இன்று எப்படி இருக்கிறீர்கள்: நேற்றை விட நன்றாக, அதே போல, அல்லது மோசமாக?</div>
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
          {ep.inbound_id && <><div className="small-note">Her voice note:</div><VoiceClip inboundId={ep.inbound_id} /></>}</div>
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
        <a role="button" tabIndex={0} onClick={() => ui.nav('plan', ep.mrn)}>{ep.full_name}</a>{' '}
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

function RedCard({ ep, role, erDoctor }) {
  const ui = useUI();
  return (
    <div className="card card-accent a-bad">
      <Head ep={ep}>{ep.task_overdue && <Pill tone="bad">Overdue</Pill>}</Head>
      {ep.doctor_note ? <div className="vt-note">{ep.doctor_note}</div>
        : <div className="small-note" style={{ marginTop: 6 }}>{ep.result.why.join(' · ')}. No family message sent: a nurse calls back by <b>{hm(ep.callback_due_at)}</b>.</div>}
      {ep.nurse_callback && <div className="small-note">Nurse callback ({ui.name(ep.nurse_callback.by)}, {hm(ep.nurse_callback.at)}): “{ep.nurse_callback.note}”</div>}
      {ep.ed_calls.length > 0 && <div className="small-note">Calls to the family: {ep.ed_calls.map((c, i) => `${i + 1}) ${hm(c.at)} ${ui.name(c.by)} — ${c.outcome.replace('_', ' ')}${c.hospital ? ` (${c.hospital})` : ''}`).join(' · ')}{ep.doctor_told_at ? ' · Doctor on Duty told' : ''}</div>}
      {ep.er_ack_by && <div className="small-note">Acknowledged at the ER Desk by {ui.name(ep.er_ack_by)} at {hm(ep.er_ack_at)}.</div>}
      <Sources ep={ep} brief={role === 'oncologist'} />
      {role === 'coordinator' && ep.task?.status === 'open' && (
        <div className="vt-task"><b>Task (due {hm(ep.task.due_at_demo)}):</b> {ep.task.reason}<EdCallForm ep={ep} /></div>
      )}
      {['coordinator', 'er_desk'].includes(role) && ep.red_kind === 'UNCONFIRMED' && !ep.package_sent_at && (
        <div className="btn-row"><Simple label="Send emergency package" path="send-package" ep={ep} tone="warn"
          done="Emergency package sent: family message with the PDF, doctor note, coordinator task" /></div>
      )}
      {role === 'er_desk' && (
        <>
          {!ep.er_ack_by && <div className="btn-row"><Simple label="Acknowledge" path="acknowledge" ep={ep} done="Acknowledged" /></div>}
          {ep.result?.action_code === 'NURSE_CALLBACK_15_MIN' && !ep.nurse_callback && (
            <NoteAction label="Record nurse callback" path="nurse-callback" ep={ep} placeholder="What the nurse found on the call" done="Nurse callback recorded" />)}
          {ui.user?.id === erDoctor
            ? <NoteAction label="Close case" path="er-close" ep={ep} tone="primary" placeholder="ER doctor's closing note" done="Red case closed by the ER doctor" />
            : <div className="small-note">Only the ER doctor closes a Red case.</div>}
        </>
      )}
    </div>
  );
}

function Sbar({ ep }) {
  const h = ep.history || {};
  return (
    <div className="vt-sbar">
      <div><b>S</b> {ep.full_name} sent a message (“{ep.translation || ep.patient_words}”); the automated call gave {TIER[ep.tier][0]}.
        Callback by <b>{hm(ep.callback_due_at)}</b>{ep.callback_overdue ? <Pill tone="bad">2-hour clock passed</Pill> : null}.</div>
      {h.clinical && <div><b>B</b> {h.clinical.treatment_done.join('; ')}. Left arm on {fmt(h.arm_baseline.date)}: {h.arm_baseline.status}. Headache {h.last_headache.score}/10 on {fmt(h.last_headache.date)}. Advice: “{h.advice.en}”</div>}
      <div><b>Her answers</b> {ep.facts || '—'}</div>
      <div><b>Rules that fired</b> {ep.result.reason_codes.join(', ') || 'none'}</div>
      <div className="small-note">No assessment and no recommendation: the doctor decides the care.</div>
    </div>
  );
}

function DoctorActions({ ep }) {
  const [note, setNote] = useState('');
  const [tier, setTier] = useState('');
  const [reason, setReason] = useState('');
  const [run, error, busy] = useRun();
  const other = ep.tier === 'TIER_2_AMBER' ? 'TIER_3_GREEN' : 'TIER_2_AMBER';
  return (
    <>
      {ep.tier === 'TIER_2_AMBER' && !ep.doctor_order && (
        <div className="btn-row">
          <input className="v3-in" style={{ flex: 1, minWidth: 160 }} placeholder="Short note (optional), e.g. pause Neurobion Forte for 5 days" value={note} onChange={(e) => setNote(e.target.value)} />
          {Object.entries(ORDER).map(([k, l]) => (
            <button key={k} type="button" className={`btn sm ${k === 'none' ? '' : 'primary'}`} disabled={busy}
              onClick={() => run(() => post(`triage/episodes/${ep.episode_id}/order`, { order: k, note }), k === 'none' ? 'No order needed — recorded' : `${l}: order card created, booking message sent`)}>{l}</button>
          ))}
        </div>
      )}
      {ep.doctor_order && <div className="small-note">Order: <b>{ORDER[ep.doctor_order.order]}</b>{ep.doctor_order.note ? ` — “${ep.doctor_order.note}”` : ''}</div>}
      {ep.followup && <div className="small-note">Follow-up call: <b>{ep.followup.answer.toLowerCase()}</b> ({hm(ep.followup.at)})</div>}
      <div className="btn-row">
        <select className="v3-in" style={{ width: 'auto' }} value={tier} onChange={(e) => setTier(e.target.value)}>
          <option value="">{ep.tier === 'TIER_2_AMBER' ? 'Lower tier…' : 'Raise tier…'}</option>
          <option value={other}>{TIER[other][0]}</option>
        </select>
        <input className="v3-in" style={{ flex: 1, minWidth: 140 }} placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
        <button type="button" className="btn sm" disabled={busy || !tier || !reason.trim()}
          onClick={() => run(() => post(`triage/episodes/${ep.episode_id}/tier`, { tier, reason }), 'Tier changed, with the reason recorded')}>Change tier</button>
      </div>
      <NoteAction label="Confirm closure" path="confirm-closure" ep={ep} tone="primary"
        placeholder={ep.followup ? `Closing note (follow-up: ${ep.followup.answer.toLowerCase()})` : 'Closing note'} done="Closure confirmed; routine follow-up resumes" />
      {error && <div className="error">{error}</div>}
    </>
  );
}

export function TriageBoard() {
  const ui = useUI();
  const [tab, setTab] = useState('needs');
  const [list, , error] = useEpisodes('open');
  const [closed] = useEpisodes('closed');
  const [cfg, setCfg] = useState(null);
  useEffect(() => { api('demo/settings').then((s) => setCfg(s.voice_triage)).catch(() => {}); }, []);
  const role = ui.role;
  const doctor = role === 'oncologist';
  const senior = doctor && cfg && ui.user?.id === cfg.senior_oncologist;
  const onDuty = doctor && cfg && ui.user?.id === cfg.doctor_on_duty;
  if (!list) return <div className="card"><div className="empty">{error || 'Loading…'}</div></div>;
  const red = list.filter((e) => e.tier === 'TIER_1_RED');
  const amber = list.filter((e) => e.tier === 'TIER_2_AMBER');
  const green = list.filter((e) => e.tier === 'TIER_3_GREEN');
  const calls = list.filter((e) => ['due', 'in_call'].includes(e.call_status));
  const followups = list.filter((e) => e.followup_due);
  const mrns = [...new Map([...list, ...(closed || [])].map((e) => [e.mrn, e])).values()];
  return (
    <>
      <ScreenHead eyebrow={`${doctor ? (senior ? 'Senior Oncologist' : onDuty ? 'Doctor on Duty' : 'Oncologist') : 'Care coordinator'} · ${ui.user?.person || ''}`}
        title="Voice triage" sub="A health message brings an automated call; plain rules give Red, Amber or Green. Red goes to the ER Desk with the family message and PDF; Amber to the Doctor on Duty within 2 hours; Green gets the doctor's own sentence and a follow-up tomorrow." />
      <Synthetic />
      {doctor && red.length > 0 && red.map((e) => (
        <div key={e.episode_id} className="vt-banner">RED alert: {e.full_name} ({e.mrn}) · {KIND[e.red_kind]} · {e.result.why.join('; ')}
          {e.pdf_doc_id && <button type="button" className="btn sm ghost" onClick={() => openPdf(e.pdf_doc_id)}>PDF</button>}
          <button type="button" className="btn sm ghost" onClick={() => openBrief(ui, e.mrn)}>Pre-OPD brief</button></div>
      ))}
      <div className="seg" style={{ margin: '6px 0 12px' }}>
        {[['needs', `Needs ${doctor ? 'me' : 'you'} (${red.length + amber.length + calls.length + followups.length})`], ['digest', `Green digest (${green.length})`],
          ['closed', `Closed (${closed?.length || 0})`], ...(doctor ? [['brief', 'Pre-OPD briefs']] : [])].map(([k, l]) => (
          <button key={k} type="button" className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>))}
      </div>
      {tab === 'needs' && (
        <>
          {!doctor && calls.map((e) => (
            <div key={e.episode_id} className="card card-accent a-blue">
              <Head ep={e}><Pill tone="brand">Call {e.call_status === 'due' ? `due by ${hm(e.call_due_at)}` : 'in progress'}</Pill></Head>
              <div className="small-note">“{e.translation || e.patient_words}” — the automated call runs in the phone simulator.</div>
              <div className="btn-row"><button type="button" className="btn sm primary" onClick={() => openMessages(ui, e)}>Open the phone simulator</button></div>
            </div>
          ))}
          {red.map((e) => <RedCard key={e.episode_id} ep={e} role={role} erDoctor={cfg?.er_doctor} />)}
          {amber.map((e) => (
            <div key={e.episode_id} className="card card-accent a-warn">
              <Head ep={e}>{e.coord_ack_by && <Pill tone="gray">Acknowledged by {ui.name(e.coord_ack_by)}</Pill>}</Head>
              <WhyPanel ep={e} />
              <Sbar ep={e} />
              <Sources ep={e} brief={doctor} />
              {doctor ? <DoctorActions ep={e} /> : (
                <>
                  {e.doctor_order && <div className="small-note">Doctor's order: <b>{ORDER[e.doctor_order.order]}</b>{e.doctor_order.note ? ` — “${e.doctor_order.note}”` : ''}</div>}
                  {!e.coord_ack_by && <div className="btn-row"><Simple label="Acknowledge" path="acknowledge" ep={e} done="Acknowledged" /></div>}
                </>
              )}
            </div>
          ))}
          {followups.filter((e) => e.tier !== 'TIER_2_AMBER').map((e) => (
            <div key={`f${e.episode_id}`} className="card card-accent a-blue">
              <Head ep={e}><Pill tone="brand">Follow-up call due</Pill></Head>
              {!doctor && <div className="btn-row"><button type="button" className="btn sm primary" onClick={() => openMessages(ui, e)}>Open the phone simulator</button></div>}
            </div>
          ))}
          {red.length + amber.length + calls.length + followups.length === 0 && <div className="card"><div className="empty">Nothing waiting.</div></div>}
        </>
      )}
      {tab === 'digest' && (
        <div className="card">
          <div className="card-title">Green digest</div>
          {green.length === 0 && <div className="small-note">No Green calls open.</div>}
          {green.map((e) => (
            <div key={e.episode_id} className="v4-item">
              <Head ep={e} />
              <div className="d">{e.result.why[0]}{e.result.repeat_advice ? ' · the doctor’s signed sentence was repeated' : ''} · follow-up {fmt(e.followup_due_on)}{e.followup ? `: ${e.followup.answer.toLowerCase()}` : ''}</div>
              <Sources ep={e} brief={doctor} />
              {doctor && <DoctorActions ep={e} />}
            </div>
          ))}
        </div>
      )}
      {tab === 'closed' && (
        <div className="card">
          {(closed || []).length === 0 && <div className="small-note">No closed episodes.</div>}
          {(closed || []).map((e) => (
            <div key={e.episode_id} className="v4-item">
              <Head ep={e} />
              <div className="d">Closed by {ui.name(e.closed_by)} {when(e.closed_at)}: “{e.close_note}”</div>
              <Sources ep={e} brief={doctor} />
              {!doctor && e.tier !== 'TIER_1_RED' && <NoteAction label="Reopen" path="reopen" ep={e} placeholder="Reason to reopen" done="Episode reopened" />}
            </div>
          ))}
        </div>
      )}
      {tab === 'brief' && (
        <div className="card">
          <div className="card-title">Pre-OPD briefs</div>
          {mrns.map((e) => <div key={e.mrn} className="btn-row"><button type="button" className="btn sm" onClick={() => openBrief(ui, e.mrn)}>{e.full_name} · {e.mrn}</button></div>)}
          {mrns.length === 0 && <div className="small-note">No triage episodes yet.</div>}
        </div>
      )}
    </>
  );
}

export function ErDesk() {
  const ui = useUI();
  const [list, , error] = useEpisodes('open');
  const [closed] = useEpisodes('closed');
  const [cfg, setCfg] = useState(null);
  useEffect(() => { api('demo/settings').then((s) => setCfg(s.voice_triage)).catch(() => {}); }, []);
  if (!list) return <div className="card"><div className="empty">{error || 'Loading…'}</div></div>;
  const red = list.filter((e) => e.tier === 'TIER_1_RED');
  const nurse = list.filter((e) => e.tier === 'TIER_2_AMBER' && e.result?.action_code === 'NURSE_CALLBACK_15_MIN');
  const isErDoctor = ui.user?.id === cfg?.er_doctor;
  return (
    <>
      <ScreenHead eyebrow={`ER Desk · ${ui.user?.person || ''}`} title="Red alerts"
        sub="Facts-only alert cards with the doctor note and the summary PDF. Label CONFIRMED, NO ANSWER or UNCONFIRMED. Only the ER doctor closes a Red case." />
      <Synthetic />
      {red.length === 0 && <div className="card"><div className="empty">No Red alerts open.</div></div>}
      {red.map((e) => <RedCard key={e.episode_id} ep={e} role="er_desk" erDoctor={cfg?.er_doctor} />)}
      {nurse.length > 0 && <div className="card-title" style={{ margin: '10px 0 8px' }}>Nurse callbacks (she pressed 0)</div>}
      {nurse.map((e) => (
        <div key={e.episode_id} className="card card-accent a-warn">
          <Head ep={e} />
          <div className="small-note">Call back by <b>{hm(e.callback_due_at)}</b>. {e.result.why.join(' · ')}</div>
          <Sources ep={e} />
          {e.nurse_callback ? <div className="small-note">Recorded: “{e.nurse_callback.note}”</div>
            : <NoteAction label="Record nurse callback" path="nurse-callback" ep={e} placeholder="What the nurse found on the call" done="Nurse callback recorded" />}
        </div>
      ))}
      {(closed || []).filter((e) => e.tier === 'TIER_1_RED').length > 0 && (
        <div className="card">
          <div className="card-title">Closed Red cases</div>
          {closed.filter((e) => e.tier === 'TIER_1_RED').map((e) => (
            <div key={e.episode_id} className="v4-item"><Head ep={e} /><div className="d">Closed by {ui.name(e.closed_by)} {when(e.closed_at)}: “{e.close_note}”</div></div>
          ))}
        </div>
      )}
      {!isErDoctor && <div className="small-note">Viewing as the duty nurse: switch to the ER doctor to close a case.</div>}
    </>
  );
}
