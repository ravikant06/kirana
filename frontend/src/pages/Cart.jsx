import { useRef, useState } from 'react'
import { api, money, newKey } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'
import { usePayment } from '../components/PaymentModal.jsx'
import { CartIcon, ClockIcon, MinusIcon, PlusIcon, ShieldIcon, TrashIcon, UserIcon } from '../components/icons.jsx'

export default function Cart({ userId, onCartChanged, onOrdered, notify, goShop }) {
  const { data: cart, error, loading, reload } = useLoad(() => (userId ? api.cart.get() : Promise.resolve(null)), [userId])
  const [busy, setBusy] = useState(false)
  const [orderError, setOrderError] = useState(null)
  const providers = useLoad(() => api.payments.providers(), [])
  const [provider, setProvider] = useState(null)
  const available = (providers.data || []).filter((p) => p.available)
  const chosen = provider ?? available.find((p) => p.isDefault)?.id ?? available[0]?.id
  const payment = usePayment({ notify, onDone: (orderId) => onOrdered(orderId) })
  // Stage 7: one key per checkout attempt. Kept until the order is placed, so clicking "Place
  // order" again after an error or timeout gets the first attempt's order instead of a new one.
  const checkoutKey = useRef(null)

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
      checkoutKey.current ??= newKey()
      const { order, payment: session, paymentProblem } = await api.orders.place(chosen, checkoutKey.current)
      checkoutKey.current = null // done: the next checkout is a new action
      onCartChanged()
      if (session) {
        payment.start(session) // stock is held; the gateway's checkout opens
      } else {
        notify(paymentProblem || `Order #${order.id} placed; pay for it from Orders.`, 'error')
        onOrdered(order.id)
      }
    } catch (e) {
      if (e.status === 422) checkoutKey.current = null // the key belonged to a different request
      setOrderError(e)
      await reload()
      onCartChanged() // the cart may have changed elsewhere (another tab placed the order)
    } finally {
      setBusy(false)
    }
  }

  if (!userId) {
    return (
      <div className="empty empty-center">
        <div className="empty-art" aria-hidden="true"><UserIcon size={36} /></div>
        <h2>No shopper selected</h2>
        <p>Carts belong to a user. Pick one in the top bar, or create one in Manage.</p>
      </div>
    )
  }

  const items = cart?.items || []
  const count = items.reduce((n, it) => n + (it.quantity || 0), 0)

  return (
    <section>
      {payment.modal}
      <header className="page-head">
        <h1>Your cart</h1>
        {items.length > 0 && <p>{count} {count === 1 ? 'item' : 'items'} ready for checkout</p>}
      </header>
      {error && <Problem error={error} />}
      {loading && !cart && (
        <div className="cart" aria-hidden="true">
          <div className="card"><div className="sk sk-row" /><div className="sk sk-row" /></div>
          <div className="card"><div className="sk sk-line" /><div className="sk sk-btn" /></div>
        </div>
      )}
      {cart && items.length === 0 && (
        <div className="empty empty-center">
          <div className="empty-art" aria-hidden="true"><CartIcon size={36} /></div>
          <h2>Your cart is empty</h2>
          <p>Looks like you haven't added anything yet. The shelf is full of good things.</p>
          <button className="btn btn-lg" onClick={goShop}>Start shopping</button>
        </div>
      )}
      {items.length > 0 && (
        <div className="cart">
          <div className="card cart-items">
            <div className="card-head">
              <h2>Items</h2>
              <button className="link-btn" onClick={goShop}>Continue shopping</button>
            </div>
            <ul className="lines">
              {items.map((it) => (
                <li key={it.productId} className="line">
                  <Thumb src={it.thumbnailUrl} alt={it.productName} className="line-img" />
                  <div className="line-main">
                    <strong>{it.productName}</strong>
                    <span className="muted">{money(it.unitPrice)} each</span>
                    <div className="line-controls">
                      <div className="qty-ctl is-outline" aria-label={`Quantity of ${it.productName}`}>
                        <button disabled={busy || it.quantity <= 1} onClick={() => run(() => api.cart.update(it.productId, it.quantity - 1))} aria-label="Decrease"><MinusIcon size={16} /></button>
                        <span>{it.quantity}</span>
                        <button disabled={busy} onClick={() => run(() => api.cart.update(it.productId, it.quantity + 1))} aria-label="Increase"><PlusIcon size={16} /></button>
                      </div>
                      <button className="link-btn link-danger" disabled={busy} onClick={() => run(() => api.cart.remove(it.productId))}>
                        <TrashIcon size={15} /> Remove
                      </button>
                    </div>
                  </div>
                  <span className="line-total">{money(it.lineTotal)}</span>
                </li>
              ))}
            </ul>
          </div>

          <aside className="card checkout">
            <h2 className="checkout-title">Price details</h2>
            <dl className="summary">
              <dt>Price ({count} {count === 1 ? 'item' : 'items'})</dt>
              <dd>{money(cart.total)}</dd>
            </dl>
            <div className="checkout-total">
              <span>Total amount</span>
              <strong>{money(cart.total)}</strong>
            </div>
            {available.length > 0 && (
              <fieldset className="pay-choice">
                <legend>Pay with</legend>
                {(providers.data || []).map((p) => (
                  <label key={p.id} className={`pay-opt ${chosen === p.id ? 'is-active' : ''} ${p.available ? '' : 'is-disabled'}`}>
                    <input
                      type="radio"
                      name="payment-provider"
                      value={p.id}
                      checked={chosen === p.id}
                      disabled={!p.available}
                      onChange={() => setProvider(p.id)}
                    />
                    <span>{p.label}{!p.available && ' (not configured)'}</span>
                  </label>
                ))}
              </fieldset>
            )}
            {orderError && <Problem error={orderError} compact />}
            <button className="btn btn-add btn-lg btn-block" disabled={busy} onClick={placeOrder}>
              {busy ? 'Working…' : `Place order · ${money(cart.total)}`}
            </button>
            <ul className="checkout-notes">
              <li><ClockIcon size={16} /> Your items are held for 10 minutes while you pay.</li>
              <li><ShieldIcon size={16} /> Payments are verified by the backend, not the browser.</li>
            </ul>
          </aside>
        </div>
      )}
    </section>
  )
}
