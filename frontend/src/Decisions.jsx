import React, { useCallback, useEffect, useState } from 'react';
import { api, post } from './api.js';
import { STAGE_LABEL } from './HandoverPanel.jsx';

const DEPARTMENTS = ['Oncology OPD', 'Laboratory', 'Radiology'];

function Gate2({ item, onDone }) {
  const [decision, setDecision] = useState(null); // nothing pre-selected
  const [error, setError] = useState('');
  const submit = async () => {
    try {
      await post(`handovers/${item.handover_id}/gate2`, { decision });
      onDone();
    } catch (e) {
      setError(e.message);
    }
  };
  return (
    <div className="stack">
      <fieldset>
        <legend>Your decision (required, nothing is pre-selected)</legend>
        {['urgent', 'routine'].map((d) => (
          <label key={d} className="check">
            <input type="radio" name={`d-${item.handover_id}`} checked={decision === d} onChange={() => setDecision(d)} />
            {d === 'urgent' ? 'Urgent' : 'Routine'}
          </label>
        ))}
      </fieldset>
      <button type="button" disabled={!decision} onClick={submit}>Submit decision</button>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function Gate3({ item, onDone }) {
  const [text, setText] = useState('');
  const [withOrder, setWithOrder] = useState(true);
  const [order, setOrder] = useState({ name: 'Surgical-site review', department: 'Oncology OPD', card_type: 'visit', within_days: 7 });
  const [error, setError] = useState('');
  const submit = async (e) => {
    e.preventDefault();
    try {
      await post(`handovers/${item.handover_id}/instruction`, { text, order: withOrder ? order : null });
      onDone();
    } catch (err) {
      setError(err.message);
    }
  };
  const set = (k) => (e) => setOrder({ ...order, [k]: k === 'within_days' ? Number(e.target.value) : e.target.value });
  return (
    <form className="stack" onSubmit={submit}>
      <label>Instruction (recorded in the simulated hospital record)
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} placeholder="e.g. Surgical-site review within 7 days" />
      </label>
      <label className="check">
        <input type="checkbox" checked={withOrder} onChange={(e) => setWithOrder(e.target.checked)} /> Create a new order card
      </label>
      {withOrder && (
        <div className="grid2">
          <label>Name <input value={order.name} onChange={set('name')} /></label>
          <label>Department
            <select value={order.department} onChange={set('department')}>
              {DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}
            </select>
          </label>
          <label>Type
            <select value={order.card_type} onChange={set('card_type')}>
              <option value="visit">Visit</option>
              <option value="test">Test</option>
            </select>
          </label>
          <label>Within (days) <input type="number" min="1" max="365" value={order.within_days} onChange={set('within_days')} /></label>
        </div>
      )}
      <button type="submit" disabled={!text.trim()}>Record instruction</button>
      {error && <p className="error">{error}</p>}
    </form>
  );
}

export default function Decisions({ onOpenPatient }) {
  const [queue, setQueue] = useState([]);
  const [error, setError] = useState('');
  const load = useCallback(() => {
    api('doctor/queue').then(setQueue).catch((e) => setError(e.message));
  }, []);
  useEffect(() => load(), [load]);

  return (
    <main>
      <section className="card wide">
        <h2>Needs my decision ({queue.length})</h2>
        <p className="muted">You see the patient's own words and their source. CarePilot does not suggest urgency.</p>
        {error && <p className="error">{error}</p>}
        {queue.length === 0 && <p className="muted">Nothing waiting for you.</p>}
      </section>
      {queue.map((item) => (
        <section key={item.handover_id} className="card wide alert">
          <h2>
            <button type="button" className="link" onClick={() => onOpenPatient(item.mrn)}>{item.full_name}</button>{' '}
            <span className="muted">({item.mrn}, {item.preferred_language})</span>
          </h2>
          <p><strong>{STAGE_LABEL[item.stage]}</strong>{item.due_date ? ` · due ${item.due_date}` : ''}</p>
          <blockquote>
            {item.patient_words}
            {item.translation && <div className="en">English: {item.translation}</div>}
            <div className="en">Source: {item.source}</div>
          </blockquote>
          {item.call_note && <p className="muted">Coordinator's call: {item.call_note}</p>}
          {item.gate2_decision && <p>Your decision: <strong>{item.gate2_decision}</strong></p>}
          {item.stage === 'awaiting_doctor' ? <Gate2 item={item} onDone={load} /> : <Gate3 item={item} onDone={load} />}
        </section>
      ))}
    </main>
  );
}
