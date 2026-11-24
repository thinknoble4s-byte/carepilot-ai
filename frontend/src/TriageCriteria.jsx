// Care Plan → triage tabs: what decides Red, Amber and Green, and how the simulated call sorts a reply.
// Read only, served from the rule code itself (/triage/criteria); tests/test_triage_criteria.py runs every line
// shown here through the rules, so the screen cannot drift from what the system does.
import React, { useEffect, useState } from 'react';
import { api } from './api.js';

const TONE = { red: ['Red', 'bad'], amber: ['Amber', 'warn'], green: ['Green', 'good'] };
const KIND = { yesno: 'Yes / No', number: 'Number', temperature: 'Number or “no thermometer”' };
const LABEL_TONE = { HEALTH_CONCERN: 'health', YES: 'yes', NO: 'no', UNCLEAR: 'unclear' };

let cache = null;
function useCriteria() {
  const [data, setData] = useState(cache);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!cache) api('triage/criteria').then((d) => { cache = d; setData(d); }).catch((e) => setError(e.message));
  }, []);
  return [data, error];
}

const Pill = ({ tier }) => <span className={`pill ${TONE[tier][1]}`}><span className="dot" />{TONE[tier][0]}</span>;

function Draft({ status }) {
  return (
    <div className="small-note" style={{ marginBottom: 10 }}>
      Protocol status <span className="tag unverified">{status}</span> — draft wording and thresholds, awaiting the
      clinical lead's sign-off. The AI never picks the tier: these fixed rules do.
    </div>
  );
}

export function TierTab({ tier }) {
  const [c, error] = useCriteria();
  if (error) return <div className="card"><div className="error">{error}</div></div>;
  if (!c) return <div className="card"><div className="empty">Loading…</div></div>;
  const t = c.tiers[tier];
  return (
    <>
      <Draft status={c.protocol_status} />
      <div className={`card vt-why ${TONE[tier][1]}`}>
        <div className="card-title"><Pill tier={tier} /> How a call becomes {TONE[tier][0]}</div>
        <ul style={{ margin: '4px 0 0', paddingLeft: 18, lineHeight: 1.7 }}>{t.rules.map((r) => <li key={r}>{r}</li>)}</ul>
      </div>
      <div className="card">
        <div className="card-title">What happens next</div>
        <div className="v4-scroll"><table className="v4-table">
          <thead><tr><th>When</th><th>Within</th><th>What happens</th><th>Action code</th></tr></thead>
          <tbody>{t.actions.map((a) => (
            <tr key={a.code + a.when}><td>{a.when}</td><td><b>{a.within}</b></td><td>{a.what}</td><td><code>{a.code}</code></td></tr>
          ))}</tbody>
        </table></div>
      </div>
      <div className="card">
        <div className="card-title">Questions whose answers lead to {TONE[tier][0]}</div>
        <div className="card-desc">The answer that gives {TONE[tier][0]}, assuming every other answer is calm. Hindi wording is a draft for a native-speaker check.</div>
        <div className="v4-scroll"><table className="v4-table">
          <thead><tr><th>Question</th><th>Asked</th><th>Answer</th><th>What the answer does</th></tr></thead>
          <tbody>{t.questions.map((q) => (
            <tr key={q.id}>
              <td><code>{q.id}</code>{q.danger && <> <span className="tag unverified">danger</span></>}
                <div>{q.text.English}</div><div className="small-note" lang="hi">{q.text.Hindi}</div></td>
              <td className="small-note">{q.asked}{q.in_fixed_12 && <div>· in the fixed 12</div>}</td>
              <td className="small-note">{KIND[q.kind]}</td>
              <td>{q.effects.map((e) => <div key={e.text}>{e.text}</div>)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </div>
      {t.reasons.length > 0 && (
        <div className="card">
          <div className="card-title">Every {TONE[tier][0]} reason code (shown on the episode as “Why this tier”)</div>
          <div className="v4-scroll"><table className="v4-table">
            <tbody>{t.reasons.map((r) => <tr key={r.code}><td><code>{r.code}</code></td><td>{r.text}</td></tr>)}</tbody>
          </table></div>
        </div>
      )}
      <div className="card">
        <div className="card-title">The numbers</div>
        <table className="v4-table"><tbody>{c.numbers.map(([k, v]) => <tr key={k}><td><b>{k}</b></td><td>{v}</td></tr>)}</tbody></table>
        <div className="card-title" style={{ marginTop: 12 }}>How the questions are chosen</div>
        <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.7 }}>{c.router.map((r) => <li key={r}>{r}</li>)}</ul>
      </div>
    </>
  );
}

export function CallTab() {
  const [c, error] = useCriteria();
  if (error) return <div className="card"><div className="error">{error}</div></div>;
  if (!c) return <div className="card"><div className="empty">Loading…</div></div>;
  const k = c.call;
  return (
    <>
      <div className="card">
        <div className="card-title">Simulated booking call (UC1-V): how a reply is sorted</div>
        <div className="card-desc">After each reply the AI picks one of four labels; fixed rules decide the next step. Prompt {k.prompt_version}.</div>
        <div className="v4-scroll"><table className="v4-table">
          <thead><tr><th>Label</th><th>What she said</th><th>What the agent does next</th></tr></thead>
          <tbody>{k.labels.map(([label, said, next]) => (
            <tr key={label}><td><span className={`vc-label ${LABEL_TONE[label]}`}>{label.replace('_', ' ')}</span></td><td>{said}</td><td>{next}</td></tr>
          ))}</tbody>
        </table></div>
        <ul style={{ margin: '10px 0 0', paddingLeft: 18, lineHeight: 1.7 }}>{k.rules.map((r) => <li key={r}>{r}</li>)}</ul>
      </div>
      <div className="card vt-why bad">
        <div className="card-title">When the technology fails, a person takes over</div>
        <div className="card-desc">From documents/TechExceptions.xlsx; each row has an automated test (tests/test_tech_exceptions.py).</div>
        <div className="v4-scroll"><table className="v4-table">
          <thead><tr><th>Fault</th><th>Result</th><th>What the handover says</th></tr></thead>
          <tbody>{k.faults.map(([f, r, why]) => <tr key={f}><td>{f}</td><td><b>{r}</b></td><td className="small-note">{why}</td></tr>)}</tbody>
        </table></div>
      </div>
      <div className="card">
        <div className="card-title">Words that stop the call before the AI is asked (unless she plainly denies them)</div>
        <div className="small-note" style={{ marginBottom: 6 }}>Hindi</div>
        <div lang="hi">{k.health_words_hi.map((w) => <span key={w} className="tag" style={{ margin: '0 4px 4px 0' }}>{w}</span>)}</div>
        <div className="small-note" style={{ margin: '8px 0 6px' }}>English (in the AI translation)</div>
        <div>{k.health_words_en.map((w) => <span key={w} className="tag" style={{ margin: '0 4px 4px 0' }}>{w}</span>)}</div>
      </div>
      <div className="card">
        <div className="card-title">What the agent can say (fixed lines only)</div>
        <div className="v4-scroll"><table className="v4-table">
          <thead><tr><th>Line</th><th>When</th><th>Words</th><th>Status</th></tr></thead>
          <tbody>{k.lines.map((l) => (
            <tr key={l.id}><td><b>{l.id}</b></td><td className="small-note">{l.when}</td>
              <td>{l.en}<div className="small-note" lang="hi">{l.hi}</div></td>
              <td><span className={`tag ${l.status === 'APPROVED' ? 'verified' : 'unverified'}`}>{l.status}</span></td></tr>
          ))}</tbody>
        </table></div>
      </div>
    </>
  );
}
