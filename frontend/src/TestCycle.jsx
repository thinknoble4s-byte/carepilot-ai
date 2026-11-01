// The test cycle (Use Case 2T v1.4; the lab and outside reports work in every data set). Patient page: test reports
// (hospital and outside) with the rule score and the seven hard checks, the report checks, the coordinator's Verify /
// Reject panel, and the SIMULATED demo controls (appointment-list row, hospital lab report, hospital data).
// Workspace: "Copies for your information" (auto-matched reports: Confirm or Flag as wrong) and the routing setting.
// Needs my decision: "Tests to verify" (imaging, the doctor's Verify) and "Report ready for review".
// CarePilot never shows a result value, summary, normal/abnormal mark or a score of the patient: only the source file.
// The match score is about the document.
import React, { useEffect, useState } from 'react';
import { api, blobUrl, post } from './api.js';
import { PatientLink } from './PatientCard.jsx';
import { Dl, fmt, Pill, useUI } from './ui.jsx';

const at = (ts) => (ts ? `${fmt(String(ts).slice(0, 10))} ${String(ts).slice(11, 16)}` : '');
const STATUS = {
  verified: ['Report verified (2 identifiers)', 'good'], mismatch: ['Mismatch — not linked', 'bad'], exception: ['No matching card', 'bad'],
  unverified: ['Unverified', 'warn'], rejected: ['Rejected', 'gray'], held: ['In the hospital data (not sent)', 'gray'],
  auto_matched: ['Auto-matched — not yet confirmed by a person', 'warn'], flagged: ['Unverified — flagged as wrong', 'bad'],
};
const ROUTED = { auto_matched: 'name, MRN and test match: verified by the system, to the doctor', wrong_report: 'patient asked for the right report', verify: 'coordinator to verify' };
const ANSWER = { done_outside: 'Done at an outside lab', done_hospital: 'Done at the hospital', not_done: 'Not done yet' };
const DELAY = { cost: 'Cost', pre_authorisation: 'Pre-authorisation', transport: 'Transport or distance', work_family: 'Work or family', other: 'Other' };

export async function openReportFile(ui, reportId) {
  try {
    const url = await blobUrl(`reports/${reportId}/file`);
    window.open(url, '_blank', 'noopener');
  } catch (e) {
    ui.toast(e.message === 'HTTP 409' ? 'The doctor sees a report once it is verified or auto-matched' : e.message);
  }
}

function Checks({ r, labels }) {
  if (r.score == null) return null;
  return (
    <div className="card-flat" style={{ marginTop: 6 }}>
      <div className="small-note">Rule score <b>{r.score}</b> / 100 (about the document, not a probability) · routed: <b>{ROUTED[r.routing_applied] || r.routing_applied}</b>
        {r.routing_mode === 'shadow' && r.routing !== r.routing_applied ? ` · Shadow mode: the rules would have ${ROUTED[r.routing]}` : ''}</div>
      <table className="v4-table" style={{ marginTop: 6 }}>
        <tbody>{Object.entries(r.hard_checks || {}).map(([k, v]) => (
          <tr key={k}><td>{k}</td><td>{labels[k]}</td><td>{v ? 'Pass' : <b>Fail</b>}</td></tr>
        ))}</tbody>
      </table>
      {r.match_facts?.length > 0 && <div className="small-note" style={{ marginTop: 6 }}>Match facts: {r.match_facts.join(' · ')}</div>}
    </div>
  );
}

