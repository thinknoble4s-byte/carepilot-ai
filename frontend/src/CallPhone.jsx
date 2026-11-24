// The patient's phone for a simulated call, shared by the DEXA voice escalation and Voice Triage.
import React, { useEffect, useState } from 'react';

// An original two-note ring made in the browser (no recording, not WhatsApp's tone). Returns what stops it.
export function startRingtone() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return () => {};
  const ctx = new Ctx();
  const ring = () => {
    [[659, 0], [880, 0.22], [659, 0.6], [880, 0.82]].forEach(([hz, after]) => {
      const at = ctx.currentTime + after;
      const tone = ctx.createOscillator();
      const gain = ctx.createGain();
      tone.frequency.value = hz;
      gain.gain.setValueAtTime(0.0001, at);
      gain.gain.exponentialRampToValueAtTime(0.22, at + 0.03);
      gain.gain.exponentialRampToValueAtTime(0.0001, at + 0.3);
      tone.connect(gain).connect(ctx.destination);
      tone.start(at);
      tone.stop(at + 0.32);
    });
  };
  Promise.resolve(ctx.resume()).catch(() => {});
  ring();
  const timer = setInterval(ring, 2200);
  return () => {
    clearInterval(timer);
    Promise.resolve(ctx.close()).catch(() => {});
  };
}

const Handset = () => (
  <svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor" aria-hidden="true">
    <path d="M6.6 10.8a15 15 0 0 0 6.6 6.6l2.2-2.2c.3-.3.7-.4 1-.2 1.1.4 2.3.6 3.6.6.6 0 1 .4 1 1V20c0 .6-.4 1-1 1A17 17 0 0 1 3 4c0-.6.4-1 1-1h3.4c.6 0 1 .4 1 1 0 1.3.2 2.5.6 3.6.1.3 0 .7-.2 1z" />
  </svg>
);

function CallTimer() {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setSecs((n) => n + 1), 1000);
    return () => clearInterval(timer);
  }, []);
  return <div className="vc-timer-run">{Math.floor(secs / 60)}:{String(secs % 60).padStart(2, '0')}</div>;
}

// The patient's phone while the call rings or runs: only what a patient would see, plus a safety stop.
export function CallFace({ phase, clinic, speaking, stop, now, busy, onAccept, onReject }) {
  const text = { gate: 'Checking before dialling…', blocked: 'No call placed', ringing: 'Incoming voice call', ended: 'Call ended' }[phase]
    || (speaking === 'agent' ? 'Speaking…' : speaking === 'patient' ? 'You are speaking…' : speaking === 'thinking' ? 'Listening…' : 'Connected');
  const dialled = !['gate', 'blocked'].includes(phase);
  return (
    <div className="wa-phone vc-phone">
      <div className="wa-status"><span>{now || ''}</span><span aria-hidden="true">▂▄▆ ᯤ ▮</span></div>
      <div className="vc-face">
        {stop && <div className="vc-stop" role="alert">Safety stop</div>}
        {dialled && (
          <>
            <div className={`vc-avatar ${phase === 'ringing' || speaking === 'agent' ? 'on' : ''}`} aria-hidden="true">
              {clinic.split(' ').map((w) => w[0]).slice(0, 2).join('')}</div>
            <div className="vc-name">{clinic}</div>
          </>
        )}
        <div className="vc-state" aria-live="polite">{text}</div>
        {phase === 'live' && (
          <>
            <CallTimer />
            <div className={`vc-wave ${speaking || ''}`} aria-hidden="true">
              {Array.from({ length: 13 }, (_, i) => <i key={i} style={{ animationDelay: `${i * 70}ms` }} />)}
            </div>
          </>
        )}
        {phase === 'ringing' && onAccept && (
          <div className="vc-answer">
            <button type="button" className="vc-answer-btn reject" disabled={busy} onClick={onReject}><span><Handset /></span>Reject</button>
            <button type="button" className="vc-answer-btn accept" disabled={busy} onClick={onAccept}><span><Handset /></span>Accept</button>
          </div>
        )}
      </div>
    </div>
  );
}
