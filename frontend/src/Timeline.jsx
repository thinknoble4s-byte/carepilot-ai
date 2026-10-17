// R3 — Patient journey (prototype v7.2 scrJourney): the longitudinal timeline, with origins.
import React, { useEffect, useState } from 'react';
import { api } from './api.js';
import { fmt, patientStatusPill, Pill, ScreenHead, useUI } from './ui.jsx';

const FILTERS = [['all', 'All events'], ['ms', 'Defining milestones'], ['gaps', 'Gaps'], ['patient', 'Patient-reported']];
const TIER = { hosp: ['hosp', 'Hospital A'], carepilot: ['unverified', 'CarePilot'], patient: ['ext', 'Patient-reported'] };

export default function Timeline({ mrn }) {
  const ui = useUI();
  const [patients, setPatients] = useState([]);
  const [t, setT] = useState(null);
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState('');
  useEffect(() => {
    api('patients').then(setPatients).catch(() => {});
  }, []);
  useEffect(() => {
    setT(null);
    api(`patients/${mrn}/timeline`).then(setT).catch((e) => setError(e.message));
  }, [mrn]);

  const items = (t?.events || []).filter((e) => filter === 'all' || (filter === 'ms' && e.kind === 'milestone')
    || (filter === 'gaps' && (e.kind === 'gap' || (e.kind === 'routine' && !e.origin))) || (filter === 'patient' && e.tier === 'patient'));
  const gaps = t ? t.need_origin - t.traced : 0;

  return (
    <>
      <ScreenHead eyebrow={ui.role === 'coordinator' ? 'Patient journey' : 'Oncologist · read only'} title="Patient journey"
        sub="The longitudinal story of care: follow-up plan, reminders, replies, decisions and instructions. Every event links to its source; anything without an origin is shown as a gap, not assumed. Tasks live on the patient view in Workspace; every step is on the Audit trail." />
      <div style={{ marginBottom: 12 }}>
        <label className="small-note" htmlFor="pp">Patient </label>
        <select id="pp" className="v4-sel" value={mrn} onChange={(e) => ui.nav('journey', e.target.value)}>
          {patients.map((p) => <option key={p.mrn} value={p.mrn}>{p.full_name}{p.mrn === 'SYN-0007' ? ' (demo)' : ''}</option>)}
        </select>
      </div>
      {error && <div className="card"><div className="error">{error}</div></div>}
      {t && (
        <div className="card">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
            <div className="v4-name">
              <a role="button" tabIndex={0} onClick={() => ui.nav('plan', mrn)} style={{ color: 'inherit', textDecoration: 'none' }}>{t.full_name}</a>{' '}
              {patientStatusPill(t.status)}
              <div className="v4-meta">{mrn}{t.therapy ? ` · ${t.therapy}` : t.plan ? ` · plan ${t.plan} confirmed` : ' · follow-up plan not confirmed yet'}</div>
            </div>
            <div className="seg">
              {FILTERS.map(([id, label]) => (
                <button key={id} type="button" className={filter === id ? 'on' : ''} onClick={() => setFilter(id)}>{label}</button>
              ))}
            </div>
          </div>
          <div className="v31-sum">
            <Pill tone={gaps ? 'bad' : 'good'}>Origin trace: {t.traced} of {t.need_origin} traced{gaps ? ` · ${gaps} gap${gaps > 1 ? 's' : ''}` : ''}</Pill>
            <Pill tone="ai">{t.milestones} defining milestones</Pill>
          </div>
          <div className="tl" style={{ marginTop: 14 }}>
            {items.map((e, i) => {
              const cls = e.kind === 'milestone' ? 'milestone' : e.kind === 'gap' ? 'gap' : 'routine';
              const gap = e.kind === 'routine' && !e.origin;
              const [tierCls, tierLabel] = TIER[e.tier] || ['hosp', e.tier];
              return (
                <div key={i} className={`tl-item ${cls}${e.tier === 'patient' ? ' ext' : ''}${gap ? ' v31gap' : ''}`}>
                  <div className="tl-date">{fmt(e.date)}</div>
                  <div className="tl-label">{e.label}</div>
                  <div className="tl-meta">
                    {e.kind === 'milestone' && <><span className="tag" style={{ background: 'var(--ai-tint)', color: 'var(--ai-deep)' }}>Milestone</span> </>}
                    <span className={`tag ${tierCls}`}>{tierLabel}</span>{' '}
                    {gap && <><span className="tag live">Origin gap</span> </>}
                    <span className="src-link">{e.src}</span>
                  </div>
                  {e.kind === 'gap'
                    ? <div className="v31-origin">↳ Raised automatically by the morning gap check · <b>rule {e.rule}</b></div>
                    : gap ? <div className="v31-origin gap">No order, request or episode on file explains this item. Not assumed; shown to the care team.</div>
                      : e.origin ? <div className="v31-origin">↳ Origin: <b>{e.origin}</b></div> : null}
                </div>
              );
            })}
            {!items.length && <div className="small-note">Nothing matches this filter.</div>}
          </div>
        </div>
      )}
    </>
  );
}
