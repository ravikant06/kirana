import { useState } from 'react'
import { api, money, when } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'
import { StockLabel } from './Shop.jsx'

export default function ProductDetail({ id, userId, onBack, onCartChanged, notify }) {
  const { data: p, error, loading } = useLoad(() => api.products.get(id), [id])
  const [active, setActive] = useState(0)
  const [qty, setQty] = useState(1)
  const [busy, setBusy] = useState(false)

  const add = async () => {
    setBusy(true)
    try {
      await api.cart.add(p.id, qty)
      onCartChanged()
      notify(`Added ${qty} × ${p.name} to your cart`)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section>
      <button className="btn-quiet back" onClick={onBack}>Back to the shelf</button>
      {error && <Problem error={error} />}
      {loading && !p && <p className="muted">Loading product…</p>}
      {p && (
        <div className="detail">
          <div className="gallery">
            <Thumb src={p.images?.[active]?.url} alt={p.name} className="gallery-main" />
            {p.images?.length > 1 && (
              <div className="gallery-strip">
                {p.images.map((img, i) => (
                  <button
                    key={img.id}
                    className={`gallery-pick ${i === active ? 'is-active' : ''}`}
                    onClick={() => setActive(i)}
                    aria-label={`Show image ${i + 1}`}
                  >
                    <Thumb src={img.url} alt="" />
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="detail-info">
            <h1>{p.name}</h1>
            <div className="detail-price">{money(p.price)}</div>
            <StockLabel stock={p.stock} />
            {p.description && <p className="detail-desc">{p.description}</p>}
            <div className="buy-row">
              <label className="qty">
                <span>Quantity</span>
                <input
                  type="number"
                  min="1"
                  value={qty}
                  onChange={(e) => setQty(Math.max(1, parseInt(e.target.value || '1', 10)))}
                />
              </label>
              <button className="btn" disabled={!userId || busy || p.stock <= 0} onClick={add}>
                {busy ? 'Adding…' : 'Add to cart'}
              </button>
            </div>
            {!userId && <p className="muted">Pick a user in the top bar to shop.</p>}
            <dl className="facts">
              <dt>Product ID</dt><dd>{p.id}</dd>
              <dt>Created</dt><dd>{when(p.createdAt)}</dd>
              <dt>Last updated</dt><dd>{when(p.updatedAt)}</dd>
            </dl>
          </div>
        </div>
      )}
    </section>
  )
}
