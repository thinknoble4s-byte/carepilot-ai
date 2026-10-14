import React, { useState } from 'react';
import { post } from './api.js';

const STATUS_LABEL = {
  sent: null,
  blocked: 'Blocked',
  pending_approval: 'Waiting for approval',
  rejected: 'Rejected',
};

// The simulated WhatsApp thread. The coordinator plays the patient to demo replies.
export default function Simulator({ mrn, messages, canReply, onChanged }) {
  const [text, setText] = useState('');
  const [error, setError] = useState('');

  const send = async (body) => {
    setError('');
    try {
      await post(`simulator/${mrn}/reply`, body);
      setText('');
      onChanged();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <section className="card wide">
      <h2>WhatsApp (simulated)</h2>
      <p className="muted">
        Messages to and from the patient. Only approved templates go out, through the send-guard. Buttons update the
        workflow directly; free text always goes to a person.
      </p>
      <div className="phone">
        {messages.length === 0 && <p className="muted">No messages yet.</p>}
        {messages.map((m) => (
          <div key={`${m.direction}-${m.id}`} className={`bubble ${m.direction} ${m.status || ''}`}>
            <div className="meta">
              {m.direction === 'out' ? `To ${m.party} · ${m.template_id}` : `From ${m.party}`} · {m.day}
              {STATUS_LABEL[m.status] && (
                <span className="pill warn">
                  {STATUS_LABEL[m.status]}
                  {m.blocked_reason ? `: ${m.blocked_reason}` : ''}
                </span>
              )}
            </div>
            {m.body && <div>{m.body}</div>}
            {m.direction === 'out' && m.body_en && m.body_en !== m.body && <div className="en">{m.body_en}</div>}
            {m.direction === 'in' && m.button && <div className="tap">Tapped: {m.button}</div>}
            {m.direction === 'in' && <div className="en">Handled: {m.handling}</div>}
            {m.direction === 'out' && m.status === 'sent' && m.buttons?.length > 0 && (
              <div className="row">
                {m.buttons.map((b) => (
                  <button key={b.id} type="button" className="btn sm" disabled={!canReply}
                    title={b.label_en} onClick={() => send({ button: b.id, in_reply_to: m.id })}>
                    {b.label}
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
      {canReply && (
        <form className="row reply" onSubmit={(e) => { e.preventDefault(); if (text.trim()) send({ text }); }}>
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Type a reply as the patient" />
          <button type="submit" className="btn primary" disabled={!text.trim()}>Send as patient</button>
          <button type="button" className="btn" onClick={() => send({ text: 'HELP' })}>HELP</button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
