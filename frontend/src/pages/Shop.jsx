import { useState } from 'react'
import { api, money } from '../api.js'
import { useLoad, usePoll } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'
import Pager from '../components/Pager.jsx'

export function StockLabel({ stock }) {
  if (stock === null || stock === undefined) return <span className="stock stock-unknown">Stock unknown</span>
  if (stock <= 0) return <span className="stock stock-out">Out of stock</span>
  if (stock <= 5) return <span className="stock stock-low">Only {stock} left</span>
  return <span className="stock stock-ok">{stock} in stock</span>
}

// Live strip of armed flash sales. Units left come straight from Redis, refreshed every 3 s.
function FlashStrip({ sales, userId, onOpen, onAdd, adding }) {
  if (!sales?.length) return null
  return (
    <section className="flash-strip" aria-labelledby="flash-title">
      <div className="flash-strip-head">
        <h2 id="flash-title">Flash sale</h2>
        <p>Units are handed out by Redis first, so the rush never reaches the database. Live count, every 3 seconds.</p>
      </div>
      <ul className="flash-cards">
        {sales.map((s) => (
          <li key={s.productId} className={`flash-card ${s.remaining <= 0 ? 'is-sold-out' : ''}`}>
            <button className="tile-name" onClick={() => onOpen(s.productId)}>{s.name}</button>
            <span className="price">{money(s.price)}</span>
            <span className="flash-left">
              {s.remaining > 0 ? <><strong>{s.remaining}</strong> left</> : <strong>Sold out</strong>}
            </span>
            <button
              className="btn btn-block"
              disabled={!userId || s.remaining <= 0 || adding === s.productId}
              title={!userId ? 'Pick a shopper in the top bar first' : undefined}
              onClick={() => onAdd({ id: s.productId, name: s.name })}
            >
              {adding === s.productId ? 'Adding…' : 'Add to cart'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function Shop({ userId, onOpen, onCartChanged, notify, goManage }) {
  const [page, setPage] = useState(0)
  const { data, error, loading, reload } = useLoad(() => api.products.list(page, 12), [page])
  const [adding, setAdding] = useState(null)
  const sales = usePoll(() => api.flashSale.active(), 3000, [])
  const onSale = new Map((sales || []).map((s) => [s.productId, s]))

  const add = async (p) => {
    setAdding(p.id)
    try {
      await api.cart.add(p.id, 1)
      onCartChanged()
      notify(`Added ${p.name} to your cart`)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setAdding(null)
    }
  }

  const products = data?.content || []

  return (
    <section>
      <header className="page-head shop-head">
        <h1>The shelf</h1>
        <p>A practice shop for your backend. Open Requests in the top bar to watch each call land.</p>
      </header>

      <FlashStrip sales={sales} userId={userId} onOpen={onOpen} onAdd={add} adding={adding} />

      {error && (
        <div className="stack">
          <Problem error={error} />
          <button className="btn" onClick={reload}>Try again</button>
        </div>
      )}
      {loading && !data && <p className="muted">Loading products…</p>}

      {data && products.length === 0 && (
        <div className="empty">
          <h2>The shelf is empty</h2>
          <p>Create your first product in Manage, then come back here.</p>
          <button className="btn" onClick={goManage}>Go to Manage</button>
        </div>
      )}

      <ul className="grid">
        {products.map((p) => (
          <li key={p.id} className="tile">
            <button className="tile-open" onClick={() => onOpen(p.id)} aria-label={`Open ${p.name}`}>
              <Thumb src={p.thumbnailUrl} alt={p.name} className="tile-img" />
            </button>
            <div className="tile-body">
              {onSale.has(p.id) && (
                <span className="badge badge-flash">Flash sale · {onSale.get(p.id).remaining} left</span>
              )}
              <button className="tile-name" onClick={() => onOpen(p.id)}>{p.name}</button>
              <div className="tile-row">
                <span className="price">{money(p.price)}</span>
                <StockLabel stock={p.stock} />
              </div>
              <button
                className="btn btn-block"
                disabled={!userId || p.stock <= 0 || adding === p.id}
                title={!userId ? 'Pick a user in the top bar first' : undefined}
                onClick={() => add(p)}
              >
                {adding === p.id ? 'Adding…' : 'Add to cart'}
              </button>
            </div>
          </li>
        ))}
      </ul>

      <Pager page={data?.page ?? page} totalPages={data?.totalPages} onChange={setPage} />
    </section>
  )
}
