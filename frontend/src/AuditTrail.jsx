// R7 — Audit trail (prototype v7.2 scrAudit): patient, date range, actor type and text filters.
import React, { useEffect, useState } from 'react';
import { api } from './api.js';
import { describe, fmt, KIND, KIND_LABEL, ScreenHead, useUI } from './ui.jsx';

const QUICK = [[1, 'Since yesterday'], [7, 'Last 7 days'], [30, 'Last 30 days']];

// Demo date minus n days, as YYYY-MM-DD (the demo clock, not the computer's clock).
const minusDays = (iso, n) => {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - n);
  return d.toISOString().slice(0, 10);
};

export default function AuditTrail({ mrn: initialMrn, users }) {
  const ui = useUI();
  const [patients, setPatients] = useState([]);
  const [mrn, setMrn] = useState(initialMrn || '');
  const [kind, setKind] = useState('all');
  // Date range on each entry's demo date, both ends included; the prototype opens on the last 30 days.
  const today = ui.today ? String(ui.today).slice(0, 10) : null;
  const [from, setFrom] = useState(today ? minusDays(today, 30) : '');
  const [to, setTo] = useState(today || '');
  const [defaulted, setDefaulted] = useState(Boolean(today));
  useEffect(() => {  // only once, when the demo date first arrives; "All dates" may clear both later
    if (today && !defaulted) { setFrom(minusDays(today, 30)); setTo(today); setDefaulted(true); }
  }, [today, defaulted]);
  const badRange = from && to && from > to;
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const names = Object.fromEntries((users || []).map((u) => [u.id, u.person]));
  const byMrn = Object.fromEntries(patients.map((p) => [p.mrn, p.full_name]));

  useEffect(() => {
    api('patients').then(setPatients).catch(() => {});
  }, []);
  useEffect(() => {
    const params = new URLSearchParams({ limit: '400' });
    if (mrn) params.set('mrn', mrn);
    if (kind !== 'all') params.set('kind', kind);
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    if (query) params.set('q', query);
    if (!mrn && kind === 'all' && !from && !to && !query) params.set('days', '3650');  // always the filtered view
    if (badRange) return;
    setError('');
    api(`audit?${params}`).then(setData).catch((e) => setError(e.message));
  }, [mrn, kind, from, to, query, badRange]);

  const c = data?.counts || { agent: 0, person: 0, system: 0, ai: 0 };
  const total = c.agent + c.person + c.system + c.ai;
  const K = [['all', `All (${total})`], ['agent', `Agent (${c.agent})`], ['person', `Human (${c.person})`],
    ['system', `System (${c.system})`], ['ai', `AI suggestion (${c.ai})`]];

  return (
    <>
      <ScreenHead eyebrow={ui.role === 'oncologist' ? 'Oncologist · read only' : 'Governance'} title="Audit trail"
        sub="Every action on every patient — by the CarePilot agent, by people, by the system and by AI — with who did it and when. Search one patient over any date range." />
      <div className="card">
        <div className="aud-grid">
          <div>
            <label className="v3-field" htmlFor="afn">Patient</label>
            <select id="afn" className="v3-in" value={mrn} onChange={(e) => setMrn(e.target.value)}>
              <option value="">All patients</option>
              {patients.map((p) => <option key={p.mrn} value={p.mrn}>{p.full_name} ({p.mrn})</option>)}
            </select>
          </div>
          <div>
            <label className="v3-field" htmlFor="aff">From</label>
            <input type="date" id="aff" className="v3-in" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} />
          </div>
          <div>
            <label className="v3-field" htmlFor="aft">To</label>
            <input type="date" id="aft" className="v3-in" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
          </div>
          <div>
            <label className="v3-field" htmlFor="afq">Contains</label>
            <input id="afq" className="v3-in" value={q} placeholder="e.g. booking, gate2, G0, blocked"
              onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && setQuery(q.trim())}
              onBlur={() => setQuery(q.trim())} />
          </div>
        </div>
        <div className="btn-row" style={{ marginTop: 8 }}>
          {QUICK.map(([n, label]) => {
            const on = today && from === minusDays(today, n) && to === today;
            return <button key={n} type="button" className={`btn sm ${on ? 'primary' : 'ghost'}`} disabled={!today}
              onClick={() => { setFrom(minusDays(today, n)); setTo(today); }}>{label}</button>;
          })}
          <button type="button" className={`btn sm ${!from && !to ? 'primary' : 'ghost'}`} onClick={() => { setFrom(''); setTo(''); }}>All dates</button>
          {(mrn || query) && <button type="button" className="btn sm ghost" onClick={() => { setMrn(''); setQ(''); setQuery(''); }}>Clear patient and search</button>}
        </div>
        <div className="seg" style={{ marginTop: 12 }}>
          {K.map(([id, label]) => <button key={id} type="button" className={kind === id ? 'on' : ''} onClick={() => setKind(id)}>{label}</button>)}
        </div>
        <div className="small-note" style={{ marginTop: 8 }}>
          {mrn ? <><b>{byMrn[mrn] || mrn}</b> · </> : 'All patients · '}
          {from || to ? `${from ? fmt(from) : 'the start'} – ${to ? fmt(to) : 'today'}` : 'All dates'} (demo dates, both ends included) · {c.agent} agent actions, {c.person} human decisions or overrides,
          {' '}{c.system} system checks, {c.ai} AI suggestions
          {data && <> · hash chain {data.chain.valid ? <b>intact</b> : <span className="error">broken at entry {data.chain.firstBadId}</span>} ({data.chain.checked} entries checked)</>}
        </div>
      </div>
      {badRange && <div className="card"><div className="error">The From date must be on or before the To date.</div></div>}
      {error && <div className="card"><div className="error">{error}</div></div>}
      <div className="card">
        <div className="v4-log">
          {(data?.entries || []).map((e) => {
            const k = KIND[e.actor_type] || 'system';
            return (
              <div key={e.id} className="e aud-e">
                <div className="ts">#{e.id} · demo {e.demo_date ? fmt(e.demo_date) : '—'}{e.demo_time ? ` ${e.demo_time}` : ''}
                  <div className="small-note">{new Date(e.occurred_at).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</div></div>
                <div><span className={`v4-k ${k}`}>{KIND_LABEL[k]}</span></div>
                <div>{e.mrn ? <a role="button" tabIndex={0} onClick={() => ui.nav('plan', e.mrn)}>{byMrn[e.mrn] || e.mrn}</a> : 'All patients'}</div>
                <div><b>{names[e.actor_name] || e.actor_name}</b> — {describe(e)}
                  <span className="small-note"> · {e.hash.slice(0, 10)}</span></div>
              </div>
            );
          })}
          {data && !data.entries.length && <div className="small-note">No entries match. Widen the date range or clear the search.</div>}
          {data && data.entries.length >= 400 && <div className="small-note">Showing the latest 400.</div>}
        </div>
      </div>
    </>
  );
}
