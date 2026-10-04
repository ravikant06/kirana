import { useEffect, useState } from 'react'
import { clearLog, subscribe } from '../api.js'

// A request running this many statements or more is highlighted: often an N+1.
const SQL_WARN = 10

const tokens = new Intl.NumberFormat('en-IN')

function aiSummary(u) {
  const cost = u.cost ? ` · $${Number(u.cost).toFixed(4)}` : ''
  const ttft = u.firstTokenMs != null ? ` · 1st token ${(u.firstTokenMs / 1000).toFixed(1)} s` : ''
  return `${u.calls} LLM · ${tokens.format(u.inputTokens + u.outputTokens)} tok${ttft}${cost}`
}

// One line per SSE event: the interesting part of its data, not the whole JSON.
function summarize({ event, data }) {
  if (event === 'token') return JSON.stringify(data.text)
  if (event === 'status') return `${data.tool} “${data.query}”`
  if (event === 'step') return `${data.tool} → ${data.count} hit(s)${data.below_floor ? `, ${data.below_floor} below floor` : ''}`
  if (event === 'citation') return data.title || data.source
  if (event === 'done') return `${data.usage?.llm_calls} LLM calls · first token ${((data.usage?.first_token_ms || 0) / 1000).toFixed(1)} s`
  if (event === 'error') return `${data.status} ${data.title}`
  if (event === 'start') return `thread ${String(data.thread_id).slice(0, 8)}…`
  return ''
}

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
                <span className="req-path">
                  {e.service === 'ai' && <span className="req-svc">AI</span>}
                  {e.path}
                </span>
                <span className="req-ms">
                  {e.ms} ms
                  {e.queries != null && (
                    <span className={`req-sql ${e.queries >= SQL_WARN ? 'is-hot' : ''}`}>
                      {e.queries} SQL · {e.dbMs} ms DB{e.cache ? ` · ${e.cache}` : ''}
                    </span>
                  )}
                  {e.aiUsage && <span className="req-sql req-ai">{aiSummary(e.aiUsage)}</span>}
                  {e.idempotencyKey && (
                    <span className="req-sql">
                      key{e.attempt > 1 ? ` · retry ${e.attempt - 1}` : ''}{e.replayed ? ' · replayed' : ''}
                    </span>
                  )}
                </span>
              </button>
              {e.direct && <div className="req-note">Sent straight to MinIO. Spring Boot never saw this request.</div>}
              {expanded === e.id && (
                <div className="req-detail">
                  <div className="req-meta">
                    {e.at.toLocaleTimeString()} {e.userId ? `as user ${e.userId}` : 'with no X-User-Id'}
                  </div>
                  {e.queries != null && (
                    <div className="req-meta">
                      Backend ran {e.queries} SQL {e.queries === 1 ? 'statement' : 'statements'}, {e.dbMs} ms inside the
                      database, out of {e.ms} ms for the whole request.
                      {e.cache === 'HIT' && ' Answered from the Redis cache.'}
                      {e.cache === 'MISS' && ' Cache miss: loaded from Postgres and stored in Redis.'}
                      {e.cache === 'BYPASS' && ' Redis was unavailable, so Postgres answered directly.'}
                    </div>
                  )}
                  {e.idempotencyKey && (
                    <div className="req-meta">
                      Idempotency-Key <code>{e.idempotencyKey}</code>
                      {e.attempt > 1 && `, attempt ${e.attempt} of the same action`}.
                      {e.replayed && ' The server had already done this action: it replayed the stored response and did nothing new.'}
                    </div>
                  )}
                  {e.aiUsage && (
                    <div className="req-meta">
                      The AI service made {e.aiUsage.calls} LLM {e.aiUsage.calls === 1 ? 'call' : 'calls'}:{' '}
                      {tokens.format(e.aiUsage.inputTokens)} input and {tokens.format(e.aiUsage.outputTokens)} output
                      tokens (thinking included),{' '}
                      {e.aiUsage.cost ? `$${e.aiUsage.cost}` : 'cost unknown (no price in pricing.yaml)'}.
                    </div>
                  )}
                  {e.events?.length > 0 && (
                    <>
                      <h3>Stream timeline ({e.events.length} events)</h3>
                      <ol className="req-timeline">
                        {e.events.map((ev, i) => (
                          <li key={i} className={`ev-${ev.event}`}>
                            <span className="ev-t">+{(ev.t / 1000).toFixed(2)} s</span>
                            <span className="ev-name">{ev.event}</span>
                            <span className="ev-data">{summarize(ev)}</span>
                          </li>
                        ))}
                      </ol>
                    </>
                  )}
                  {Array.isArray(e.responseBody?.steps) && e.responseBody.steps.length > 0 && (
                    <>
                      <h3>Agent steps</h3>
                      <ol className="req-steps">
                        {e.responseBody.steps.map((s, i) => (
                          <li key={i}>
                            <code>{s.tool}</code>
                            {s.query ? ` “${s.query}”` : ''}
                            {Object.keys(s.where || {}).length > 0 && (
                              <span className="req-where"> {Object.entries(s.where).map(([k, v]) => `${k}=${v}`).join(', ')}</span>
                            )}
                            <span className="req-hits"> → {s.count} {s.count === 1 ? 'hit' : 'hits'}</span>
                          </li>
                        ))}
                      </ol>
                    </>
                  )}
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
