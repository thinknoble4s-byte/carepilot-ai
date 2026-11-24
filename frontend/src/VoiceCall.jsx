// UC1-V voice escalation (spec v0.1): the SIMULATED CALL screen, its Workspace panel and the call records.
// The hospital's agent calls the patient. Both voices play by themselves: the agent speaks only library lines, the
// patient is a simulated voice (recorded clips). What the server decided is shown as it is spoken, never before.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api, blobUrl, post } from './api.js';
import { CallFace, startRingtone } from './CallPhone.jsx';
import { openMessages, WaPhone } from './Modals.jsx';
import { fmt, Pill, useUI } from './ui.jsx';

// documents/TechExceptions.xlsx: why the AI could not sort a reply (the reason code on the turn).
const FAULT = { ai_speech_failed: 'transcription failed', ai_translation_failed: 'translation failed',
  ai_sorter_failed: 'no answer from the AI in time', ai_invalid_label: 'the AI answer was not a valid label' };
const LABEL = { YES: ['YES', 'yes'], NO: ['NO', 'no'], HEALTH_CONCERN: ['HEALTH CONCERN', 'health'], UNCLEAR: ['UNCLEAR', 'unclear'] };
const OUTCOME = {
  booked: ['Booked', 'good'], declined: ['Declined after the explanation', 'warn'], safety_stop: ['Safety stop', 'bad'],
  no_answer: ['No answer', 'gray'], wrong_person: ['Patient not confirmed', 'warn'],
  slot_unsuitable: ['Slot does not suit: a person books', 'warn'], unclear: ['Reply unclear twice', 'warn'],
};
const APPROVAL = { DRAFT: 'DRAFT: language check', 'DRAFT-CLINICAL': 'DRAFT-CLINICAL: Dr. Sekhar to approve', APPROVED: 'Doctor-approved text' };
const outcomeOf = (c) => (c.reason_code === 'patient_rejected' ? ['Rejected by the patient', 'warn'] : OUTCOME[c.outcome]);
const hm = (ts) => (ts ? String(ts).slice(11, 16) : '');
const when = (ts) => (ts ? `${fmt(ts)} ${hm(ts)}` : '');
const weekday = (iso) => new Date(`${String(iso).slice(0, 10)}T00:00:00`).toLocaleDateString('en-IN', { weekday: 'short' });
const slotText = (day, at) => `${weekday(day)} ${fmt(day)}, ${String(at).slice(0, 5)}`;

// User, 22 Nov: a person takes a moment to think and react before answering the agent.
const THINK_MS = 3000;

// The sound of one call. stop() ends whatever is playing or waiting, and whoever awaited it sees a stale token.
function createPlayer() {
  let token = 0;
  let end = null;
  return {
    stop() {
      token += 1;
      end?.();
      return token;
    },
    live: (t) => t === token,
    wait(ms, t) {
      if (t !== token) return Promise.resolve();
      return new Promise((resolve) => {
        const timer = setTimeout(done, ms);
        function done() {
          clearTimeout(timer);
          end = null;
          resolve();
        }
        end = done;
      });
    },
    // Resolves true when the sound played to its end, false when there was none (the text is then given time).
    async play(urlPromise, t) {
      let url;
      try {
        url = await urlPromise;
      } catch (e) {
        return false;
      }
      if (t !== token) {
        URL.revokeObjectURL(url);
        return true;
      }
      const audio = new Audio(url);
      return new Promise((resolve) => {
        const done = (played) => {
          audio.onended = null;
          audio.onerror = null;
          audio.pause();
          URL.revokeObjectURL(url);
          end = null;
          resolve(played);
        };
        end = () => done(true);
        audio.onended = () => done(true);
        audio.onerror = () => done(false);
        audio.play().catch(() => done(false));
      });
    },
  };
}

