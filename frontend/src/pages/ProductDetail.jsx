import { useState } from 'react'
import { api, money, when } from '../api.js'
import { useLoad, usePoll } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'
import { StockLabel } from './Shop.jsx'
import { BoltIcon, CartIcon, ChevronRight, ClockIcon, MinusIcon, PlusIcon, ShieldIcon } from '../components/icons.jsx'

export default function ProductDetail({ id, userId, inCart, onBack, goCart, onCartChanged, notify }) {
  const { data: p, error, loading } = useLoad(() => api.products.get(id), [id])
  const flash = usePoll(() => api.flashSale.status(id, true), 2000, [id])
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

  const out = p && p.stock !== null && p.stock !== undefined && p.stock <= 0

  return (
    <section className="pdp">
      <nav className="crumbs" aria-label="Breadcrumb">
        <button className="link-btn" onClick={onBack}>Home</button>
        <ChevronRight size={14} />
        <button className="link-btn" onClick={onBack}>All products</button>
        {p && (<><ChevronRight size={14} /><span aria-current="page">{p.name}</span></>)}
      </nav>
      {error && <Problem error={error} />}
      {loading && !p && (
        <div className="detail" aria-hidden="true">
          <div className="sk sk-gallery" />
          <div className="detail-info"><div className="sk sk-line" /><div className="sk sk-line short" /><div className="sk sk-btn" /></div>
        </div>
      )}
      {p && (
        <div className="detail">
          <div className={`gallery ${p.images?.length > 1 ? 'has-strip' : ''}`}>
            {p.images?.length > 1 && (
              <div className="gallery-strip">
                {p.images.map((img, i) => (
                  <button
                    key={img.id}
                    className={`gallery-pick ${i === active ? 'is-active' : ''}`}
                    onClick={() => setActive(i)}
                    onMouseEnter={() => setActive(i)}
                    aria-label={`Show image ${i + 1}`}
                  >
                    <Thumb src={img.url} alt="" />
                  </button>
                ))}
              </div>
            )}
            <div className="gallery-stage">
              <Thumb src={p.images?.[active]?.url} alt={p.name} className="gallery-main" />
              {flash?.active && <span className="tile-flag gallery-flag"><BoltIcon size={12} /> Flash deal</span>}
            </div>
          </div>

          <div className="detail-info">
            <h1>{p.name}</h1>
            <div className="detail-price-row">
              <span className="detail-price">{money(p.price)}</span>
            </div>
            <StockLabel stock={p.stock} />

            {flash?.active && (
              <div className={`flash-banner ${flash.remaining <= 0 ? 'is-sold-out' : ''}`} role="status">
                <span className="flash-banner-ico"><BoltIcon size={18} /></span>
                <div>
                  <strong>{flash.remaining > 0 ? `Flash deal: ${flash.remaining} left right now` : 'Sold out in this flash sale'}</strong>
                  <span>Checkout asks Redis for a unit first; if none are left you are turned away before the database.</span>
                </div>
              </div>
            )}

            <div className="buy-box">
              <div className="qty">
                <span id="qty-label">Quantity</span>
                <div className="qty-ctl is-outline" role="group" aria-labelledby="qty-label">
                  <button onClick={() => setQty((q) => Math.max(1, q - 1))} disabled={qty <= 1} aria-label="Decrease"><MinusIcon size={16} /></button>
                  <input
                    type="number"
                    min="1"
                    value={qty}
                    aria-label="Quantity"
                    onChange={(e) => setQty(Math.max(1, parseInt(e.target.value || '1', 10)))}
                  />
                  <button onClick={() => setQty((q) => q + 1)} aria-label="Increase"><PlusIcon size={16} /></button>
                </div>
              </div>
              <div className="buy-actions">
                <button className="btn btn-add btn-lg" disabled={!userId || busy || out} onClick={add}>
                  <CartIcon size={18} /> {busy ? 'Adding…' : out ? 'Out of stock' : 'Add to cart'}
                </button>
                {inCart > 0 && (
                  <button className="btn btn-outline btn-lg" onClick={goCart}>
                    Go to cart · {inCart}
                  </button>
                )}
              </div>
              {!userId && <p className="muted">Pick a shopper in the top bar to shop.</p>}
            </div>

            <ul className="perks">
              <li><ShieldIcon size={18} /><span><b>Secure payment</b> through the gateway, verified by signature</span></li>
              <li><ClockIcon size={18} /><span><b>Held for 10 minutes</b> while you pay at checkout</span></li>
            </ul>

            {p.description && (
              <div className="detail-block">
                <h2>About this product</h2>
                <p className="detail-desc">{p.description}</p>
              </div>
            )}

            <div className="detail-block">
              <h2>Product details</h2>
              <dl className="facts">
                <dt>Product ID</dt><dd>{p.id}</dd>
                <dt>Created</dt><dd>{when(p.createdAt)}</dd>
                <dt>Last updated</dt><dd>{when(p.updatedAt)}</dd>
              </dl>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
