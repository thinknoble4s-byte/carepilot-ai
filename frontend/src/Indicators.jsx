// R6 — Performance Indicators (prototype v7.2 scrKpis). Aggregates only; nothing invented.
import React, { useEffect, useState } from 'react';
import { api } from './api.js';
import { ScreenHead, useUI } from './ui.jsx';

export default function Indicators() {
  const ui = useUI();
  const [k, setK] = useState(null);
  useEffect(() => {
    api('kpis').then(setK).catch(() => {});
  }, []);
  const tile = (n, l, s) => (
    <div className="card" style={{ margin: 0 }}>
      <div style={{ fontSize: 26, fontWeight: 800, color: 'var(--ink)' }}>{n}</div>
      <div className="small-note" style={{ fontWeight: 700 }}>{l}</div>
      <div className="small-note">{s}</div>
    </div>
  );
  return (
    <>
      <ScreenHead eyebrow={`Chief of hospital · ${ui.user?.person || ''}`} title="Performance Indicators"
        sub="How well the follow-up programme keeps survivors connected. Aggregates only — no patient names or records on this screen." />
      {k && (
        <>
          <div className="card kpi-hero">
            <div className="small-note" style={{ fontWeight: 700 }}>Pilot KPI · recall completion</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 18, flexWrap: 'wrap', marginTop: 4 }}>
              <div style={{ fontSize: 46, fontWeight: 800, color: 'var(--ink)', lineHeight: 1 }}>—</div>
              <div className="small-note">{k.recall_completion_note}</div>
            </div>
            <div className="bar-row" style={{ marginTop: 12 }}>
              <div className="bar-label">Baseline</div><div className="bar-track" /><div className="bar-val">—</div>
            </div>
            <div className="bar-row"><div className="bar-label">Pilot</div><div className="bar-track" /><div className="bar-val">—</div></div>
            <div className="small-note" style={{ marginTop: 6 }}>{k.baseline_note} No baseline or completion figure is shown until it is measured.</div>
          </div>
          <div className="grid-4" style={{ marginBottom: 14, gridTemplateColumns: 'repeat(auto-fit, minmax(170px, 1fr))' }}>
            {tile(`${k.agent_share}%`, 'Coordination done by the agent', `${k.agent_actions} agent actions vs ${k.human_actions} person actions`)}
            {tile(k.handover_response_median_days == null ? '—' : k.handover_response_median_days === 0 ? 'Same day'
              : `${k.handover_response_median_days} working day${k.handover_response_median_days === 1 ? '' : 's'}`,
              'Handover response', `median from a handover opening to the coordinator's decision (${k.handovers_decided} decided)`)}
            {tile(k.handover_response_median_minutes == null ? '—' : `${k.handover_response_median_minutes} min`,
              'Handover response (demo clock)', 'median minutes from the agent stopping to the coordinator reviewing')}
            {tile(k.time_to_call_median_minutes == null ? '—' : `${k.time_to_call_median_minutes} min`, 'Urgent: time to call',
              `median minutes from the doctor's Urgent choice to the call that reached the patient (${k.urgent_calls_reached} reached${k.time_to_call_max_minutes != null ? `, longest ${k.time_to_call_max_minutes} min` : ''})`)}
            {tile(k.draft_acceptance == null ? '—' : `${k.draft_acceptance}%`, 'AI drafts accepted unedited',
              `${k.proposals_approved_unedited} of ${k.proposals_decided} proposals decided`)}
            {tile(`${k.g0_booked_after_reminder} of ${k.g0_gaps}`, 'Booked after the T-7 reminder', 'G0 gaps whose item left "Due"')}
            {tile(k.not_responding, 'Not responding', 'patients with 2+ messages in a row unanswered')}
            {tile(k.lost_to_follow_up, 'Lost to follow-up', 'closed with a reason under the agreed definition')}
            {tile(k.open_handovers, 'Open handovers', 'automated messages stopped for these patients')}
            {tile(k.messages_sent, 'Messages sent', `${k.sends_blocked} blocked by the send-guard`)}
            {tile(`${k.plans_confirmed} of ${k.patients}`, 'Plans confirmed', 'patients with order cards')}
          </div>
          <div className="v4-plan-note">Synthetic cohort of {k.patients} patients on the breast cancer survivorship plan. The numbers show the calculations work, not that outcomes improved. In the pilot only aggregate numbers leave the clinic.</div>
        </>
      )}
    </>
  );
}