function Gate({ checks, shown }) {
  return (
    <div className="vc-gate">
      <div className="vc-h">Safety gate before dialling</div>
      {checks.slice(0, shown).map((c) => (
        <div key={c.check} className={`vc-check ${c.ok ? 'ok' : 'fail'}`}>
          <b aria-hidden="true">{c.ok ? '✓' : '✕'}</b> {c.label}
          {!c.ok && <span className="small-note"> ({c.reason})</span>}
        </div>
      ))}
    </div>
  );
}

function Turn({ turn, patient, onPlay }) {
  const agent = turn.who === 'agent';
  const chip = LABEL[turn.label];
  return (
    <div className={`vc-turn ${turn.who}`}>
      <div className="vc-meta">
        <b>{agent ? 'Hospital agent' : `${patient} (simulated patient voice)`}</b>
        {agent && <span className="tag hosp">{turn.line_id}</span>}
        {agent && <span className={`tag ${turn.approval_status === 'APPROVED' ? 'verified' : 'unverified'}`}>{APPROVAL[turn.approval_status]}</span>}
        {chip && <span className={`vc-label ${chip[1]}`}>{chip[0]}</span>}
        {onPlay && <button type="button" className="vc-play" onClick={onPlay} aria-label={`Play turn ${turn.seq}`}>▶</button>}
      </div>
      <div className="vc-said v31-kn">{turn.text || (turn.ai_failed ? '(the AI could not hear this)' : '(no usable speech)')}</div>
      {turn.translation && <div className="vc-en">{agent ? 'English meaning' : 'English (AI translation)'}: {turn.translation}</div>}
      {turn.ai_failed && <div className="vc-en"><b>The AI could not sort this reply</b>{FAULT[turn.reason_code] ? ` (${FAULT[turn.reason_code]})` : ''}, so it counts as a health concern: the call stops and a person takes over.</div>}
    </div>
  );
}

function Undo({ callId, onDone }) {
  const ui = useUI();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const undo = async () => {
    try {
      await post(`voice/calls/${callId}/undo`, { reason });
      ui.toast("The agent's booking is undone: a task to book by phone was created");
      onDone?.();
      ui.refresh();
    } catch (e) {
      setError(e.message);
    }
  };
  if (!open) return <button type="button" className="btn sm" onClick={() => setOpen(true)}>Undo the agent's booking</button>;
  return (
    <span className="vc-undo">
      <input className="v3-in" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason for undoing"
        placeholder="Reason (your name is recorded)" />
      <button type="button" className="btn sm primary" disabled={!reason.trim()} onClick={undo}>Undo booking</button>
      {error && <span className="error">{error}</span>}
    </span>
  );
}

