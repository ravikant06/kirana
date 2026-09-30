import { useEffect, useState } from 'react'
import { api, money, when } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import { usePayment } from '../components/PaymentModal.jsx'

// What each status means to a shopper.
const STATUS_LABEL = { CREATED: 'Awaiting payment', PAID: 'Paid', CANCELLED: 'Cancelled', FAILED: 'Not paid' }

export default function Orders({ userId, highlight, notify }) {
  const { data, error, loading, reload } = useLoad(() => (userId ? api.orders.list() : Promise.resolve(null)), [userId])
  const [busy, setBusy] = useState(null)
  const payment = usePayment({ notify, onDone: () => reload() })

  const payNow = async (o) => {
    setBusy(o.id)
    try {
      payment.start(await api.orders.pay(o.id))
    } catch (e) {
      notify(e.message, 'error')
      reload()
    } finally {
      setBusy(null)
    }
  }

  const cancel = async (o) => {
    setBusy(o.id)
    try {
      await api.orders.cancel(o.id)
      notify(`Order #${o.id} cancelled; its items are back on the shelf`)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(null)
      reload()
    }
  }
  const [open, setOpen] = useState(highlight ?? null)
  useEffect(() => setOpen(highlight ?? null), [highlight])

  if (!userId) {
    return (
      <div className="empty">
        <h2>No shopper selected</h2>
        <p>Orders belong to a user. Pick one in the top bar.</p>
      </div>
    )
  }

  const orders = data || []
  return (
    <section>
      {payment.modal}
      <header className="page-head">
        <h1>Your orders</h1>
      </header>
      {error && <Problem error={error} />}
      {loading && !data && <p className="muted">Loading orders…</p>}
      {data && orders.length === 0 && (
        <div className="empty">
          <h2>No orders yet</h2>
          <p>Place an order from your cart and it will show up here.</p>
        </div>
      )}
      <ul className="orders">
        {orders.map((o) => (
          <li key={o.id} className={`order ${o.id === highlight ? 'is-new' : ''}`}>
            <button className="order-row" onClick={() => setOpen(open === o.id ? null : o.id)} aria-expanded={open === o.id}>
              <span className="order-id">#{o.id}</span>
              <span className="order-when">{when(o.createdAt)}</span>
              <span className={`badge badge-${String(o.status || '').toLowerCase()}`}>{STATUS_LABEL[o.status] || o.status}</span>
              <span className="order-count">{o.items?.length ?? 0} items</span>
              <strong className="order-total">{money(o.total)}</strong>
            </button>
            {o.status === 'CREATED' && (
              <div className="order-pay">
                <span className="muted">
                  {o.paymentDueAt ? `Items held until ${when(o.paymentDueAt)}.` : 'Awaiting payment.'}
                </span>
                <button className="btn" disabled={busy === o.id} onClick={() => payNow(o)}>Pay now</button>
                <button className="btn-quiet" disabled={busy === o.id} onClick={() => cancel(o)}>Cancel order</button>
              </div>
            )}
            {(o.status === 'CANCELLED' || o.status === 'FAILED') && o.closedReason && (
              <div className="order-pay"><span className="muted">{o.closedReason}</span></div>
            )}
            {open === o.id && (
              <table className="order-items">
                <thead>
                  <tr><th>Product</th><th>Unit price paid</th><th>Qty</th><th>Line total</th></tr>
                </thead>
                <tbody>
                  {(o.items || []).map((it, i) => (
                    <tr key={i}>
                      <td>{it.productName}</td>
                      <td>{money(it.unitPrice)}</td>
                      <td>{it.quantity}</td>
                      <td>{money(it.lineTotal)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
