import React, { useCallback, useEffect, useState } from 'react';
import { api, post, upload } from './api.js';
import { Placeholder, ScreenHead, useUI } from './ui.jsx';

function ImportPanel({ sourceType, onChanged }) {
  const [file, setFile] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');

  const submit = async (e) => {
    e.preventDefault();
    if (!file) return;
    const form = new FormData();
    form.append('source_type', sourceType);
    form.append('file', file);
    setError('');
    try {
      setResult(await upload('imports', form));
      onChanged();
    } catch (err) {
      setResult(null);
      setError(err.message);
    }
  };

  return (
    <section className="card">
      <h2>{sourceType === 'register' ? 'Upload the follow-up register' : "Upload today's appointment list"}</h2>
      <form className="stack" onSubmit={submit}>
        <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files[0] || null)} />
        <button type="submit" className="btn primary" disabled={!file}>Import</button>
      </form>
      {result && (
        <p className="muted">
          {result.rows_read} rows read · {result.rows_created} created · {result.rows_skipped_identical} already
          present · {result.rows_exception} exceptions
        </p>
      )}
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function Exceptions({ items, onChanged }) {
  const dismiss = async (id) => {
    const reason = window.prompt('Reason for dismissing this exception?');
    if (reason === null) return;
    try {
      await post(`exceptions/${id}/dismiss`, { reason });
      onChanged();
    } catch (e) {
      window.alert(e.message);
    }
  };
  return (
    <section className="card wide">
      <h2>Open exceptions ({items.length})</h2>
      <p className="muted">Rows that could not be used. Nothing is created from them until someone fixes the source.</p>
      <div className="tablewrap">
        <table className="dtab">
          <thead>
            <tr><th>File</th><th>Line</th><th>Reason</th><th>Row</th><th /></tr>
          </thead>
          <tbody>
            {items.map((x) => (
              <tr key={x.exception_id}>
                <td>{x.source_type}</td>
                <td>{x.row_number}</td>
                <td><code>{x.reason_code}</code></td>
                <td><code>{x.raw_row}</code></td>
                <td><button type="button" className="btn sm" onClick={() => dismiss(x.exception_id)}>Dismiss</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}


const TABS = [['docs', 'Documents'], ['appt', 'Appointment list'], ['reg', 'Follow-up register']];

export default function Intake() {
  const [tab, setTab] = useState('appt');
  const [exceptions, setExceptions] = useState([]);
  const { refresh } = useUI();

  const load = useCallback(() => {
    api('exceptions').then(setExceptions).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const changed = () => {
    load();
    refresh();
  };
  const source = tab === 'reg' ? 'register' : 'appointments';

  return (
    <>
      <ScreenHead eyebrow="Care coordinator · everything arriving from outside" title="Intake & verification"
        sub="Documents, appointment lists and registers in the form the clinic already has them — photos, PDFs, Excel, WhatsApp. Nothing from outside is treated as fact until a person verifies it." />
      <div className="seg" style={{ marginBottom: 14 }}>
        {TABS.map(([id, label]) => (
          <button key={id} type="button" className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>{label}</button>
        ))}
      </div>
      {tab === 'docs' ? (
        <Placeholder what="Document intake (photo or PDF, AI reading, verification against two identifiers)"
          when="a later increment (AI reading starts in Increment 4)" />
      ) : (
        <main>
          <ImportPanel key={source} sourceType={source} onChanged={changed} />
          <Exceptions items={exceptions.filter((x) => x.source_type === source)} onChanged={changed} />
        </main>
      )}
    </>
  );
}