// Either a new call (escalationId, through the gate) or an existing one (callId: resumed, or its record).
export function VoiceCallScreen({ escalationId, callId, script = 's2' }) {
  const ui = useUI();
  const uiRef = useRef(ui);
  uiRef.current = ui;
  const player = useRef(null);
  if (!player.current) player.current = createPlayer();
  const [phase, setPhase] = useState('gate');
  const [gate, setGate] = useState({ checks: [], shown: 0 });
  const [call, setCall] = useState(null);
  const [upTo, setUpTo] = useState(0);
  const [speaking, setSpeaking] = useState(null);
  const [thread, setThread] = useState(null);
  const [error, setError] = useState('');
  const [reload, setReload] = useState(0);
  const [answering, setAnswering] = useState(false);
  const box = useRef(null);
  const callRef = useRef(null);
  const keep = (c) => {
    callRef.current = c;
    setCall(c);
  };

  const finish = useCallback(async (c) => {
    setSpeaking(null);
    setUpTo(c.turns.length);
    setPhase('ended');
    try {
      setThread(await api(`patients/${c.mrn}/messages`));
    } catch (e) {
      setError(e.message);
    }
  }, []);

  useEffect(() => {
    const p = player.current;
    const t = p.stop();
    const live = () => p.live(t);
    const coordinator = uiRef.current.role === 'coordinator';

    async function sound(urlPromise, chars) {
      if (await p.play(urlPromise, t)) return;
      if (!live()) return;
      await p.wait(Math.min(1200 + chars * 45, 11000), t);
    }

    async function converse(first, firstAudio) {
      let c = first;
      let audio = firstAudio;
      setPhase('live');
      while (live()) {
        const line = c.turns[c.turns.length - 1];
        const url = audio || blobUrl(`voice/calls/${c.call_id}/turns/${line.seq}/audio`);
        setUpTo(line.seq);
        setSpeaking('agent');
        await sound(url, line.text.length);
        if (!live()) return;
        if (c.status === 'ended') {
          await finish(c);
          uiRef.current.refresh();
          return;
        }
        setSpeaking('thinking');
        await p.wait(THINK_MS, t);
        if (!live()) return;
        // The patient's clip plays while the server hears and sorts the same clip.
        setSpeaking('patient');
        audio = null;
        const next = post(`voice/calls/${c.call_id}/reply`, { after_seq: line.seq }).then((r) => {
          if (live()) audio = blobUrl(`voice/calls/${r.call_id}/turns/${r.turns[r.turns.length - 1].seq}/audio`);
          return r;
        });
        next.catch(() => {});  // awaited below
        const clip = c.patient_next;
        if (clip?.has_audio) await sound(blobUrl(`voice/calls/${c.call_id}/clips/${clip.clip_id}/audio`), clip.chars);
        else await p.wait(1500, t);
        if (!live()) return;
        setSpeaking(null);
        c = await next;
        if (!live()) return;
        keep(c);
        setUpTo(c.turns.length - 1);  // what she said and its label, then the agent's line
        await p.wait(c.turns[c.turns.length - 2].label === 'HEALTH_CONCERN' ? 1600 : 800, t);
      }
    }

    async function run() {
      let c;
      const known = callRef.current?.call_id || callId;
      if (known) {
        c = await api(`voice/calls/${known}`);
      } else {
        const r = await post(`voice/escalations/${escalationId}/call`, { script });
        for (let i = 1; i <= r.gate.length && live(); i += 1) {
          setGate({ checks: r.gate, shown: i });
          await p.wait(330, t);
        }
        if (!live()) return;
        if (!r.call) {
          setPhase('blocked');
          return;
        }
        c = r.call;
        uiRef.current.refresh(); // the order card now offers Resume call
      }
      if (!live()) return;
      setGate({ checks: c.gate, shown: c.gate.length });
      keep(c);
      if (c.status === 'ended') {
        await finish(c);
      } else if (c.status === 'ringing') {
        setPhase('ringing'); // it rings until Accept, Reject or No answer
      } else if (!coordinator) {
        setUpTo(c.turns.length);
        setPhase('live');
      } else {
        await converse(c);
      }
    }

    run().catch((e) => {
      if (!live()) return;
      setSpeaking(null);
      setError(e.message);
    });
    return () => p.stop();
  }, [escalationId, callId, script, reload, finish]);

  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [upTo]);

  const coordinator = ui.role === 'coordinator';
  const ringing = phase === 'ringing' && coordinator && !answering;
  useEffect(() => (ringing ? startRingtone() : undefined), [ringing]);

  const answerPhone = async (how) => {
    setAnswering(true); // the ringtone stops at once
    setError('');
    try {
      const c = await post(`voice/calls/${call.call_id}/${how}`);
      keep(c);
      if (how === 'answer') {
        setPhase('live');
        setReload((n) => n + 1); // the conversation starts with L1
      } else {
        await finish(c);
        ui.refresh();
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setAnswering(false);
    }
  };
  const replay = (turn) => {
    const t = player.current.stop();
    player.current.play(blobUrl(`voice/calls/${call.call_id}/turns/${turn.seq}/audio`), t).then((played) => {
      if (!played) ui.toast('No audio for this turn: the text is shown');
    });
  };

  const turns = (call?.turns || []).filter((t) => t.seq <= upTo);
  const said = (id) => turns.some((t) => t.line_id === id);
  const stop = turns.some((t) => t.label === 'HEALTH_CONCERN');
  const ended = phase === 'ended';
  const slot = said('L5') && !stop ? call.slot : null;
  const patient = call?.patient || { full_name: 'the patient', preferred_language: '' };
  const card = call?.card;
  const booked = card?.booking_source === 'voice_agent' && card.state === 'Booked' && (said('L6') || (ended && call.outcome === 'booked'));
  const outcome = ended && outcomeOf(call);

  return (
    <div className="vc">
      <div className="vc-left">
        <div className="vc-sim">SIMULATED CALL{ended ? ' · ended' : ''}</div>
        {ended && thread ? (
          <WaPhone thread={thread} patient={patient} settings={{ clinic_name: call?.caller || ui.clinic }} english coordinator={false} now={ui.now} />
        ) : (
          <CallFace phase={phase} clinic={call?.caller || ui.clinic || 'Hospital A'} speaking={speaking} stop={stop} now={ui.now}
            busy={answering} onAccept={coordinator ? () => answerPhone('answer') : null} onReject={() => answerPhone('reject')} />
        )}
      </div>
      <div className="vc-right">
        <div className="vc-top">
          <span className="vc-sim">SIMULATED CALL</span>
          <span className="small-note">No live telephony. Both voices are synthetic: the patient's is a stand-in, in real use a real person answers.</span>
        </div>
        {gate.checks.length > 0 && (upTo === 0 ? <Gate checks={gate.checks} shown={gate.shown} /> : (
          <details className="vc-gate-done"><summary>✓ Safety gate passed ({gate.checks.length} checks)</summary>
            <Gate checks={gate.checks} shown={gate.shown} /></details>
        ))}
        {phase === 'blocked' && <div className="vc-banner stop">No call: the safety gate did not pass. The reason is in the audit trail.</div>}
        {phase === 'ringing' && call && coordinator && (
          <>
            <div className="vc-hearing">Ringing on the patient's phone. The agent speaks only after Accept.</div>
            <div className="btn-row"><button type="button" className="btn sm" disabled={answering} onClick={() => answerPhone('no-answer')}>
              Simulate: the patient does not answer</button></div>
          </>
        )}
        {stop && <div className="vc-banner stop" role="alert"><b>Safety stop.</b> A health concern always wins: the script stopped, nothing is booked and the send block is on. A person takes over.</div>}
        {slot && !booked && <div className="vc-banner slot">One slot offered: <b>{slotText(slot.slot_date, slot.slot_time)}</b>. It is booked only on a yes to this slot.</div>}
        {booked && (
          <div className="vc-banner booked"><b>{card.name}: Booked {slotText(card.appointment_date, card.appointment_time)}</b> by the CarePilot agent.
            {ended && ui.role === 'coordinator' && (
              <div><Undo callId={call.call_id} onDone={() => api(`voice/calls/${call.call_id}`).then(keep).catch(() => {})} /></div>
            )}
          </div>
        )}
        {ended && call.outcome === 'safety_stop' && call.handover && (
          <div className="small-note"><b>{card.name}: On hold: handover open.</b> A person calls by {hm(call.handover.callback_due_at)}. It is at the top of Needs you.</div>
        )}
        {(phase === 'live' || turns.length > 0) && (
          <div className="vc-transcript" ref={box} aria-live="polite">
            {turns.map((t) => <Turn key={t.seq} turn={t} patient={patient.full_name} onPlay={ended ? () => replay(t) : null} />)}
            {phase === 'live' && speaking === 'thinking' && <div className="vc-hearing">Patient is thinking…</div>}
            {phase === 'live' && speaking === 'patient' && <div className="vc-hearing">Patient speaking · Sarvam is listening and sorting…</div>}
            {!turns.length && <div className="vc-hearing">The transcript appears here as each line is spoken.</div>}
          </div>
        )}
        {ended && call.reason_code === 'patient_rejected' && (
          <div className="small-note"><b>The patient rejected the call.</b> Nothing was said, no automatic retry will follow, and the coordinator has a task.</div>
        )}
        {error && (
          <div className="error">{error}{' '}
            <button type="button" className="btn sm" onClick={() => { setError(''); setReload((n) => n + 1); }}>Try again</button></div>
        )}
        {ended && (
          <>
            <div className="vc-record">
              <Pill tone={outcome[1]}>{outcome[0]}</Pill>
              <span>Call #{call.call_id} · {when(call.started_at)} to {hm(call.ended_at)} · {call.language} · reason {call.reason_code}</span>
              <span>Models: speech {call.models.speech} · translation {call.models.translation} · sorter {call.models.sorter} · voice {call.models.voice}</span>
              {call.undone_at && <span>Booking undone by {ui.name(call.undone_by)}: “{call.undo_reason}”</span>}
            </div>
            <div className="btn-row">
              <button type="button" className="btn sm" onClick={() => ui.nav('audit', call.mrn)}>Audit trail</button>
              {ui.role === 'coordinator' && <button type="button" className="btn sm" onClick={() => ui.nav('worklist')}>Needs you</button>}
              <button type="button" className="btn sm" onClick={() => ui.nav('plan', call.mrn)}>Patient page</button>
            </div>
          </>
        )}
        <div className="vc-rules">The agent speaks only fixed lines from the library. The AI listens and sorts; rules decide. A health concern always wins.</div>
      </div>
    </div>
  );
}

