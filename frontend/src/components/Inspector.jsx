import { useEffect, useState } from 'react'
import { clearLog, subscribe } from '../api.js'

function statusClass(s) {
  if (s === 0) return 's-fail'
  if (s < 300) return 's-ok'
  if (s < 500) return 's-client'
  return 's-server'
}

export default function Inspector({ open, onClose }) {
  const [entries, setEntries] = useState([])
  const [expanded, setExpanded] = useState(null)
  useEffect(() => subscribe(setEntries), [])

  return (
    <aside className={`inspector ${open ? 'is-open' : ''}`} aria-hidden={!open}>
      <div className="inspector-head">
        <h2>Requests</h2>
        <p>Every call this page made, newest first.</p>
        <div className="inspector-actions">
          <button className="btn-ghost-dark" onClick={clearLog}>Clear</button>
          <button className="btn-ghost-dark" onClick={onClose}>Close</button>
        </div>
      </div>
      {entries.length === 0 ? (
        <p className="inspector-empty">Nothing yet. Click around the shop and each request will appear here.</p>
      ) : (
        <ol className="inspector-list">
          {entries.map((e) => (
            <li key={e.id}>
              <button
                className="req-row"
                onClick={() => setExpanded(expanded === e.id ? null : e.id)}
                aria-expanded={expanded === e.id}
              >
                <span className={`req-status ${statusClass(e.status)}`}>{e.status || 'ERR'}</span>
                <span className="req-method">{e.method}</span>
                <span className="req-path">{e.path}</span>
                <span className="req-ms">{e.ms} ms</span>
              </button>
              {e.direct && <div className="req-note">Sent straight to MinIO. Spring Boot never saw this request.</div>}
              {expanded === e.id && (
                <div className="req-detail">
                  <div className="req-meta">
                    {e.at.toLocaleTimeString()} {e.userId ? `as user ${e.userId}` : 'with no X-User-Id'}
                  </div>
                  {e.requestBody !== undefined && (
                    <>
                      <h3>Request body</h3>
                      <pre>{typeof e.requestBody === 'string' ? e.requestBody : JSON.stringify(e.requestBody, null, 2)}</pre>
                    </>
                  )}
                  <h3>Response body</h3>
                  <pre>
                    {e.responseBody === null || e.responseBody === undefined
                      ? '(empty)'
                      : typeof e.responseBody === 'string'
                        ? e.responseBody
                        : JSON.stringify(e.responseBody, null, 2)}
                  </pre>
                </div>
              )}
            </li>
          ))}
        </ol>
      )}
    </aside>
  )
}
