import { useEffect, useState } from 'react'
import { api, money, when } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'

export default function Orders({ userId, highlight }) {
  const { data, error, loading } = useLoad(() => (userId ? api.orders.list() : Promise.resolve(null)), [userId])
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
              <span className={`badge badge-${String(o.status || '').toLowerCase()}`}>{o.status}</span>
              <span className="order-count">{o.items?.length ?? 0} items</span>
              <strong className="order-total">{money(o.total)}</strong>
            </button>
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