function useOverview() {
  const [data, setData] = useState(null);
  useEffect(() => {
    api('voice/overview').then(setData).catch(() => {});
  }, []);
  return data?.enabled ? data : null;
}

function Calls({ item }) {
  const ui = useUI();
  return item.calls.map((c) => (
    <div key={c.call_id} className="d">
      Call #{c.call_id} {when(c.started_at)}: <b>{c.status === 'ended' ? outcomeOf(c)[0] : 'in progress'}</b>{c.undone_at ? ' · booking undone' : ''}{' '}
      {(c.status === 'ended' || ui.role === 'coordinator') && (
        <button type="button" className="btn sm ghost" onClick={() => ui.voiceCall({ callId: c.call_id })}>
          {c.status === 'ended' ? 'Open call record' : 'Resume call'}</button>
      )}
    </div>
  ));
}

// On the order card of the test the patient missed: there only when the T-7 reminder was ignored for 2 days.
export function SimulateCall({ card, focus = false }) {
  const ui = useUI();
  const ref = useRef(null);
  const v = card.voice;
  // Each call case opens on its own ending: Savitri S2, Sushila S3.
  const caseScript = ui.demoCases?.find((c) => c.mrn === v?.patient.mrn)?.script;
  const [picked, setPicked] = useState(null);
  const script = picked || caseScript || ui.voiceScript;
  useEffect(() => {
    if (focus && v?.escalation_id) ref.current?.scrollIntoView?.({ block: 'center' });
  }, [focus, v?.escalation_id]);
  if (!v) return card.voice_note ? <div className="small-note vc-cardcall">{card.voice_note}</div> : null;
  const blocked = !v.call_id && v.blocked.length > 0;
  return (
    <div className="vc-cardcall" ref={ref}>
      <div className="vc-ignored"><b>Reminder ignored for {Math.floor(v.hours_since / 24)} days</b> ({v.hours_since} hours): sent {when(v.reminder_at)}, no reply, not booked.{' '}
        <button type="button" className="btn sm ghost"
          onClick={() => openMessages(ui, v.patient)}>View reminder</button>
      </div>
      {v.no_script && <div className="small-note"><b>No simulated call.</b> {v.no_script}.</div>}
      {!v.no_script && ui.role === 'coordinator' && (
        <div className="btn-row">
          <button type="button" className="btn primary" disabled={blocked}
            onClick={() => ui.voiceCall(v.call_id ? { callId: v.call_id } : { escalationId: v.escalation_id, script })}>
            {v.call_id ? 'Resume call' : 'Simulate call'}</button>
          {!v.call_id && !blocked && (
            <label className="small-note">Simulated patient answers as{' '}
              <select value={script} onChange={(e) => { setPicked(e.target.value); ui.setVoiceScript(e.target.value); }}>
                <option value="s2">S2: I'm fine, no need</option>
                <option value="s3">S3: mentions pain (safety stop)</option>
              </select>
            </label>
          )}
        </div>
      )}
      {blocked && <div className="small-note">Cannot call now: {v.blocked.join('; ')}{v.opens_at ? `. Calls open ${when(v.opens_at)}` : ''}.</div>}
    </div>
  );
}

