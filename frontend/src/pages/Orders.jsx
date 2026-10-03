import { useEffect, useState, useRef } from 'react'
import { api, money, when, newKey } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import { usePayment } from '../components/PaymentModal.jsx'
import { BoxIcon, CheckIcon, ChevronDown, ClockIcon, UserIcon, XIcon } from '../components/icons.jsx'

// What each status means to a shopper.
const STATUS_LABEL = { CREATED: 'Awaiting payment', PAID: 'Paid', CANCELLED: 'Cancelled', FAILED: 'Not paid' }
// Stage 6d: what happens to a payment that arrived after its order closed. [badge, badge class, explanation]
const REFUND = {
  DUE: ['Refund due', 'badge-failed', 'It will be refunded.'],
  REQUESTED: ['Refund requested', 'badge-created', 'We are asking the payment gateway to refund it.'],
  PENDING: ['Refund in progress', 'badge-created', 'The gateway is returning the money.'],
  PROCESSED: ['Refunded', 'badge-paid', 'The money has been returned to you.'],
  FAILED: ['Refund failed', 'badge-failed', 'The gateway refused the refund; our team will contact you.'],
}

// Placed -> Paid -> Sent to warehouse (6e), or Placed -> Cancelled / Not paid.
// Only states the backend reports; nothing invented.
function Track({ status, shipmentId }) {
  const closed = status === 'CANCELLED' || status === 'FAILED'
  const steps = [
    ['Placed', 'done'],
    closed ? [STATUS_LABEL[status], 'bad'] : ['Paid', status === 'PAID' ? 'done' : 'now'],
  ]
  if (status === 'PAID') steps.push(['Sent to warehouse', shipmentId ? 'done' : 'now'])
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
  // Stage 7: a key per (action, order), kept until that action succeeds: clicking again after a
  // timeout repeats the SAME action and gets its answer, rather than starting a new one.
  const keys = useRef({})
  const keyFor = (action, id) => (keys.current[`${action}:${id}`] ??= newKey())
  const done = (action, id) => delete keys.current[`${action}:${id}`]

  const payNow = async (o) => {
    setBusy(o.id)
    try {
      payment.start(await api.orders.pay(o.id, keyFor('pay', o.id)))
      done('pay', o.id)
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
      await api.orders.cancel(o.id, keyFor('cancel', o.id))
      done('cancel', o.id)
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

  // Stage 6c-6e: an order can change without this tab doing anything (webhooks, the refund and
  // fulfilment consumers). While any order is waiting on one of them, look again every 5 s.
  const awaiting = (data || []).some((o) => o.status === 'CREATED' || (o.status === 'PAID' && !o.shipmentId) || (o.latePaymentId && o.refundStatus !== 'PROCESSED' && o.refundStatus !== 'FAILED'))
  useEffect(() => {
    if (!awaiting) return
    const t = setInterval(() => reload(), 5000)
    return () => clearInterval(t)
  }, [awaiting, reload])

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
              <Track status={o.status} shipmentId={o.shipmentId} />
              {o.shipmentId && (
                <div className="order-pay">
                  <span className="muted">Shipment {o.shipmentId} · handed to the warehouse {when(o.sentToWarehouseAt)}</span>
                </div>
              )}
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
              {o.latePaymentId && (
                <div className="order-pay">
                  <span className={`badge ${REFUND[o.refundStatus || 'DUE'][1]}`}>{REFUND[o.refundStatus || 'DUE'][0]}</span>
                  <span className="muted">
                    Your payment {o.latePaymentId} arrived after this order closed. {REFUND[o.refundStatus || 'DUE'][2]}
                  </span>
                </div>
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
