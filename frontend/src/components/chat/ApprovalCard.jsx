import { useEffect, useState } from 'react'
import { api } from '../../api.js'

// An action the assistant proposed, waiting for the shopper (AI Phase 6 M4/M5).
//
// Everything on this card comes from the AI service's stored copy of the action (summary, lines,
// expiry), never from the model's text, and the click sends only "confirm" or "reject": the server
// runs exactly what it stored. A second click, or a retry after a timeout, gets the first outcome.
const LABEL = {
  pending: 'Waiting for you', executing: 'Working…', done: 'Done', failed: 'Failed',
  rejected: 'Not done', expired: 'Expired',
}

export default function ApprovalCard({ approval, onDecided, notify }) {
  const [view, setView] = useState({ ...approval, id: approval.approval_id, status: 'pending' })
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(Date.now())

  // Reopened thread or another tab: ask the server for the real state.
  useEffect(() => {
    let alive = true
    api.ai.approval(approval.approval_id).then((v) => alive && setView(v)).catch(() => {})
    return () => { alive = false }
  }, [approval.approval_id])

  // Another click (or tab) is running it right now: check back until it finishes.
  useEffect(() => {
    if (view.status !== 'executing') return
    const t = setTimeout(() => {
      api.ai.approval(approval.approval_id).then((v) => {
        setView(v)
        if (v.status !== 'executing') onDecided?.(v)
      }).catch(() => {})
    }, 1000)
    return () => clearTimeout(t)
  }, [view, approval.approval_id, onDecided])

  useEffect(() => {
    if (view.status !== 'pending') return
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [view.status])

  const left = Math.max(0, Math.round((new Date(view.expires_at).getTime() - now) / 1000))
  const open = view.status === 'pending' && left > 0

  const decide = async (decision) => {
    setBusy(true)
    try {
      const v = await api.ai.decide(approval.approval_id, decision)
      setView(v)
      onDecided?.(v)
    } catch (e) {
      notify?.(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`approval approval-${view.status}`} role="group" aria-label="Action waiting for your confirmation">
      <div className="approval-head">
        <strong>{view.summary}</strong>
        <span className="approval-status">{open || view.status !== 'pending' ? LABEL[view.status] : 'Expired'}</span>
      </div>
      {view.lines?.length > 0 && (
        <ul className="approval-lines">{view.lines.map((l) => <li key={l}>{l}</li>)}</ul>
      )}
      {open ? (
        <div className="approval-actions">
          <button className="btn btn-add" disabled={busy} onClick={() => decide('confirm')}>Confirm</button>
          <button className="btn-quiet" disabled={busy} onClick={() => decide('reject')}>Reject</button>
          <span className="approval-expiry">expires in {Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}</span>
        </div>
      ) : (
        view.message && <p className="approval-message">{view.message}</p>
      )}
    </div>
  )
}
