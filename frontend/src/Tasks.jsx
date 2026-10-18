// Use Case 2b — tasks for a person after "Please help me book", "I can't come" or HELP.
// Tasks section on the Patient page, and the task panel: call attempts, then one result with a remarks note.
// The coordinator closes the task, never the order card; the card changes only when she records a booking.
// Use Case 2c — the doctor's Urgent choice: a "Call the patient today" task (owner by 16:00, cover by 18:00, then
// Overdue). It closes only with a reached call and a note; there is no "Not reached". Pass to cover by hand.
import React, { useEffect, useState } from 'react';
import { api, post } from './api.js';
import { PatientWords } from './Modals.jsx';
import { Dl, fmt, Pill, useUI } from './ui.jsx';

const RESULT_LABEL = {
  booked: 'Record booking', could_not_book: 'Could not book', not_reached: 'Not reached',
  rebooked: 'Rebooked', reason_recorded: 'Reason recorded', sorted: 'Sorted', needs_follow_up: 'Needs follow-up',
};
const RESULTS = {
  help_booking: ['booked', 'could_not_book', 'not_reached'],
  cant_come: ['rebooked', 'reason_recorded', 'not_reached'],
  help_request: ['sorted', 'needs_follow_up', 'not_reached'],
};
const REASON_LABEL = { cost: 'Cost', pre_authorisation: 'Pre-authorisation', transport: 'Transport or distance', work_family: 'Work or family', other: 'Other' };
const STATE_TONE = { Open: 'warn', Overdue: 'bad', Done: 'good', Cancelled: 'gray', 'Moved to cover': 'bad' };
const hm = (ts) => (ts ? String(ts).slice(11, 16) : '');
const at = (ts) => (ts ? `${fmt(String(ts).slice(0, 10))} ${String(ts).slice(11, 16)}` : '');

export function TasksSection({ mrn }) {
  const ui = useUI();
  const [data, setData] = useState(null);
  useEffect(() => {
    api(`patients/${mrn}/tasks`).then(setData).catch(() => {});
  }, [mrn]);
  if (!data) return null;
  const list = data.tasks;
  return (
    <div className="card">
      <div className="card-title">Tasks</div>
      <div className="card-desc">Work for a person, each with an owner, a cover and a due date. A task closes only when a person records the result; the order card is never closed here.</div>
      {data.hold && <div className="role-note" style={{ marginBottom: 8 }}><b>Routine reminders are held</b> while a help task is open: a person is already helping this patient.</div>}
      {list.length === 0 && <div className="small-note">No tasks for this patient.</div>}
      {list.map((t) => (t.urgent_task ? <UrgentItem key={t.task_id} t={t} /> : (
        <div key={t.task_id} className={`v4-item ${t.state === 'Overdue' ? 'over' : t.state === 'Open' ? 'req' : ''}`}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="t">{t.label || t.task_type.replaceAll('_', ' ')}{t.card_name ? ` — ${t.card_name}` : t.help_task ? ' — patient level' : ''}</div>
            <Pill tone={STATE_TONE[t.state] || 'gray'}>{t.state}</Pill>
          </div>
          <div className="d">{t.reason}</div>
          <div className="d">Owner: <b>{t.owner_name}</b>{t.cover_name ? ` · cover: ${t.cover_name}` : ''}
            {t.due_date && t.status === 'open' ? <> · <Dl due={t.due_date} today={ui.today} /></> : t.due_date ? ` · due ${fmt(t.due_date)}` : ''}</div>
          {t.attempts?.length > 0 && <div className="d">Call attempts: {t.attempts.map((a, i) => `${i + 1}) ${at(a.called_at_demo)} ${ui.name(a.called_by)} — ${a.result === 'reached' ? 'reached' : 'no answer'}`).join(' · ')}</div>}
          {t.status === 'done' && (
            <div className="d">Result: <b>{RESULT_LABEL[t.result] || 'Done'}</b>{t.result_code ? ` (${REASON_LABEL[t.result_code] || t.result_code})` : ''}
              {t.appointment_date ? ` · appointment ${fmt(t.appointment_date)}${t.appointment_time ? ` ${String(t.appointment_time).slice(0, 5)}` : ''}` : ''}
              {' '}· by {ui.name(t.done_by)} {t.done_at_demo ? at(t.done_at_demo) : fmt(t.done_on)}
              {t.done_note ? <> · remarks: “{t.done_note}”</> : null}</div>
          )}
          {t.help_task && t.status === 'open' && ui.role === 'coordinator' && (
            <div className="btn-row"><button type="button" className="btn sm primary"
              onClick={() => ui.showModal(`${t.label} — task panel`, 'Phone the patient outside CarePilot, log each call, then record one result. Nothing is sent to the patient.',
                <TaskPanel task={t} reasons={data.reasons} need={data.attempts_needed} />)}>Open task</button></div>
          )}
        </div>
      )))}
    </div>
  );
}