// Workspace: status and call records. The call itself starts on the order card.
export function VoicePanel() {
  const ui = useUI();
  const data = useOverview();
  if (!data) return null;
  return (
    <div className="card agent-card">
      <div className="card-title">Voice escalation <span className="vc-sim">SIMULATED CALL</span></div>
      <div className="card-desc">
        {data.settings.delay_hours} hours after an ignored reminder, with the test still not booked, its order card shows Simulate call.
        Calls only from {data.settings.window_start} to {data.settings.window_end}, never on Sunday, and only through the safety gate.
      </div>
      {data.items.map((it) => (
        <div key={it.card.card_id} className={`v4-item ${it.on_hold || (it.event && it.card.state === 'Due') ? 'over' : 'req'}`}>
          <div className="t">{it.full_name}: {it.card.name}{' '}
            {it.card.state === 'Booked' ? <Pill tone="good">Booked {fmt(it.card.appointment_date)} {String(it.card.appointment_time || '').slice(0, 5)}</Pill>
              : it.on_hold ? <Pill tone="bad">On hold: handover open</Pill>
                : it.event ? <Pill tone="bad">Booking overdue</Pill> : <Pill tone="warn">Not booked</Pill>}</div>
          <div className="d">WhatsApp reminder sent {when(it.reminder_at)} · due {fmt(it.card.due_date)}
            {it.event ? ` · voice escalation raised ${when(it.event.raised_at)}` : ''}</div>
          <div className="btn-row">
            <button type="button" className="btn sm" onClick={() => ui.nav('plan', it.mrn)}>Open order card</button>
          </div>
          <Calls item={it} />
        </div>
      ))}
      {data.resettable && (
        <div className="btn-row">
          <span className="small-note">Reset and show again:</span>
          {Object.entries(data.scripts).map(([id, title]) => (
            <button key={id} type="button" className="btn sm ghost" onClick={() => ui.runVoiceDemo(id)}>{title}</button>
          ))}
        </div>
      )}
    </div>
  );
}

