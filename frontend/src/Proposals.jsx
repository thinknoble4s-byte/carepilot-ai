// Use Case 2a — a proposal: the AI's draft for a reply that did not mention a health matter.
// Approve means "I read the message and the draft is a fair description"; it changes no card and sends nothing.
import React, { useState } from 'react';
import { post } from './api.js';
import { useUI, VoiceClip } from './ui.jsx';

export default function ProposalCard({ p, onDone }) {
  const ui = useUI();
  const [summary, setSummary] = useState(p.summary || '');
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const act = async (path, body, msg) => {
    setError('');
    try {
      await post(path, body);
      ui.toast(msg);
      if (onDone) onDone();
      ui.refresh();
    } catch (e) {
      setError(e.message);
    }
  };
  const edited = summary.trim() && summary.trim() !== (p.summary || '');
  return (
    <div className="v4-item req">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <div className="t">{p.full_name ? `${p.full_name} — ` : ''}Proposal to approve</div>
        <span className="tag">Routine reminders on hold</span>
      </div>
      <div className="fb" style={{ marginTop: 6, fontSize: 12.5 }}>
        <b>Patient {p.kind === 'voice' ? 'said (voice note)' : 'wrote'}:</b> "{p.transcript || p.text}"
        {p.translation && <div className="small-note">English (AI translation, Sarvam): {p.translation}</div>}
        {p.kind === 'voice' && <div style={{ marginTop: 6 }}><VoiceClip inboundId={p.inbound_id} /></div>}
      </div>
      {p.draft_failed
        ? <div className="d">The AI could not draft a summary. Read the message and write one.</div>
        : <div className="d">AI draft — intent <b>{(p.intent || 'other').replaceAll('_', ' ')}</b>{p.intent_reason ? ` (${p.intent_reason})` : ''}</div>}
      <label className="v3-field" htmlFor={`ps${p.proposal_id}`}>Summary (edit if needed)</label>
      <input id={`ps${p.proposal_id}`} className="v3-in" value={summary} onChange={(e) => setSummary(e.target.value)} />
      <div className="small-note">Approving changes no card and sends nothing. It lifts the hold on routine reminders.</div>
      <div className="btn-row">
        <button type="button" className="btn sm good" disabled={!summary.trim()}
          onClick={() => act(`proposals/${p.proposal_id}/approve`, { summary: edited ? summary : null },
            edited ? 'Approved with your edit — hold lifted' : 'Approved — hold lifted')}>Approve{edited ? ' (edited)' : ''}</button>
        <button type="button" className="btn sm" onClick={() => setRejecting(true)}>Reject</button>
        {p.mrn && ui.role === 'coordinator' && <button type="button" className="btn sm ghost" onClick={() => ui.nav('plan', p.mrn)}>Open</button>}
      </div>
      {rejecting && (
        <>
          <label className="v3-field" htmlFor={`pr${p.proposal_id}`}>Reason for rejecting (required)</label>
          <input id={`pr${p.proposal_id}`} className="v3-in" value={reason} onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. The draft misses that she asked for a morning slot" />
          <div className="btn-row">
            <button type="button" className="btn sm primary" disabled={!reason.trim()}
              onClick={() => act(`proposals/${p.proposal_id}/reject`, { reason }, 'Rejected — hold lifted')}>Reject the draft</button>
          </div>
        </>
      )}
      {error && <div className="error">{error}</div>}
    </div>
  );
}