export function ReportsSection({ mrn }) {
  const ui = useUI();
  const [data, setData] = useState(null);
  useEffect(() => {
    api(`patients/${mrn}/reports`).then(setData).catch(() => {});
  }, [mrn]);
  if (!data) return null;
  const coordinator = ui.role === 'coordinator';
  return (
    <div className="card">
      <div className="card-title">Test reports</div>
      <div className="card-desc">A hospital report is linked by MRN and test code and verified by the system when both match. A photo or PDF from the patient is held Unverified, its identifiers read by AI and scored by plain rules: if the name, the MRN and the test match, all hard checks pass and the score is {data.routing.threshold} or more, it is verified by the system and goes to the doctor (no person confirms it); below that a coordinator verifies it; the wrong kind of report or an unreadable file brings the "possible wrong report" message. Routing mode: <b>{data.routing.mode === 'live' ? 'Live' : 'Shadow (every outside report waits for the coordinator)'}</b>. CarePilot never reads a result.</div>
      {data.checks.length > 0 && (
        <div className="v4-item">
          <div className="t">Report checks</div>
          {data.checks.map((k) => (
            <div key={k.check_id} className="d">{k.card_name}{k.round > 1 ? ` (round ${k.round})` : ''}: raised {fmt(k.raised_on)}
              {k.status === 'skipped' ? ' — report found in the hospital data, no message sent' : ''}
              {k.sent_on ? ` · sent ${fmt(k.sent_on)}` : ''}{k.status === 'blocked' ? ` · held: ${k.blocked_reason}` : ''}
              {k.answer ? ` · answer: ${ANSWER[k.answer]} (${fmt(k.answered_on)})` : k.sent_on ? ' · no answer yet' : ''}
              {k.timer_from ? ` · upload timer from ${fmt(k.timer_from)}` : ''}{k.nudged_on ? ` · nudge sent ${fmt(k.nudged_on)}` : ''}
              {k.status === 'closed' && k.closed_reason ? ` · closed: ${k.closed_reason.replaceAll('_', ' ')}` : ''}</div>
          ))}
        </div>
      )}
      {data.reports.length === 0 && <div className="small-note">No reports yet.</div>}
      {data.reports.map((r) => {
        const [label, tone] = STATUS[r.status] || [r.status, 'gray'];
        return (
          <div key={r.report_id} className={`v4-item ${r.status === 'unverified' || r.status === 'mismatch' ? 'req' : ''}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <div className="t">{r.road === 'hospital' ? 'Hospital lab' : 'Outside lab'} — {r.card_name || r.proposed_card_name || r.test_code || 'report'}</div>
              <span>{r.wrong_report && <><span className="tag unverified">Possibly wrong or unclear report</span> </>}<Pill tone={tone}>{label}</Pill></span>
            </div>
            <div className="d">Received {at(r.received_at_demo)} · {r.channel}{r.file_name ? ` · ${r.file_name}` : ''}
              {r.road === 'hospital' && ` · on the report: MRN ${r.report_mrn}, test ${r.test_code}`}
              {r.road === 'outside' && r.sender_phone ? ` · from ${r.sender_phone}` : ''}{r.group_id ? ` · page of report #${r.group_id}` : ''}</div>
            {r.patient_note && <div className="d">Patient's note: “{r.patient_note}”{r.note_translation ? <> · <span className="tag hosp">AI translation</span> {r.note_translation}</> : null}</div>}
            {r.status === 'mismatch' && r.mismatch && (
              <div className="d">Report vs card — MRN: <b>{r.mismatch.mrn[0]}</b> vs <b>{r.mismatch.mrn[1]}</b> · test code: <b>{r.mismatch.test_code[0]}</b> vs <b>{r.mismatch.test_code[1]}</b> · card {r.mismatch.card_state}. The card did not move; see the Report mismatch task.</div>
            )}
            {r.status === 'verified' && r.road === 'outside' && <div className="d">Outside report (verified by coordinator) — {ui.name(r.verified_by)}, {at(r.verified_at_demo)}; matching: {(r.matched || []).join(', ')}</div>}
            {r.status === 'rejected' && <div className="d">Rejected by {ui.name(r.rejected_by)}: {r.reject_reason}. The file is kept; the card did not move.</div>}
            {r.status === 'flagged' && <div className="d">Flagged as wrong by {ui.name(r.flagged_by)}: {r.flag_reason}. The file is kept Unverified; the card went back to its earlier state.</div>}
            {r.road === 'outside' && <Checks r={r} labels={data.hard_check_labels} />}
            {r.has_file && (['verified', 'auto_matched'].includes(r.status) || coordinator) && (
              <div className="btn-row"><button type="button" className="btn sm" onClick={() => openReportFile(ui, r.report_id)}>Open source file</button></div>
            )}
            {coordinator && r.status === 'unverified' && !r.group_id && <VerifyPanel r={r} data={data} />}
          </div>
        );
      })}
      {coordinator && <DemoControls mrn={mrn} tests={data.open_tests} />}
    </div>
  );
}

function VerifyPanel({ r, data }) {
  const ui = useUI();
  const [matched, setMatched] = useState([]);
  const [cardId, setCardId] = useState(r.proposed_card_id || '');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const act = async (fn, msg) => {
    setError('');
    try { await fn(); ui.toast(msg); ui.refresh(); } catch (e) { setError(e.message); }
  };
  const ids = r.identifiers || {};
  return (
    <div className="card-flat" style={{ marginTop: 8 }}>
      <div className="card-title">Verify outside report (person's act — never the agent's)</div>
      <div className="small-note">Identifiers read by the reader (AI suggestion; no result values are read):
        {r.ai_failed || !Object.keys(ids).length ? <b> could not read the file — check it by eye.</b> : null}</div>
      {Object.keys(ids).length > 0 && (
        <table className="v4-table" style={{ marginTop: 6 }}>
          <thead><tr><th>Identifier</th><th>On the report</th><th>On record</th><th>Matches?</th><th>Read</th></tr></thead>
          <tbody>{(r.facts || []).map((f) => (
            <tr key={f.identifier}><td>{data.labels[f.identifier] || f.identifier}</td><td>{f.report}</td><td>{f.record ?? '—'}</td>
              <td>{f.match === true ? 'Yes' : f.match === false ? 'No' : 'Check by eye'}</td>
              <td>{r.read_confidence?.[f.identifier] != null ? `${Math.round(r.read_confidence[f.identifier] * 100)}%` : ''}</td></tr>
          ))}</tbody>
        </table>
      )}
      <div className="small-note" style={{ marginTop: 6 }}>Proposed test card: <b>{r.proposed_card_name || 'none open'}</b>{r.proposed_card_state ? ` (${r.proposed_card_state})` : ''}. Open the file; check the document type, the report date, the received date and channel; then tick the identifiers you checked and that match (two at least).</div>
      <div className="btn-row" role="group" aria-label="Matching identifiers">
        {data.checkable.map((k) => (
          <label key={k} className="v4-check" style={{ marginRight: 12 }}>
            <input type="checkbox" checked={matched.includes(k)}
              onChange={() => setMatched(matched.includes(k) ? matched.filter((x) => x !== k) : [...matched, k])} /> {data.labels[k] || k}
          </label>
        ))}
      </div>
      <label className="v3-field" htmlFor={`vc${r.report_id}`}>Test card this report answers</label>
      <select id={`vc${r.report_id}`} className="v3-in" value={cardId} onChange={(e) => setCardId(Number(e.target.value) || '')}>
        <option value="">Choose…</option>
        {data.open_tests.filter((c) => ['Due', 'Booked', 'Completed'].includes(c.state) && c.specimen !== false)
          .map((c) => <option key={c.card_id} value={c.card_id}>{c.name} ({c.state})</option>)}
      </select>
      <div className="btn-row">
        <button type="button" className="btn primary" disabled={matched.length < 2 || !cardId}
          title={matched.length < 2 ? 'Needs two matching identifiers' : ''}
          onClick={() => act(() => post(`reports/${r.report_id}/verify`, { matched, card_id: cardId }), 'Verified — Report verified; the doctor now sees it')}>Verify</button>
      </div>
      <label className="v3-field" htmlFor={`vr${r.report_id}`}>Or reject — reason (required)</label>
      <input id={`vr${r.report_id}`} className="v3-in" value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. Belongs to another patient; not a lab report" />
      <div className="btn-row">
        <button type="button" className="btn" disabled={!reason.trim()}
          onClick={() => act(() => post(`reports/${r.report_id}/reject`, { reason }), 'Rejected — card unchanged, file kept; a Call the patient task asks her to resend')}>Reject</button>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

function DemoControls({ mrn, tests }) {
  const ui = useUI();
  const first = tests.find((c) => !['Report verified', 'Verified'].includes(c.state)) || tests[0];
  const codes = [...new Set(tests.map((c) => c.test_code).filter(Boolean))];
  const [code, setCode] = useState(first?.test_code || '');
  const [day, setDay] = useState(ui.today ? String(ui.today).slice(0, 10) : '');
  const [time, setTime] = useState('08:00');
  const [attended, setAttended] = useState(false);
  const [labCode, setLabCode] = useState(first?.test_code || '');
  const [labMrn, setLabMrn] = useState(mrn);
  const [withFile, setWithFile] = useState(true);
  const [deliver, setDeliver] = useState(true);
  const [error, setError] = useState('');
  const act = async (fn, msg) => {
    setError('');
    try { const r = await fn(); ui.toast(typeof msg === 'function' ? msg(r) : msg); ui.refresh(); } catch (e) { setError(e.message); }
  };
  if (!first) return null;
  return (
    <div className="card-flat" style={{ marginTop: 10 }}>
      <div className="card-title">Demo controls <span className="tag unverified">SIMULATED</span></div>
      <div className="small-note">Stand-ins for the hospital systems: a test row of the daily appointment list (Use Case 0) and the hospital lab sending a report, or holding it in the hospital data where the report check finds it.</div>
      <div className="grid-2">
        <div>
          <div className="card-title" style={{ marginTop: 8 }}>Appointment list row</div>
          <label className="v3-field" htmlFor="alC">Test code</label>
          <input id="alC" className="v3-in" list="tcCodes" value={code} onChange={(e) => setCode(e.target.value)} />
          <datalist id="tcCodes">{codes.map((c) => <option key={c} value={c} />)}</datalist>
          <label className="v3-field" htmlFor="alD">Date</label>
          <input id="alD" type="date" className="v3-in" value={day} onChange={(e) => setDay(e.target.value)} />
          <label className="v3-field" htmlFor="alT">Time</label>
          <input id="alT" type="time" className="v3-in" value={time} onChange={(e) => setTime(e.target.value)} />
          <label className="v4-check" style={{ marginTop: 8 }}>
            <input type="checkbox" checked={attended} onChange={(e) => setAttended(e.target.checked)} /> Attended (the test was done)
          </label>
          <div className="btn-row">
            <button type="button" className="btn" disabled={!code || !day}
              onClick={() => act(() => post('test-cycle/appointment-list', { rows: [{ mrn, test_code: code, date: day, time: time || null, attended }] }),
                (r) => (r.moved.length ? `Appointment list loaded: card ${r.moved[0].to}` : 'Appointment list loaded: row went to the exceptions list'))}>Load the appointment list</button>
          </div>
        </div>
        <div>
          <div className="card-title" style={{ marginTop: 8 }}>Simulated lab: send report</div>
          <label className="v3-field" htmlFor="lbM">MRN on the report</label>
          <input id="lbM" className="v3-in" value={labMrn} onChange={(e) => setLabMrn(e.target.value)} />
          <label className="v3-field" htmlFor="lbC">Test code on the report</label>
          <input id="lbC" className="v3-in" list="tcCodes" value={labCode} onChange={(e) => setLabCode(e.target.value)} />
          <div className="small-note">Specimen-based tests only (blood test, CBC): imaging has no lab report.</div>
          <label className="v4-check" style={{ marginTop: 8 }}>
            <input type="checkbox" checked={withFile} onChange={(e) => setWithFile(e.target.checked)} /> Attach the report file
          </label>
          <label className="v4-check" style={{ marginTop: 4 }}>
            <input type="checkbox" checked={!deliver} onChange={(e) => setDeliver(!e.target.checked)} /> Only put it in the hospital data (not sent; the 07:00 report check finds it)
          </label>
          <div className="btn-row">
            <button type="button" className="btn" disabled={!labCode}
              onClick={() => act(() => post('simulated-lab/report', { mrn, test_code: labCode, report_mrn: labMrn || mrn, with_file: withFile, deliver }),
                (r) => (r.duplicate ? 'Same report sent again — attached once, nothing new'
                  : { verified: 'Report linked and verified (MRN and test code match)', held: 'Report held in the hospital data', mismatch: 'Report mismatch — the card did not move; task for the owner', exception: 'No matching test card — sent to the exceptions list' }[r.status] || 'Report received'))}>Send report</button>
          </div>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

// Workspace: Copies for your information (no due date, no Overdue mark, so not tasks).
export function InfoCopies() {
  const ui = useUI();
  const [items, setItems] = useState([]);
  const [reason, setReason] = useState({});
  const load = () => api('copies').then(setItems).catch(() => {});
  useEffect(() => { load(); }, []);
  const act = async (fn, msg) => {
    try { await fn(); ui.toast(msg); ui.refresh(); load(); } catch (e) { ui.toast(e.message); }
  };
  if (!items.length) return null;
  const coordinator = ui.role === 'coordinator';
  return (
    <div className="card">
      <div className="card-title">Copies for your information ({items.filter((k) => k.status === 'open').length})</div>
      <div className="card-desc">Wrong-report notes: the patient was asked for the right report (older auto-matched reports from before 25 Oct may also appear: Confirm or Flag as wrong). No due date and no Overdue mark.</div>
      {items.map((k) => (
        <div key={k.copy_id} className={`v4-item ${k.status === 'open' ? 'soon' : ''}`}>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="t"><PatientLink mrn={k.mrn} name={k.full_name} /> · {k.mrn} — {k.test_name}</div>
            <Pill tone={k.status === 'open' ? 'warn' : k.status === 'flagged' ? 'bad' : 'good'}>
              {k.kind === 'auto_matched' ? `Auto-matched ${k.score}` : 'Possibly wrong or unclear report'}{k.status !== 'open' ? ` · ${k.status} by ${ui.name(k.acted_by)}` : ''}</Pill>
          </div>
          <div className="d">Received {at(k.received_at_demo)} · {k.file_name} · card {k.card_state}{k.owner_name ? ` · for ${k.owner_name}` : ''}</div>
          {k.match_facts?.length > 0 && <div className="d">Match facts: {k.match_facts.join(' · ')}</div>}
          {k.reason && <div className="d">Reason: {k.reason}</div>}
          <div className="btn-row">
            <button type="button" className="btn sm" onClick={() => openReportFile(ui, k.report_id)}>Open source file</button>
            {coordinator && k.status === 'open' && k.kind === 'auto_matched' && (
              <>
                <button type="button" className="btn sm good" onClick={() => act(() => post(`copies/${k.copy_id}/confirm`), 'Confirmed — Report verified')}>Confirm</button>
                <input className="v3-in" style={{ maxWidth: 240 }} value={reason[k.copy_id] || ''} placeholder="Reason to flag (required)"
                  aria-label="Reason to flag as wrong" onChange={(e) => setReason({ ...reason, [k.copy_id]: e.target.value })} />
                <button type="button" className="btn sm warn" disabled={!(reason[k.copy_id] || '').trim()}
                  onClick={() => act(() => post(`copies/${k.copy_id}/flag`, { reason: reason[k.copy_id] }), 'Flagged — doctor\'s item withdrawn, card back, Call the patient task')}>Flag as wrong</button>
              </>
            )}
            {coordinator && k.status === 'open' && k.kind === 'wrong_report' && (
              <button type="button" className="btn sm ghost" onClick={() => act(() => post(`copies/${k.copy_id}/read`), 'Marked as read')}>Mark as read</button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

// The routing setting (REQ-29): threshold, weights, Shadow / Live — changed only by the clinical lead.
export function RoutingSettings() {
  const ui = useUI();
  const [r, setR] = useState(null);
  const [evaluation, setEvaluation] = useState(null);
  const [threshold, setThreshold] = useState('');
  const load = () => api('report-routing').then((x) => { setR(x); setThreshold(String(x.threshold)); }).catch(() => {});
  useEffect(() => { load(); }, []);
  if (!r || !r.use_case_2t) return null;
  const lead = ui.role === 'oncologist' && ui.user?.id === r.clinical_lead;
  const change = async (body) => {
    try { await api('report-routing', { method: 'PUT', body: JSON.stringify(body) }); ui.toast('Routing setting changed (audited)'); load(); } catch (e) { ui.toast(e.message); }
  };
  const run = () => api('report-routing/evaluation').then(setEvaluation).catch((e) => ui.toast(e.message));
  return (
    <div className="card">
      <div className="card-title">Outside reports: routing setting (v{r.version})</div>
      <div className="card-desc">Mode <b>{r.mode === 'live' ? 'Live' : 'Shadow'}</b> · threshold <b>{r.threshold}</b> (a rule score, not a probability) · weights: {Object.entries(r.weights).map(([k, v]) => `${k.replaceAll('_', ' ')} ${v}`).join(', ')}.
        In Shadow mode the score and routing are shown to the coordinator but every outside report waits for her Verify. Only the clinical lead ({ui.name(r.clinical_lead)}) changes this; every change is audited.</div>
      {lead && (
        <div className="btn-row">
          <button type="button" className="btn sm" onClick={() => change({ mode: r.mode === 'live' ? 'shadow' : 'live' })}>Switch to {r.mode === 'live' ? 'Shadow' : 'Live'}</button>
          <input className="v3-in" style={{ maxWidth: 90 }} type="number" min="50" max="100" value={threshold} aria-label="Threshold"
            onChange={(e) => setThreshold(e.target.value)} />
          <button type="button" className="btn sm" disabled={Number(threshold) === r.threshold} onClick={() => change({ threshold: Number(threshold) })}>Set threshold</button>
        </div>
      )}
      <div className="btn-row"><button type="button" className="btn sm ghost" onClick={run}>Run the synthetic document test set</button></div>
      {evaluation && (
        <div className="small-note">{evaluation.documents} documents; <b>{evaluation.reached_threshold}</b> of {evaluation.should_not_pass} that should not pass reached {evaluation.threshold} (target zero),
          {' '}{evaluation.would_auto_route} would have been auto-routed; {evaluation.right_reports_auto_routed} of {evaluation.right_reports} right reports auto-routed.</div>
      )}
    </div>
  );
}

// Needs my decision: non-specimen tests (imaging) waiting for the doctor's Verify (every data set).
export function TestVerifications() {
  const ui = useUI();
  const [items, setItems] = useState([]);
  const load = () => api('doctor/verifications').then(setItems).catch(() => {});
  useEffect(() => { load(); }, []);
  const verify = async (c) => {
    try {
      const r = await post(`cards/${c.card_id}/verify`);
      ui.toast(`${c.full_name}: ${c.name} — ${r.state}`);
      ui.refresh();
    } catch (e) {
      ui.toast(e.message);
    }
  };
  if (!items.length) return null;
  return (
    <div className="card">
      <div className="card-title">Tests to verify ({items.length})</div>
      <div className="card-desc">A non-specimen test (imaging) has no lab report: the doctor sets it Verified once it is completed. A scan done at an outside centre comes with the patient's report (checked by AI, or verified by a coordinator): open it, then Verify. Lab reports are verified by their two identifiers (the system or a coordinator).</div>
      {items.map((c) => (
        <div key={c.card_id} className="v4-item soon">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="t"><PatientLink mrn={c.mrn} name={c.full_name} /> · {c.mrn} · {c.item_code} — {c.name}</div>
            <Pill tone="warn">{c.state} (non-specimen)</Pill>
          </div>
          <div className="d">Completed {fmt(c.completed_on)}{c.label ? ` · ${c.label}` : ''}</div>
          <div className="btn-row">
            {c.report && <button type="button" className="btn sm" onClick={() => openReportFile(ui, c.report.report_id)}>Open the outside report</button>}
            {ui.role === 'oncologist' && <button type="button" className="btn sm primary" onClick={() => verify(c)}>Verify</button>}
          </div>
        </div>
      ))}
    </div>
  );
}

// Needs my decision: reports ready for review (Use Case 2T).
export function ReportReviews() {
  const ui = useUI();
  const [data, setData] = useState({ open: [], recent: [] });
  const [choice, setChoice] = useState({});
  const [text, setText] = useState({});
  const [days, setDays] = useState({});
  const load = () => api('doctor/reviews').then(setData).catch(() => {});
  useEffect(() => { load(); }, []);
  const act = async (fn, msg) => {
    try { await fn(); ui.toast(msg); ui.refresh(); load(); } catch (e) { ui.toast(e.message); }
  };
  if (!data.open.length && !data.recent.length) return null;
  const live = data.open.filter((v) => ['open', 'awaiting_instruction'].includes(v.status));
  const notices = data.open.filter((v) => !['open', 'awaiting_instruction'].includes(v.status));
  return (
    <>
      <div className="card-title" style={{ margin: '6px 0 8px' }}>Report ready for review ({live.length})</div>
      {notices.map((v) => (
        <div key={`n${v.review_id}`} className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="v4-name"><PatientLink mrn={v.mrn} name={v.full_name} /> <span className="v4-meta">· {v.mrn} · {v.test_name}</span></div>
            <Pill tone="bad">{v.withdrawn_reason}</Pill>
          </div>
          <div className="d">The coordinator flagged the auto-matched outside report as wrong{v.flag_reason ? `: ${v.flag_reason}` : ''}. Nothing was sent to the patient automatically.</div>
          {ui.role === 'oncologist' && <div className="btn-row"><button type="button" className="btn sm" onClick={() => act(() => post(`reviews/${v.review_id}/noted`), 'Noted')}>Noted</button></div>}
        </div>
      ))}
      {live.map((v) => (
        <div key={v.review_id} className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="v4-name">
              <PatientLink mrn={v.mrn} name={v.full_name} />{' '}
              <span className="v4-meta">· {v.mrn} · {v.test_name}</span>
              {v.unverified_label && <> <span className="tag unverified">{v.unverified_label}</span></>}
            </div>
            <Dl due={String(v.due_at_demo).slice(0, 10)} label="Decision" today={ui.today} />
          </div>
          <div className="d" style={{ marginTop: 6 }}>Received {at(v.received_at_demo)} · <b>{v.road === 'outside' ? 'outside lab' : 'hospital lab'}</b> · checked {v.checked}
            {' '}· for {v.doctor}{v.moved_from ? ` (moved from ${v.moved_from} after 1 working day)` : ''}</div>
          <div className="d">Report date {v.report_date ? fmt(v.report_date) : '—'} · {v.days_since_due > 0 ? `${v.days_since_due} days since due` : v.days_since_due === 0 ? 'on the due date' : `${-v.days_since_due} days before due`}
            {v.delay_reason ? ` · reason for delay: ${DELAY[v.delay_reason] || v.delay_reason}` : ''}</div>
          {v.match_facts?.length > 0 && <div className="d">Match facts: {v.match_facts.join(' · ')}</div>}
          {v.patient_note && <div className="d">Patient's note: “{v.patient_note}”{v.note_translation ? <> · <span className="tag hosp">AI translation</span> {v.note_translation}</> : null}</div>}
          <div className="small-note">CarePilot shows no value, summary or mark: open the source file and decide.{v.opened_by ? ` Opened by ${ui.name(v.opened_by)} ${at(v.opened_at_demo)}.` : ''}</div>
          <div className="btn-row"><button type="button" className="btn sm" onClick={() => openReportFile(ui, v.report_id).then(load)}>Open source file</button></div>
          {v.status === 'open' && ui.role === 'oncologist' && (
            <>
              <div className="btn-row" role="radiogroup" aria-label="Your decision on the report">
                {[['continue', 'Continue as planned'], ['discussion', 'Needs discussion']].map(([k, l]) => (
                  <label key={k} className="v4-check" style={{ marginRight: 14 }}>
                    <input type="radio" name={`rv-${v.review_id}`} checked={choice[v.review_id] === k}
                      onChange={() => setChoice({ ...choice, [v.review_id]: k })} /> {l}
                  </label>
                ))}
              </div>
              <div className="btn-row">
                <button type="button" className="btn primary" disabled={!choice[v.review_id]}
                  onClick={() => act(() => post(`reviews/${v.review_id}/decision`, { decision: choice[v.review_id] }),
                    choice[v.review_id] === 'continue' ? 'Continue as planned — the patient gets the approved message' : 'Needs discussion — write the instruction')}>Submit</button>
              </div>
            </>
          )}
          {v.status === 'awaiting_instruction' && ui.role === 'oncologist' && (
            <div className="card-flat" style={{ marginTop: 8 }}>
              <div className="card-title">SIMULATED hospital record</div>
              <label className="v3-field" htmlFor={`rvT${v.review_id}`}>Instruction</label>
              <input id={`rvT${v.review_id}`} className="v3-in" value={text[v.review_id] ?? 'Review visit within 7 days'}
                onChange={(e) => setText({ ...text, [v.review_id]: e.target.value })} />
              <label className="v3-field" htmlFor={`rvN${v.review_id}`}>Within how many days</label>
              <input id={`rvN${v.review_id}`} type="number" min="1" max="365" className="v3-in" value={days[v.review_id] ?? '7'}
                onChange={(e) => setDays({ ...days, [v.review_id]: e.target.value })} />
              <div className="btn-row">
                <button type="button" className="btn primary"
                  onClick={() => act(() => post(`reviews/${v.review_id}/instruction`, { text: text[v.review_id] ?? 'Review visit within 7 days', days: Number(days[v.review_id] ?? 7) }),
                    'Instruction picked up — Doctor-requested visit card created and the patient told')}>Save to the record</button>
              </div>
            </div>
          )}
        </div>
      ))}
      {data.recent.length > 0 && (
        <div className="card">
          <div className="card-title">Recent report decisions</div>
          <table className="v4-table">
            <thead><tr><th>Patient</th><th>Test</th><th>Decision</th><th>Instruction</th></tr></thead>
            <tbody>{data.recent.map((d) => (
              <tr key={d.review_id}><td>{d.full_name}</td><td>{d.test_name} ({d.road})</td>
                <td>{d.decision === 'continue' ? 'Continue as planned' : 'Needs discussion'} · {ui.name(d.decided_by)} {at(d.decided_at_demo)}
                  {d.flagged_after_decision ? <> · <span className="tag unverified">Report flagged after your decision</span></> : null}</td>
                <td>{d.instruction || (d.decision === 'continue' ? (d.message_id ? 'Patient messaged' : 'No next visit — coordinator task') : '—')}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
