// The test cycle (every data set). Patient page: test reports (hospital and outside), the coordinator's check /
// reject panel for an outside report, and the SIMULATED demo controls (appointment-list row, hospital lab report).
// Needs my decision: "Tests to verify" (the doctor alone sets Report verified / Verified) and, in Use Case 2T,
// "Report ready for review" with Continue as planned / Needs discussion.
// CarePilot never shows a result value, summary, normal/abnormal mark or score: only the source file.
import React, { useEffect, useState } from 'react';
import { api, blobUrl, post } from './api.js';
import { Dl, fmt, Pill, useUI } from './ui.jsx';

const at = (ts) => (ts ? `${fmt(String(ts).slice(0, 10))} ${String(ts).slice(11, 16)}` : '');
const STATUS = {
  verified: ['Checked (2 identifiers)', 'good'], mismatch: ['Mismatch — not linked', 'bad'], exception: ['No matching card', 'bad'],
  unverified: ['Unverified', 'warn'], rejected: ['Rejected', 'gray'],
};

export async function openReportFile(ui, reportId) {
  try {
    const url = await blobUrl(`reports/${reportId}/file`);
    window.open(url, '_blank', 'noopener');
  } catch (e) {
    ui.toast(e.message === 'HTTP 409' ? 'The doctor sees a report only once it is verified' : e.message);
  }
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
      <div className="card-desc">Hospital reports are linked by MRN and test code and checked by plain rules. A photo or PDF from the patient is held Unverified until a coordinator checks two identifiers. CarePilot never reads a result: the doctor opens the source file and alone sets the test to verified.</div>
      {data.reports.length === 0 && <div className="small-note">No reports yet.</div>}
      {data.reports.map((r) => {
        const [label, tone] = STATUS[r.status] || [r.status, 'gray'];
        return (
          <div key={r.report_id} className={`v4-item ${r.status === 'unverified' || r.status === 'mismatch' ? 'req' : ''}`}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
              <div className="t">{r.road === 'hospital' ? 'Hospital lab' : 'Outside lab'} — {r.card_name || r.proposed_card_name || r.test_code || 'report'}</div>
              <Pill tone={tone}>{label}</Pill>
            </div>
            <div className="d">Received {at(r.received_at_demo)} · {r.channel}{r.file_name ? ` · ${r.file_name}` : ''}
              {r.road === 'hospital' && ` · on the report: MRN ${r.report_mrn}, test ${r.test_code}`}</div>
            {r.status === 'mismatch' && r.mismatch && (
              <div className="d">Report vs card — MRN: <b>{r.mismatch.mrn[0]}</b> vs <b>{r.mismatch.mrn[1]}</b> · test code: <b>{r.mismatch.test_code[0]}</b> vs <b>{r.mismatch.test_code[1]}</b> · card {r.mismatch.card_state}. The card did not move; see the Report mismatch task.</div>
            )}
            {r.status === 'verified' && r.road === 'outside' && <div className="d">Outside report (checked by coordinator) — {ui.name(r.verified_by)}, {at(r.verified_at_demo)}; matching: {(r.matched || []).join(', ')}</div>}
            {r.status === 'rejected' && <div className="d">Rejected by {ui.name(r.rejected_by)}: {r.reject_reason}. The file is kept; the card did not move.</div>}
            {r.has_file && (r.status === 'verified' || coordinator) && (
              <div className="btn-row"><button type="button" className="btn sm" onClick={() => openReportFile(ui, r.report_id)}>Open source file</button></div>
            )}
            {coordinator && r.status === 'unverified' && <VerifyPanel r={r} data={data} />}
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
      <div className="card-title">Check the document (person's act — never the agent's)</div>
      <div className="small-note">Identifiers read by the reader (AI suggestion; no result values are read):
        {r.ai_failed || !Object.keys(ids).length ? <b> could not read the file — check it by eye.</b> : null}</div>
      {Object.keys(ids).length > 0 && (
        <table className="v4-table" style={{ marginTop: 6 }}>
          <thead><tr><th>Identifier</th><th>On the report</th><th>On record</th><th>Matches?</th></tr></thead>
          <tbody>{(r.facts || []).map((f) => (
            <tr key={f.identifier}><td>{data.labels[f.identifier] || f.identifier}</td><td>{f.report}</td><td>{f.record || '—'}</td>
              <td>{f.match === true ? 'Yes' : f.match === false ? 'No' : 'Check by eye'}</td></tr>
          ))}</tbody>
        </table>
      )}
      <div className="small-note" style={{ marginTop: 6 }}>Proposed test card: <b>{r.proposed_card_name || 'none open'}</b>{r.proposed_card_state ? ` (${r.proposed_card_state})` : ''}. Open the file, then tick the identifiers you checked and that match (two at least).</div>
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
        {data.open_tests.filter((c) => ['Due', 'Scheduled', 'Completed'].includes(c.state)).map((c) => <option key={c.card_id} value={c.card_id}>{c.name} ({c.state})</option>)}
      </select>
      <div className="btn-row">
        <button type="button" className="btn primary" disabled={matched.length < 2 || !cardId}
          title={matched.length < 2 ? 'Needs two matching identifiers' : ''}
          onClick={() => act(() => post(`reports/${r.report_id}/verify`, { matched, card_id: cardId }), 'Checked — the test now waits for the doctor to verify it')}>Verify</button>
      </div>
      <label className="v3-field" htmlFor={`vr${r.report_id}`}>Or reject — reason (required)</label>
      <input id={`vr${r.report_id}`} className="v3-in" value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="e.g. Belongs to another patient; not a lab report" />
      <div className="btn-row">
        <button type="button" className="btn" disabled={!reason.trim()}
          onClick={() => act(() => post(`reports/${r.report_id}/reject`, { reason }), 'Rejected — the card is unchanged, the file is kept')}>Reject</button>
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
  const [error, setError] = useState('');
  const act = async (fn, msg) => {
    setError('');
    try { const r = await fn(); ui.toast(typeof msg === 'function' ? msg(r) : msg); ui.refresh(); } catch (e) { setError(e.message); }
  };
  if (!first) return null;
  return (
    <div className="card-flat" style={{ marginTop: 10 }}>
      <div className="card-title">Demo controls <span className="tag unverified">SIMULATED</span></div>
      <div className="small-note">Stand-ins for the hospital systems: a test row of the daily appointment list (Use Case 0) and the hospital lab sending a report.</div>
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
          <div className="btn-row">
            <button type="button" className="btn" disabled={!labCode}
              onClick={() => act(() => post('simulated-lab/report', { mrn, test_code: labCode, report_mrn: labMrn || mrn, with_file: withFile }),
                (r) => (r.duplicate ? 'Same report sent again — attached once, nothing new'
                  : { verified: 'Report received and checked — waiting for the doctor to verify', mismatch: 'Report mismatch — the card did not move; task for the owner', exception: 'No matching test card — sent to the exceptions list' }[r.status] || 'Report received'))}>Send report</button>
          </div>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

// Needs my decision: tests waiting for the doctor's verification (every data set).
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
      <div className="card-desc">Only the doctor sets the verified status. A specimen-based test (blood test) is verified once its report is received; a non-specimen test (imaging) once it is completed.</div>
      {items.map((c) => (
        <div key={c.card_id} className="v4-item soon">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="t"><a role="button" tabIndex={0} onClick={() => ui.nav('plan', c.mrn)}>{c.full_name}</a> · {c.mrn} · {c.item_code} — {c.name}</div>
            <Pill tone="warn">{c.state}{c.specimen === false ? ' (non-specimen)' : ''}</Pill>
          </div>
          <div className="d">{c.report ? `Report ${c.report.file_name} · ${c.report.road === 'outside' ? 'outside lab (checked by coordinator)' : 'hospital lab'} · received ${at(c.report.received_at_demo)}`
            : `Completed ${fmt(c.completed_on)}`}</div>
          <div className="btn-row">
            {c.report && <button type="button" className="btn sm" onClick={() => openReportFile(ui, c.report.report_id)}>Open source file</button>}
            {ui.role === 'oncologist' && <button type="button" className="btn sm primary" onClick={() => verify(c)}>{c.report ? 'Verify the report' : 'Verify'}</button>}
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
    try { await fn(); ui.toast(msg); ui.refresh(); } catch (e) { ui.toast(e.message); }
  };
  if (!data.open.length && !data.recent.length) return null;
  return (
    <>
      <div className="card-title" style={{ margin: '6px 0 8px' }}>Report ready for review ({data.open.length})</div>
      {data.open.map((v) => (
        <div key={v.review_id} className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
            <div className="v4-name">
              <a role="button" tabIndex={0} onClick={() => ui.nav('plan', v.mrn)}>{v.full_name}</a>{' '}
              <span className="v4-meta">· {v.mrn} · {v.test_name}</span>
            </div>
            <Dl due={String(v.due_at_demo).slice(0, 10)} label="Decision" today={ui.today} />
          </div>
          <div className="d" style={{ marginTop: 6 }}>Received {at(v.received_at_demo)} · <b>{v.road === 'outside' ? 'outside lab' : 'hospital lab'}</b>
            {v.road === 'outside' ? ` · verified by ${ui.name(v.verified_by)}` : ' · linked and checked by MRN and test code'}
            {' '}· for {v.doctor}{v.moved_from ? ` (moved from ${v.moved_from} after 1 working day)` : ''}</div>
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
                <td>{d.decision === 'continue' ? 'Continue as planned' : 'Needs discussion'} · {ui.name(d.decided_by)} {at(d.decided_at_demo)}</td>
                <td>{d.instruction || (d.decision === 'continue' ? (d.message_id ? 'Patient messaged' : 'No next visit — coordinator task') : '—')}</td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </>
  );
}