function UrgentItem({ t }) {
  const ui = useUI();
  const open = t.status === 'open';
  return (
    <div className={`v4-item ${open ? 'over' : ''}`}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <div className="t"><span className="tag live">Urgent</span> {t.label}</div>
        <Pill tone={STATE_TONE[t.state] || 'gray'}>{t.state}</Pill>
      </div>
      <div className="d">Doctor: <b>{ui.name(t.handover?.gate2_by)}</b>, Urgent at {hm(t.created_at_demo)} {fmt(String(t.created_at_demo).slice(0, 10))}
        {' '}· instruction: {t.handover?.instruction ? <b>{t.handover.instruction}</b> : <i>Waiting for the doctor's instruction</i>}</div>
      <div className="d">Owner: <b>{t.owner_name}</b>
        {t.moved_from ? ` — moved to cover, from ${t.moved_from} at ${hm(t.moved_at_demo)} (${t.moved_reason}) · deadline ${hm(t.cover_deadline_at)}`
          : ` by ${hm(t.owner_deadline_at)} · cover: ${t.cover_name} by ${hm(t.cover_deadline_at)}`}
        {t.state === 'Overdue' && <> · <b>Overdue after {hm(t.cover_deadline_at)}</b>: never closed by itself, stays at the top</>}</div>
      {t.attempts?.length > 0 && <div className="d">Call attempts: {t.attempts.map((a, i) => `${i + 1}) ${at(a.called_at_demo)} ${ui.name(a.called_by)} — ${a.result === 'reached' ? 'reached' : 'no answer'}`).join(' · ')}</div>}
      {t.status === 'done' && (
        <div className="d">Reached by <b>{ui.name(t.done_by)}</b> at {hm(t.done_at_demo)} · time to call <b>{t.minutes_to_call} minutes</b>
          {t.appointment_date ? ` · visit booked ${fmt(t.appointment_date)}${t.appointment_time ? ` ${String(t.appointment_time).slice(0, 5)}` : ''}` : ' · no booking recorded'}
          {t.done_note ? <> · note: “{t.done_note}”</> : null}</div>
      )}
      {open && <div className="small-note">Routine reminders are held while this call is open. Nothing is sent to the patient about the problem.</div>}
      {open && ui.role === 'coordinator' && (
        <div className="btn-row"><button type="button" className="btn sm primary"
          onClick={() => ui.showModal(`${t.label} — task panel`, 'Phone the patient outside CarePilot and pass on what the doctor asked. Give no advice of your own. Nothing is sent to the patient.',
            <UrgentPanel task={t} />)}>Open task</button></div>
      )}
    </div>
  );
}

function UrgentPanel({ task }) {
  const ui = useUI();
  const h = task.handover || {};
  const [attempts, setAttempts] = useState(task.attempts || []);
  const [result, setResult] = useState('');
  const [note, setNote] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [passing, setPassing] = useState(false);
  const [why, setWhy] = useState('');
  const [error, setError] = useState('');
  const card = Boolean(h.instruction_card);
  const ready = result === 'no_answer' || (result === 'reached' && note.trim() && (!time || date));

  const save = async () => {
    setError('');
    try {
      const r = await post(`tasks/${task.task_id}/attempts`, {
        result, note: note || null, appointment_date: result === 'reached' && date ? date : null,
        appointment_time: result === 'reached' && time ? time : null,
      });
      if (result === 'no_answer') {
        setAttempts([...attempts, r]);
        setResult('');
        ui.toast(`Call attempt ${attempts.length + 1} logged: no answer — the task stays open`);
        return;
      }
      ui.toast(`Patient reached — task Done, time to call ${r.minutes_to_call} minutes${date ? ', visit Booked (confirmed by coordinator)' : ''}`);
      ui.closeModal();
      ui.refresh();
    } catch (e) {
      setError(e.message);
    }
  };
  const pass = async () => {
    setError('');
    try {
      await post(`tasks/${task.task_id}/pass-to-cover`, { reason: why });
      ui.toast(`Passed to ${task.cover_name} (cover)`);
      ui.closeModal();
      ui.refresh();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <>
      <PatientWords h={h} />
      <div className="small-note" style={{ lineHeight: 1.7, marginTop: 6 }}>
        <span className="tag live">Urgent</span> chosen by <b>{ui.name(h.gate2_by)}</b> at {hm(h.gate2_at_demo)} {fmt(String(h.gate2_at_demo || '').slice(0, 10))}<br />
        Doctor's instruction: {h.instruction ? <b>{h.instruction}</b> : <i>Waiting for the doctor's instruction</i>}<br />
        Owner {task.owner_name}{task.moved_from ? ` (moved from ${task.moved_from})` : ''} · <b>by {hm(task.moved_from ? task.cover_deadline_at : task.owner_deadline_at)}</b>
        {!task.moved_from && ` · cover ${task.cover_name} by ${hm(task.cover_deadline_at)}`}
      </div>

      <div className="card-title" style={{ marginTop: 12 }}>Call attempts</div>
      {attempts.length === 0 ? <div className="small-note">None logged yet.</div> : (
        <ol className="small-note" style={{ margin: '4px 0 0 18px' }}>
          {attempts.map((a) => <li key={a.attempt_id}>{at(a.called_at_demo)} · {ui.name(a.called_by)} · {a.result === 'reached' ? 'reached' : 'no answer'}</li>)}
        </ol>
      )}

      <div className="card-title" style={{ marginTop: 12 }}>Log call attempt</div>
      <div role="radiogroup" aria-label="Call result">
        {[['reached', 'Reached'], ['no_answer', 'No answer']].map(([v, l]) => (
          <label key={v} className="v4-check" style={{ marginRight: 14 }}>
            <input type="radio" name={`ur${task.task_id}`} checked={result === v} onChange={() => setResult(v)} /> {l}
          </label>
        ))}
      </div>
      <div className="small-note">No answer keeps the task open: there is no "Not reached" for an urgent call. It closes only when a person reaches the patient.</div>
      {result === 'reached' && (
        <>
          <label className="v3-field" htmlFor="urN">Note: what you passed on and what the patient said (required)</label>
          <textarea id="urN" className="v3-in" rows={2} value={note} onChange={(e) => setNote(e.target.value)}
            placeholder="e.g. Told patient to come within 2 days; she agreed" />
          <div className="card-title" style={{ marginTop: 10 }}>Record booking (optional)</div>
          {card ? (
            <div className="grid-2">
              <div><label className="v3-field" htmlFor="urD">Appointment date (booked in the hospital system)</label>
                <input id="urD" type="date" className="v3-in" min={ui.today ? String(ui.today).slice(0, 10) : undefined} value={date} onChange={(e) => setDate(e.target.value)} /></div>
              <div><label className="v3-field" htmlFor="urT">Time</label>
                <input id="urT" type="time" className="v3-in" value={time} onChange={(e) => setTime(e.target.value)} /></div>
            </div>
          ) : <div className="small-note">Available once the doctor saves the instruction (the Doctor-requested visit card does not exist yet).</div>}
          <div className="small-note">Leave it empty and the card stays Due; the patient can use the booking message buttons. No confirmation message is sent.</div>
        </>
      )}
      <div className="btn-row">
        <button type="button" className="btn primary" disabled={!ready} onClick={save}>
          {result === 'reached' ? 'Save: patient reached, close the task' : 'Save the attempt'}</button>
        <button type="button" className="btn ghost" onClick={() => { ui.closeModal(); ui.refresh(); }}>Close</button>
      </div>

      {!task.moved_from && task.cover_name && (
        <div style={{ marginTop: 12 }}>
          {!passing ? <button type="button" className="btn sm" onClick={() => setPassing(true)}>Pass to cover ({task.cover_name})</button> : (
            <>
              <label className="v3-field" htmlFor="urP">Why are you passing it? (required)</label>
              <input id="urP" className="v3-in" value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. in a meeting, on leave, cannot reach after 2 tries" />
              <div className="btn-row"><button type="button" className="btn sm primary" disabled={!why.trim()} onClick={pass}>Pass to {task.cover_name} now</button></div>
            </>
          )}
        </div>
      )}
      <div className="small-note" style={{ marginTop: 8 }}>Never give medical advice: pass on the doctor's words. A new or worse health problem goes to the doctor outside CarePilot (open point).</div>
      {error && <div className="error">{error}</div>}
    </>
  );
}

function TaskPanel({ task, reasons, need }) {
  const ui = useUI();
  const [attempts, setAttempts] = useState(task.attempts || []);
  const [result, setResult] = useState('');
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [code, setCode] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState('');
  const misses = attempts.filter((a) => a.result === 'no_answer').length;
  const canNotReach = misses >= need;

  const logAttempt = async (r) => {
    setError('');
    try {
      const a = await post(`tasks/${task.task_id}/attempts`, { result: r });
      setAttempts([...attempts, a]);
      ui.toast(`Call attempt ${attempts.length + 1} logged: ${r === 'reached' ? 'reached' : 'no answer'}`);
    } catch (e) {
      setError(e.message);
    }
  };
  const ready = result && (
    (result === 'booked' && date && time) || (result === 'rebooked' && date)
    || (result === 'could_not_book' && note.trim()) || (result === 'reason_recorded' && code)
    || (result === 'not_reached' && canNotReach && note.trim())
    || (['sorted', 'needs_follow_up'].includes(result) && note.trim()));
  const save = async () => {
    setError('');
    try {
      await post(`tasks/${task.task_id}/result`, {
        result, note: note || null, appointment_date: date || null, appointment_time: time || null, reason_code: code || null,
      });
      ui.toast(`${task.label}: ${RESULT_LABEL[result]} — task closed${['booked', 'rebooked'].includes(result) ? (task.card_type === 'test' ? ', test Scheduled' : ', card Booked (confirmed by coordinator)') : ''}`);
      ui.closeModal();
      ui.refresh();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <>
      <div className="small-note" style={{ lineHeight: 1.7 }}>
        {task.card_name ? <>Card: <b>{task.card_name}</b> · </> : 'Patient-level task · '}
        Owner {task.owner_name}{task.cover_name ? `, cover ${task.cover_name}` : ''} · <Dl due={task.due_date} today={ui.today} />
      </div>

      <div className="card-title" style={{ marginTop: 12 }}>Call attempts</div>
      {attempts.length === 0 ? <div className="small-note">None logged yet.</div> : (
        <ol className="small-note" style={{ margin: '4px 0 0 18px' }}>
          {attempts.map((a) => <li key={a.attempt_id}>{at(a.called_at_demo)} · {ui.name(a.called_by)} · {a.result === 'reached' ? 'reached' : 'no answer'}</li>)}
        </ol>
      )}
      <div className="btn-row">
        <span className="small-note">Log call attempt:</span>
        <button type="button" className="btn sm" onClick={() => logAttempt('reached')}>Reached</button>
        <button type="button" className="btn sm" onClick={() => logAttempt('no_answer')}>No answer</button>
      </div>

      <div className="card-title" style={{ marginTop: 12 }}>Result</div>
      <div role="radiogroup" aria-label="Result">
        {RESULTS[task.task_type].map((r) => {
          const off = r === 'not_reached' && !canNotReach;
          return (
            <label key={r} className="v4-check" style={{ marginRight: 14, opacity: off ? 0.5 : 1 }}
              title={off ? `Needs ${need} call attempts with no answer (${misses} so far)` : ''}>
              <input type="radio" name={`res${task.task_id}`} disabled={off} checked={result === r} onChange={() => setResult(r)} /> {RESULT_LABEL[r]}
            </label>
          );
        })}
      </div>
      {!canNotReach && <div className="small-note">Not reached is available after {need} call attempts with no answer ({misses} so far).</div>}

      {['booked', 'rebooked'].includes(result) && (
        <div className="grid-2">
          <div><label className="v3-field" htmlFor="tpD">Appointment date (booked in the hospital system)</label>
            <input id="tpD" type="date" className="v3-in" min={ui.today ? String(ui.today).slice(0, 10) : undefined} value={date} onChange={(e) => setDate(e.target.value)} /></div>
          <div><label className="v3-field" htmlFor="tpT">Time{result === 'rebooked' ? ' (optional)' : ''}</label>
            <input id="tpT" type="time" className="v3-in" value={time} onChange={(e) => setTime(e.target.value)} /></div>
        </div>
      )}
      {result === 'reason_recorded' && (
        <>
          <label className="v3-field" htmlFor="tpR">Reason the patient cannot come</label>
          <select id="tpR" className="v3-in" value={code} onChange={(e) => setCode(e.target.value)}>
            <option value="">Choose a reason…</option>
            {reasons.map((r) => <option key={r} value={r}>{REASON_LABEL[r] || r}</option>)}
          </select>
        </>
      )}
      <label className="v3-field" htmlFor="tpN">
        Remarks note{result === 'could_not_book' ? ' — why it could not be booked (required)' : result === 'not_reached' || task.task_type === 'help_request' ? ' (required)' : ' (optional)'}
      </label>
      <textarea id="tpN" className="v3-in" rows={2} value={note} onChange={(e) => setNote(e.target.value)}
        placeholder={task.task_type === 'help_request' ? 'e.g. Asked where to park; answered' : 'e.g. Booked by phone for Friday morning'} />
      <div className="small-note">Never give medical advice. If the patient mentions a health problem, tell the doctor (open point in Use Case 2b).</div>
      <div className="btn-row">
        <button type="button" className="btn primary" disabled={!ready} onClick={save}>Save and close the task</button>
        <button type="button" className="btn ghost" onClick={ui.closeModal}>Cancel</button>
      </div>
      {error && <div className="error">{error}</div>}
    </>
  );
}
