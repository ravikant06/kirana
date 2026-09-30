import { useState } from 'react'
import { api } from '../api.js'
import { usePoll } from '../hooks.js'

// Stage 5: see each circuit breaker and the checkout bulkhead live, and break things on purpose.
const BREAKER_LABEL = {
  redis: 'Redis',
  minio: 'MinIO (images)',
  'payment-mock': 'Payment · test gateway',
  'payment-razorpay': 'Payment · Razorpay',
}
const STATE = {
  CLOSED: ['Closed', 'is-ok', 'Calls go through normally.'],
  OPEN: ['Open', 'is-bad', 'Failing fast: calls are skipped without waiting.'],
  HALF_OPEN: ['Half-open', 'is-warn', 'Letting a few trial calls through.'],
}
const PAYMENT_MODES = [
  ['normal', 'Normal', {}],
  ['slow', 'Slow (8 s)', { delayMs: 8000 }],
  ['flaky', 'Flaky (30%)', { failureRate: 0.3 }],
  ['down', 'Down', {}],
  ['hang', 'Hang', {}],
]
const NETWORK_FAULTS = [
  ['normal', 'Normal'],
  ['latency', '+1 s latency'],
  ['hang', 'Hang'],
  ['down', 'Down'],
]

const pct = (v) => (v < 0 ? '–' : `${Math.round(v)}%`)

export default function ResilienceLab({ notify }) {
  const [tick, setTick] = useState(0)
  const status = usePoll(() => api.system.status(), 2000, [tick])
  const refresh = () => setTick((t) => t + 1)

  const act = async (fn, msg) => {
    try {
      await fn()
      notify(msg)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      refresh()
    }
  }

  if (!status) return null
  const mode = status.paymentMockMode?.mode

  return (
    <div className="panel lab">
      <div className="lab-head">
        <h2>Resilience lab</h2>
        <p className="muted">
          Break a dependency, then use the shop and watch what happens here. Updates every 2 seconds.
          {!status.resilienceEnabled && ' Resilience is switched off (kirana.resilience.enabled=false).'}
        </p>
      </div>

      <div className="lab-grid">
        {status.breakers.map((b) => {
          const [label, cls, hint] = STATE[b.state] || [b.state, '', '']
          return (
            <div key={b.name} className={`lab-card ${cls}`}>
              <div className="lab-card-head">
                <strong>{BREAKER_LABEL[b.name] || b.name}</strong>
                <span className={`lab-pill ${cls}`}>{label}</span>
              </div>
              <p className="muted">{hint}</p>
              <p className="lab-metrics">
                failed {pct(b.failureRate)} · slow {pct(b.slowCallRate)} · {b.recentCalls} recent · {b.rejectedCalls} skipped
              </p>
              {b.state !== 'CLOSED' && (
                <button className="btn-quiet" onClick={() => act(() => api.system.resetBreaker(b.name), `${BREAKER_LABEL[b.name] || b.name} breaker closed`)}>
                  Close it now
                </button>
              )}
            </div>
          )
        })}
        <div className="lab-card">
          <div className="lab-card-head">
            <strong>Checkout slots</strong>
            <span className={`lab-pill ${status.checkout.available === 0 ? 'is-bad' : 'is-ok'}`}>
              {status.checkout.available} / {status.checkout.max} free
            </span>
          </div>
          <p className="muted">At most {status.checkout.max} checkouts run at once. The next one gets "Checkout busy" straight away, so browsing keeps working.</p>
        </div>
      </div>

      <div className="lab-controls">
        <h3>Payment gateway (test gateway API)</h3>
        {status.paymentMockMode ? (
          <div className="lab-buttons">
            {PAYMENT_MODES.map(([m, label, extra]) => (
              <button
                key={m}
                className={mode === m ? 'btn' : 'btn-quiet'}
                onClick={() => act(() => api.system.paymentFault({ mode: m, ...extra }), `Test gateway: ${label}`)}
              >
                {label}
              </button>
            ))}
          </div>
        ) : (
          <p className="muted">payment-mock is not reachable. Start it with <code>docker compose up -d payment-mock</code>.</p>
        )}

        <h3>Network between the backend and…</h3>
        {status.chaosEnabled ? (
          ['redis', 'minio', 'payment'].map((proxy) => (
            <div key={proxy} className="lab-row">
              <span className="lab-row-name">{proxy === 'payment' ? 'payment gateway' : proxy}</span>
              <span className="muted lab-row-state">{status.networkFaults[proxy]}</span>
              <div className="lab-buttons">
                {NETWORK_FAULTS.map(([f, label]) => (
                  <button
                    key={f}
                    className={(status.networkFaults[proxy] || '').startsWith(f === 'latency' ? 'latency' : f) ? 'btn' : 'btn-quiet'}
                    onClick={() => act(() => api.system.networkFault(proxy, { fault: f, latencyMs: 1000 }), `${proxy}: ${label}`)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          ))
        ) : (
          <p className="muted">
            Network faults need the backend started with the chaos profile, which routes its traffic through Toxiproxy:
            <code> mvn spring-boot:run -Dspring-boot.run.profiles=chaos</code>
          </p>
        )}
      </div>
    </div>
  )
}
