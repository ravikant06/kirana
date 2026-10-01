import { useEffect, useState } from 'react'
import { api, money, when } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import { usePayment } from '../components/PaymentModal.jsx'
import { BoxIcon, CheckIcon, ChevronDown, ClockIcon, UserIcon, XIcon } from '../components/icons.jsx'

// What each status means to a shopper.
const STATUS_LABEL = { CREATED: 'Awaiting payment', PAID: 'Paid', CANCELLED: 'Cancelled', FAILED: 'Not paid' }

// Placed -> Paid, or Placed -> Cancelled / Not paid. Only states the backend reports; nothing invented.
function Track({ status }) {
  const closed = status === 'CANCELLED' || status === 'FAILED'
  const steps = [
    ['Placed', 'done'],
    closed ? [STATUS_LABEL[status], 'bad'] : ['Paid', status === 'PAID' ? 'done' : 'now'],
  ]
  return (
    <ol className="track" aria-label="Order progress">
      {steps.map(([label, state], i) => (
        <li key={label} className={`track-step is-${state}`}>
          <span className="track-dot">
            {state === 'done' && <CheckIcon size={12} />}
            {state === 'bad' && <XIcon size={12} />}
            {state === 'now' && <ClockIcon size={12} />}
          </span>
          <span>{label}</span>
          {i < steps.length - 1 && <span className={`track-bar ${status === 'PAID' ? 'is-done' : ''}`} />}
        </li>
      ))}
    </ol>
  )
}

export default function Orders({ userId, highlight, notify, goShop }) {
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
      <div className="empty empty-center">
        <div className="empty-art" aria-hidden="true"><UserIcon size={36} /></div>
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
        {orders.length > 0 && <p>{orders.length} {orders.length === 1 ? 'order' : 'orders'}, newest first</p>}
      </header>
      {error && <Problem error={error} />}
      {loading && !data && (
        <div className="orders" aria-hidden="true">
          <div className="card"><div className="sk sk-row" /></div>
          <div className="card"><div className="sk sk-row" /></div>
        </div>
      )}
      {data && orders.length === 0 && (
        <div className="empty empty-center">
          <div className="empty-art" aria-hidden="true"><BoxIcon size={36} /></div>
          <h2>No orders yet</h2>
          <p>Place an order from your cart and it will show up here.</p>
          {goShop && <button className="btn btn-lg" onClick={goShop}>Start shopping</button>}
        </div>
      )}
      <ul className="orders">
        {orders.map((o) => (
          <li key={o.id} className={`order ${o.id === highlight ? 'is-new' : ''}`}>
            <button className="order-row" onClick={() => setOpen(open === o.id ? null : o.id)} aria-expanded={open === o.id}>
              <span className="order-ico" aria-hidden="true"><BoxIcon size={22} /></span>
              <span className="order-head">
                <span className="order-id">Order #{o.id}</span>
                <span className="order-when">{when(o.createdAt)} · {o.items?.length ?? 0} {(o.items?.length ?? 0) === 1 ? 'item' : 'items'}</span>
              </span>
              <span className={`badge badge-${String(o.status || '').toLowerCase()}`}>{STATUS_LABEL[o.status] || o.status}</span>
              <strong className="order-total">{money(o.total)}</strong>
              <ChevronDown size={18} className={`order-caret ${open === o.id ? 'is-open' : ''}`} />
            </button>
            <div className="order-sub">
              <Track status={o.status} />
              {o.status === 'CREATED' && (
                <div className="order-pay">
                  <span className="muted">
                    {o.paymentDueAt ? `Items held until ${when(o.paymentDueAt)}.` : 'Awaiting payment.'}
                  </span>
                  <button className="btn btn-add" disabled={busy === o.id} onClick={() => payNow(o)}>Pay now</button>
                  <button className="btn-quiet" disabled={busy === o.id} onClick={() => cancel(o)}>Cancel order</button>
                </div>
              )}
              {(o.status === 'CANCELLED' || o.status === 'FAILED') && o.closedReason && (
                <div className="order-pay"><span className="muted">{o.closedReason}</span></div>
              )}
            </div>
            {open === o.id && (
              <div className="order-items-wrap">
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
                  <tfoot>
                    <tr><td colSpan="3">Order total</td><td>{money(o.total)}</td></tr>
                  </tfoot>
                </table>
              </div>
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}
