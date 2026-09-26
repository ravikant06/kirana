import { useState } from 'react'
import { api, money } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'

export default function Cart({ userId, onCartChanged, onOrdered, notify, goShop }) {
  const { data: cart, error, loading, reload } = useLoad(() => (userId ? api.cart.get() : Promise.resolve(null)), [userId])
  const [busy, setBusy] = useState(false)
  const [orderError, setOrderError] = useState(null)

  const run = async (fn) => {
    setBusy(true)
    try {
      await fn()
      await reload()
      onCartChanged()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  const placeOrder = async () => {
    setBusy(true)
    setOrderError(null)
    try {
      const order = await api.orders.place()
      onCartChanged()
      notify(`Order #${order.id} placed`)
      onOrdered(order.id)
    } catch (e) {
      setOrderError(e)
      await reload()
    } finally {
      setBusy(false)
    }
  }

  if (!userId) {
    return (
      <div className="empty">
        <h2>No shopper selected</h2>
        <p>Carts belong to a user. Pick one in the top bar, or create one in Manage.</p>
      </div>
    )
  }

  const items = cart?.items || []

  return (
    <section>
      <header className="page-head">
        <h1>Your cart</h1>
      </header>
      {error && <Problem error={error} />}
      {loading && !cart && <p className="muted">Loading cart…</p>}
      {cart && items.length === 0 && (
        <div className="empty">
          <h2>Your cart is empty</h2>
          <p>Add something from the shelf.</p>
          <button className="btn" onClick={goShop}>Browse products</button>
        </div>
      )}
      {items.length > 0 && (
        <div className="cart">
          <ul className="lines">
            {items.map((it) => (
              <li key={it.productId} className="line">
                <Thumb src={it.thumbnailUrl} alt={it.productName} className="line-img" />
                <div className="line-main">
                  <strong>{it.productName}</strong>
                  <span className="muted">{money(it.unitPrice)} each</span>
                </div>
                <div className="stepper" aria-label={`Quantity of ${it.productName}`}>
                  <button disabled={busy || it.quantity <= 1} onClick={() => run(() => api.cart.update(it.productId, it.quantity - 1))} aria-label="Decrease">−</button>
                  <span>{it.quantity}</span>
                  <button disabled={busy} onClick={() => run(() => api.cart.update(it.productId, it.quantity + 1))} aria-label="Increase">+</button>
                </div>
                <span className="line-total">{money(it.lineTotal)}</span>
                <button className="btn-quiet" disabled={busy} onClick={() => run(() => api.cart.remove(it.productId))}>Remove</button>
              </li>
            ))}
          </ul>
          <div className="checkout">
            <div className="checkout-total">
              <span>Total</span>
              <strong>{money(cart.total)}</strong>
            </div>
            {orderError && <Problem error={orderError} compact />}
            <button className="btn btn-big" disabled={busy} onClick={placeOrder}>
              {busy ? 'Working…' : 'Place order'}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
