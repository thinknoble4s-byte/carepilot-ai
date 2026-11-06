// R2 — Workspace worklist (prototype v7.2 scrWorklist), fed by GET /worklist.
import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { openCall, openDeptLists, openHandover, openMessages, openPause } from './Modals.jsx';
import { PatientLink } from './PatientCard.jsx';
import ProposalCard from './Proposals.jsx';
import { InfoCopies, RoutingSettings } from './TestCycle.jsx';
import { bookedLabel, Chip, fmt, GapState, GUARD, Reason, ScreenHead, useUI } from './ui.jsx';

const FILTERS = [['action', 'Coordinator action required'], ['noresp', 'No response'], ['overdue', 'Overdue'],
  ['verify', 'Awaiting verification'], ['duesoon', 'Due soon'], ['watch', 'Early warning'], ['ontrack', 'On track']];
const OFF = { agent: 1, approval: 1, closed: 1, paused: 1 };

export default function Worklist() {
  const ui = useUI();
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [filter, setFilter] = useState('action');
  const [mine, setMine] = useState('mine');
  const me = ui.user?.person;

  const [allCards, setAllCards] = useState([]);
  const [replyProposals, setReplyProposals] = useState([]);
  const load = useCallback(() => {
    api('worklist').then(setData).catch((e) => setError(e.message));
    api('proposals').then(setReplyProposals).catch(() => {});
    api('cards').then(setAllCards).catch(() => {});
  }, []);
  useEffect(() => load(), [load]);

  if (error) return <div className="card"><div className="error">{error}</div></div>;
  if (!data) return <div className="card"><div className="empty">Loading…</div></div>;

  const other = data.coordinators.find((c) => c !== me) || 'Colleague';
  // Use Case 2c: an urgent call moved to the cover shows in the cover's list too.
  const isMine = (p) => p.coordinator === me || (p.assignees || []).includes(me);
  const inScope = (p) => mine === 'all' || (mine === 'mine' ? isMine(p) : !isMine(p));
  const rows = data.patients.filter(inScope);
  const tags = (p) => new Set(p.reasons.map((r) => (r.action ? ['action', r.tag] : [r.tag])).flat());
  const count = Object.fromEntries(FILTERS.map(([k]) => [k, 0]));
  rows.forEach((p) => {
    if (OFF[p.main]) return;
    if (p.main === 'ontrack') count.ontrack += 1;
    tags(p).forEach((t) => { if (count[t] != null && t !== 'ontrack') count[t] += 1; });
  });
  const shown = rows.filter((p) => {
    if (filter === 'all') return true;
    if (OFF[p.main]) return filter === p.main;
    if (filter === 'ontrack') return p.main === 'ontrack';
    return tags(p).has(filter);
  });
  const handled = rows.filter((p) => p.main === 'agent');
  const proposals = data.proposals.filter((m) => inScope(data.patients.find((p) => p.mrn === m.mrn) || {}));
  const g = data.glance;
  const fig = (n, l, s, cls) => (
    <div className={`bfig ${cls || ''}`}><div className="bn">{n}</div><div className="bl">{l}</div><div className="bs">{s}</div></div>
  );
  const open = (mrn) => ui.nav('plan', mrn);
  const act = async (fn, msg) => {
    try {
      await fn();
      ui.toast(msg);
      ui.refresh();
    } catch (e) {
      ui.toast(e.message);
    }
  };
  const seg = [['mine', `My patients (${data.patients.filter(isMine).length})`],
    ['other', `${other.split(' ')[0]}'s (${data.patients.filter((p) => !isMine(p)).length})`],
    ['all', `All (${data.patients.length})`]];

  return (
    <>
      <ScreenHead eyebrow={`Care coordinator · ${me || ''}`} title="Workspace"
        sub="Everyone on follow-up. The CarePilot agent runs the repetitive coordination work around the clock; you see what needs a person — exceptions, escalations and conversations — and what the agent has already done. Click a patient to open them." />
      {GUARD}
      <div className="card" style={{ padding: '12px 16px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <div className="seg">
            {seg.map(([id, label]) => (
              <button key={id} type="button" className={mine === id ? 'on' : ''} onClick={() => setMine(id)}>{label}</button>
            ))}
          </div>
          <div className="psearch">
            <label className="small-note" htmlFor="psq">Find a patient</label>
            <input id="psq" className="v3-in" list="psl" placeholder="Name or MRN — opens the patient"
              onChange={(e) => {
                const hit = data.patients.find((p) => p.full_name === e.target.value || p.mrn === e.target.value);
                if (hit) open(hit.mrn);
              }} />
            <datalist id="psl">{data.patients.map((p) => <option key={p.mrn} value={p.full_name}>{p.mrn}</option>)}</datalist>
          </div>
          <div className="btn-row" style={{ margin: 0 }}>
            <button type="button" className="btn sm" onClick={() => ui.nav('intake')}>⬆ Upload today's appointment list</button>
            <button type="button" className="btn sm" onClick={() => openDeptLists(ui)}>Morning department lists</button>
          </div>
        </div>
        <div className="small-note" style={{ marginTop: 6 }}>
          Appointment list last uploaded: {data.appointments
            ? `batch #${data.appointments.batch_id} · ${data.appointments.rows_created} rows matched, ${data.appointments.rows_exception} exceptions`
            : 'not yet'}
        </div>
      </div>

      <div className="card agent-card">
        <div className="card-title">Today at a glance</div>
        <div className="bfigs">
          {fig(g.agent_actions, 'actions done by the agent',
            <>since yesterday evening, for {g.agent_patients} patients — <a role="button" tabIndex={0}
              onClick={() => { const d = document.getElementById('agentdone'); d.open = true; d.scrollIntoView(); }}>see them</a></>, 'ag')}
          {fig(g.handled, 'patients fully handled by the agent', 'off your list — nothing for you to do', 'ag')}
          {fig(g.approvals, 'approvals waiting', 'one tap each, below')}
          {fig(g.need_you, 'patients need you now', 'exceptions, escalations and conversations', 'you')}
        </div>
        <div className="small-note" style={{ marginTop: 8 }}>
          Without the agent, <b>{g.need_you + g.handled + g.approvals} patients</b> would need you today; with it, <b>{g.need_you}</b> do,
          plus {g.approvals} one-tap approval{g.approvals === 1 ? '' : 's'}. The agent never handles health matters — those always come to you.
        </div>
      </div>

      {replyProposals.length > 0 && (
        <div className="card agent-card">
          <div className="card-title">Replies to approve ({replyProposals.length})</div>
          <div className="card-desc">The AI found no health mention and drafted a summary. Routine reminders to these patients are on hold until you approve or reject.</div>
          {replyProposals.map((p) => <ProposalCard key={p.proposal_id} p={p} onDone={load} />)}
        </div>
      )}

      {proposals.length > 0 && (
        <div className="card agent-card">
          <div className="card-title">Agent proposals waiting for your approval ({proposals.length})</div>
          <div className="card-desc">Tasks in shadow mode. Nothing is sent until you approve.</div>
          {proposals.map((m) => (
            <div key={m.message_id} className="v4-item req">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
                <div className="t">{m.full_name} — {m.template_id.replaceAll('_', ' ')}{m.item_code ? ` for ${m.item_code}` : ''} ({m.recipient})</div>
                <span className="tag">Shadow mode</span>
              </div>
              <div className="d">The agent would send, in {m.language}: "{m.body_en}"</div>
              <div className="btn-row">
                <button type="button" className="btn sm good" onClick={() => act(() => post(`outbox/${m.message_id}/approve`), 'Approved and sent')}>Approve</button>
                <button type="button" className="btn sm" onClick={() => act(() => post(`outbox/${m.message_id}/reject`), 'Rejected')}>Reject</button>
                <button type="button" className="btn sm ghost" onClick={() => open(m.mrn)}>Open</button>
              </div>
            </div>
          ))}
        </div>
      )}

      <InfoCopies />

      <div className="card-title" style={{ margin: '6px 0 8px' }}>Needs you</div>
      <div className="v4-kpis" style={{ gridTemplateColumns: 'repeat(7,1fr)' }}>
        {FILTERS.map(([id, label]) => (
          <button key={id} type="button" className={`v4-kpi ${filter === id ? 'on' : ''}`} onClick={() => setFilter(id)}>
            <div className="n">{count[id]}</div><div className="l">{label}</div>
          </button>
        ))}
      </div>
      <div className="card" style={{ padding: '6px 8px' }}>
        <div className="v4-row head"><div>Patient</div><div>Status</div><div>Why, and by when</div><div>Contact</div><div /></div>
        {shown.map((p) => (
          <div key={p.mrn} className="v4-row">
            <div>
              <div className="v4-name">
                <PatientLink mrn={p.mrn} name={p.full_name} style={{ color: 'inherit', textDecoration: 'none' }} />
                {p.mrn === 'SYN-0011' && <> <span className="tag hosp">Demo patient</span></>}
              </div>
              <div className="v4-meta">{p.mrn} · {p.language}{p.therapy ? ` · ${p.therapy}` : ''}
                {p.treatment_end ? ` · month ${Math.max(0, Math.round((new Date(data.today) - new Date(p.treatment_end)) / 864e5 / 30.4))}` : ''}</div>
            </div>
            <div><Chip k={p.main} /></div>
            <div className="v4-reason">
              {p.reasons.length ? p.reasons.slice(0, 3).map((r, i) => <Reason key={i} r={r} today={data.today} />)
                : <div>Nothing due in the next 14 days</div>}
              {p.reasons.length > 3 && <div className="small-note">+{p.reasons.length - 3} more</div>}
            </div>
            <div className="v4-meta">Last reply: {fmt(p.last_reply)}<br />Coordinator: <b>{p.coordinator || 'Unassigned'}</b>
              {p.caregiver_in_loop && <><br />Caregiver in the loop</>}</div>
            <div>
              {p.main !== 'closed' && (
                <div className="inline-acts">
                  {p.main === 'paused' ? (
                    <button type="button" className="btn sm" onClick={() => act(() => post(`patients/${p.mrn}/status`, { status: 'Active' }), 'Follow-up resumed')}>Resume</button>
                  ) : (
                    <>
                      {p.handover_id && <button type="button" className="btn sm primary" onClick={() => openHandover(ui, p)}>Review handover</button>}
                      <button type="button" className={`btn sm ${p.mrn === 'SYN-0011' && !p.handover_id ? 'primary' : ''}`} onClick={() => openMessages(ui, p)}>Message</button>
                      <button type="button" className="btn sm" onClick={() => openCall(ui, p)}>Call</button>
                      <button type="button" className="btn sm" onClick={() => openPause(ui, p)}>Pause</button>
                    </>
                  )}
                  <button type="button" className="btn sm ghost" onClick={() => open(p.mrn)}>Open</button>
                </div>
              )}
            </div>
          </div>
        ))}
        {!shown.length && <div className="empty" style={{ padding: 18 }}>No patients in this group.</div>}
      </div>
      <div className="small-note" style={{ margin: '6px 0 0' }}>
        Also: <a role="button" tabIndex={0} onClick={() => setFilter('paused')}>paused ({rows.filter((p) => p.main === 'paused').length})</a> ·{' '}
        <a role="button" tabIndex={0} onClick={() => setFilter('closed')}>closed ({rows.filter((p) => p.main === 'closed').length})</a> ·{' '}
        <a role="button" tabIndex={0} onClick={() => setFilter('all')}>everyone ({rows.length})</a>
      </div>

      <div className="card-title" style={{ margin: '18px 0 8px' }}>Handled by the agent</div>
      <div className="card agent-card">
        {handled.length ? (
          <>
            <div className="card-desc">The agent is working these patients. They come back to your list automatically if a reply doesn't arrive in time or a health matter comes up.</div>
            {handled.map((p) => (
              <div key={p.mrn} className="hrow">
                <PatientLink mrn={p.mrn} name={p.full_name} bold />
                <span>{p.reasons.filter((r) => r.tag === 'agent').map((r) => r.txt).join(' · ')}</span>
              </div>
            ))}
          </>
        ) : <div className="small-note">No patients are fully with the agent right now.</div>}
      </div>
      <div className="card-title" style={{ margin: '18px 0 8px' }}>Order cards — all patients ({allCards.length})</div>
      <div className="card">
        <div className="card-desc">Every card created from a confirmed plan or a doctor's instruction, with its reminder gap. A card stays Due until a booking event.</div>
        <div className="v4-scroll">
          <table className="v4-table">
            <thead><tr><th>Patient</th><th>Card</th><th>Due</th><th>State</th><th>Owner / cover</th><th>Origin</th><th>Reminder gap</th></tr></thead>
            <tbody>
              {allCards.map((c) => (
                <tr key={c.card_id}>
                  <td><PatientLink mrn={c.mrn} name={c.full_name} /> <span className="small-note">{c.mrn}</span></td>
                  <td>{c.name}</td><td>{fmt(c.due_date)}</td>
                  <td><span className={`tag ${c.state === 'Due' ? '' : 'verified'}`}>{c.state === 'Booked' ? bookedLabel(c) : c.state}</span></td>
                  <td>{c.owner_name}<div className="small-note">cover {c.cover_name}</div></td>
                  <td>{c.origin === 'plan_item' ? 'Plan item' : "Doctor's instruction"}</td>
                  <td><GapState status={c.gap_status} reason={c.gap_reason} rule={c.gap_rule} /></td>
                </tr>
              ))}
              {!allCards.length && <tr><td colSpan={7} className="small-note">No order cards yet. Cards are created when a coordinator confirms a plan.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      <RoutingSettings />

      <details className="card agent-card" id="agentdone">
        <summary className="card-title" style={{ cursor: 'pointer', margin: 0 }}>Everything the agent did since yesterday evening ({data.feed.length} actions)</summary>
        <div className="card-desc" style={{ marginTop: 8 }}>Routine coordination within the autonomy policy. Every action is on the patient's audit trail; switch the agent off for any patient from their patient view.</div>
        <div className="v4-log">
          {data.feed.map((r, i) => (
            <div key={i} className="e">
              <div className="ts">{fmt(r.day)}</div>
              <div><span className="v4-k agent">Agent</span></div>
              <div><PatientLink mrn={r.mrn} name={r.full_name} bold /> — {r.text}</div>
            </div>
          ))}
          {!data.feed.length && <div className="small-note">No agent actions yet today. Run the morning check.</div>}
        </div>
      </details>
    </>
  );
}
