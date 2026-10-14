// Shared building blocks that mirror the prototype (v7.2): screen heading, pop-up, notification, dates.
import React, { createContext, useContext } from 'react';

export const UI = createContext({ toast: () => {}, showModal: () => {}, closeModal: () => {}, refresh: () => {} });
export const useUI = () => useContext(UI);

// The prototype's fmt(): "22 Sept 2026".
export function fmt(iso) {
  if (!iso) return '—';
  const d = new Date(`${String(iso).slice(0, 10)}T00:00:00`);
  return d.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

export function ScreenHead({ eyebrow, title, sub }) {
  return (
    <div className="screen-head">
      <div>
        <div className="screen-eyebrow">{eyebrow}</div>
        <h1 className="screen-title">{title}</h1>
        {sub && <div className="screen-sub">{sub}</div>}
      </div>
    </div>
  );
}

export const GUARD = (
  <div className="v4-guard">
    CarePilot organises, reminds, tracks and routes. It does not diagnose, prescribe, score clinical risk, interpret
    results or advise patients. Every AI suggestion is confirmed by a named person, and every step is logged.
  </div>
);

export function Modal({ modal, onClose }) {
  if (!modal) return null;
  return (
    <div className="modal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal modal-wrap" role="dialog" aria-modal="true" style={modal.wide ? { maxWidth: 920 } : undefined}>
        <button type="button" className="modal-close" aria-label="Close" onClick={onClose}>&times;</button>
        <h3>{modal.title}</h3>
        {modal.sub && <div className="modal-sub">{modal.sub}</div>}
        {modal.body}
      </div>
    </div>
  );
}

export function Scope() {
  return (
    <div className="grid-2">
      <div className="card-flat">
        <div className="card-title">It does</div>
        <div className="small-note" style={{ lineHeight: 1.8 }}>
          Tracks the follow-up plan as order cards with named owners · checks for gaps every morning, including before
          anything is missed · sends approved messages to patients and caregivers in their language · logs button
          replies and structures free text for a person to confirm · holds outside documents unverified until a person
          checks · asks about care elsewhere · routes concerns to the doctor with a deadline · picks up instructions
          from the hospital record or a paper note · runs repetitive coordination through an AI agent within an
          autonomy policy, stopping and handing over the moment a patient mentions a health matter · logs every step
        </div>
      </div>
      <div className="card-flat">
        <div className="card-title">It does not</div>
        <div className="small-note" style={{ lineHeight: 1.8 }}>
          Diagnose · prescribe · recommend treatment · score clinical risk · interpret results or symptoms · advise
          patients · resolve differences between sources on its own · ask doctors to enter orders into another system
        </div>
      </div>
    </div>
  );
}

export function Placeholder({ what, when }) {
  return (
    <div className="card">
      <div className="empty">
        {what} arrives in {when}. Nothing here is mocked: this screen fills from real records once that step is built.
      </div>
    </div>
  );
}