// Patient page: this patient's calls, with the coordinator's undo.
export function VoiceCalls({ mrn }) {
  const ui = useUI();
  const data = useOverview();
  const items = data?.items.filter((i) => i.mrn === mrn && i.calls.length) || [];
  if (!items.length) return null;
  return (
    <div className="card">
      <div className="card-title">Automated calls <span className="vc-sim">SIMULATED CALL</span></div>
      {items.map((it) => {
        const booking = it.calls.find((c) => c.outcome === 'booked' && !c.undone_at);
        return (
          <div key={it.card.card_id}>
            <Calls item={it} />
            {booking && ui.role === 'coordinator' && it.card.booking_source === 'voice_agent' && <Undo callId={booking.call_id} />}
          </div>
        );
      })}
    </div>
  );
}

// Doctor: told when a patient still said no after the one explanation.
export function VoiceDeclined() {
  const ui = useUI();
  const data = useOverview();
  if (!data?.declined.length) return null;
  return (
    <div className="card">
      <div className="card-title">Declined after the explanation ({data.declined.length})</div>
      <div className="card-desc">For your information. No more automated calls go out for these scans until a person decides.</div>
      {data.declined.map((d) => (
        <div key={d.call_id} className="d">
          <b>{d.full_name}</b>: {d.card_name}, {when(d.ended_at)}{' '}
          <button type="button" className="btn sm ghost" onClick={() => ui.voiceCall({ callId: d.call_id })}>Open call record</button>
        </div>
      ))}
    </div>
  );
}
