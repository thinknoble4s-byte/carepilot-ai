import React, { useEffect, useState } from 'react';
import { api } from './api.js';
import { ScreenHead } from './ui.jsx';

export default function AuditTrail({ users, role }) {
  const [audit, setAudit] = useState(null);
  const [error, setError] = useState('');
  const names = Object.fromEntries(users.map((u) => [u.id, u.person]));

  useEffect(() => {
    api('audit?limit=50')
      .then(setAudit)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <>
    <ScreenHead eyebrow={role === 'oncologist' ? 'Oncologist · read only' : 'Governance'} title="Audit trail"
      sub="Every action on every patient — by the CarePilot agent, by people, by the system and by AI — with who did it and when." />
    <main>
      <section className="card wide">
        {error && <p className="error">{error}</p>}
        {audit && (
          <>
            <p className={audit.chain.valid ? 'muted' : 'error'}>
              Hash chain {audit.chain.valid ? 'intact' : `broken at entry ${audit.chain.firstBadId}`} ·{' '}
              {audit.chain.checked} entries checked · latest 50 shown
            </p>
            <div className="tablewrap">
              <table className="dtab">
                <thead>
                  <tr><th>#</th><th>When</th><th>Actor</th><th>Action</th><th>Patient</th><th>Entity</th><th>Hash</th></tr>
                </thead>
                <tbody>
                  {audit.entries.map((e) => (
                    <tr key={e.id}>
                      <td>{e.id}</td>
                      <td>{new Date(e.occurred_at).toLocaleString()}</td>
                      <td>
                        {names[e.actor_name] || e.actor_name} <span className="muted">({e.actor_type})</span>
                      </td>
                      <td>{e.action}</td>
                      <td>{e.mrn || ''}</td>
                      <td>{[e.entity_type, e.entity_id].filter(Boolean).join(' / ')}</td>
                      <td><code>{e.hash.slice(0, 10)}</code></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </main>
    </>
  );
}
