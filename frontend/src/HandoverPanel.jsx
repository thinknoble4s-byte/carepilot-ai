import React, { useState } from 'react';
import { post } from './api.js';

export const STAGE_LABEL = {
  awaiting_coordinator: 'Waiting for the coordinator (Gate 1)',
  calling: 'Coordinator to call the patient',
  awaiting_doctor: "Waiting for the doctor's decision (Gate 2)",
  awaiting_instruction: "Waiting for the doctor's instruction (Gate 3)",
  ready_to_close: 'Ready to close',
  closed: 'Closed',
};

const GATE1 = [
  ['send_to_doctor', 'Send to doctor'],
  ['call_first', 'Call the patient first'],
  ['nothing_clinical', 'Nothing clinical'],
];

export default function HandoverPanel({ mrn, handover, tasks, role, onChanged }) {
  const [error, setError] = useState('');
  const coordinator = role === 'coordinator';
  const open = handover && handover.stage !== 'closed';
  const urgentOpen = tasks.some((t) => t.task_type === 'urgent_call' && t.handover_id === handover?.handover_id);

  const act = async (path, body) => {
    setError('');
    try {
      await post(path, body);
      onChanged();
    } catch (e) {
      setError(e.message);
    }
  };
  const ask = (question, path, key) => {
    const value = window.prompt(question);
    if (value !== null) act(path, { [key]: value });
  };

  return (
    <section className={`card ${open ? 'alert' : ''}`}>
      <h2>Handover</h2>
      {!handover && <p className="muted">No handover. The agent may send approved reminders.</p>}
      {handover && (
        <>
          <p><strong>{STAGE_LABEL[handover.stage]}</strong></p>
          {open && <p className="warn-text">Automated messages to this patient are stopped until a person closes this.</p>}
          {handover.patient_words && (
            <blockquote>
              {handover.patient_words}
              {handover.translation && <div className="en">English: {handover.translation}</div>}
              <div className="en">Source: {handover.source}</div>
            </blockquote>
          )}
          <dl className="facts">
            {handover.gate1_choice && (<><dt>Gate 1</dt><dd>{handover.gate1_choice.replaceAll('_', ' ')} ({handover.gate1_by})</dd></>)}
            {handover.call_note && (<><dt>Call</dt><dd>{handover.call_note}</dd></>)}
            {handover.gate2_decision && (<><dt>Gate 2</dt><dd>{handover.gate2_decision} ({handover.gate2_by})</dd></>)}
            {handover.instruction && (<><dt>Gate 3</dt><dd>{handover.instruction} ({handover.instruction_by})</dd></>)}
            {handover.closed_by && (<><dt>Closed</dt><dd>{handover.closed_reason} ({handover.closed_by}, {handover.closed_on})</dd></>)}
          </dl>
        </>
      )}
      {coordinator && open && handover.stage === 'awaiting_coordinator' && (
        <div className="row">
          {GATE1.map(([id, label]) => (
            <button key={id} type="button" className="btn"
              onClick={() => act(`handovers/${handover.handover_id}/gate1`, { choice: id })}>{label}</button>
          ))}
        </div>
      )}
      {coordinator && open && (handover.stage === 'calling' || urgentOpen) && (
        <button type="button" className="btn"
          onClick={() => ask('Call outcome?', `handovers/${handover.handover_id}/call`, 'note')}>
          {urgentOpen ? 'Record the urgent call' : 'Record the call'}
        </button>
      )}
      {coordinator && open && handover.stage === 'ready_to_close' && (
        <button type="button" className="btn primary"
          onClick={() => ask('Reason for closing (your name is recorded)?', `handovers/${handover.handover_id}/close`, 'reason')}>
          Close handover
        </button>
      )}
      {coordinator && !open && (
        <button type="button" className="btn"
          onClick={() => ask('Why are you opening a handover?', `patients/${mrn}/handover`, 'reason')}>
          Open a handover
        </button>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
