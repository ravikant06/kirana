import { useEffect, useState } from 'react'
import { api, money } from '../../api.js'

// Product cards in a chat answer (AI Phase 4).
//
// The assistant sends product *ids* only. Everything on a card (photo, name, price, stock) comes
// from Kirana's own API, fetched here, so a price on screen can never be something the model
// said or something a search index remembered. Reopening an old chat refetches: today's prices.
//
// "Add" uses Kirana's cart API directly, with an Idempotency-Key (Stage 7): this is the shopper
// clicking a button, not the assistant acting on their behalf (that is Phase 6, with approvals).
export default function ProductCards({ ids, onOpen, onCartChanged, notify }) {
  const [products, setProducts] = useState(null)
  const [error, setError] = useState(null)
  const key = ids.join(',')

  useEffect(() => {
    let alive = true
    api.products.batch(ids)
      .then((rows) => alive && setProducts(rows))
      .catch((e) => alive && setError(e))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])

  if (error) return <p className="pc-note">Couldn’t load these products right now.</p>
  if (!products) {
    return (
      <div className="pc-row" aria-busy="true">
        {ids.slice(0, 3).map((id) => <div key={id} className="pc-card pc-skeleton" />)}
      </div>
    )
  }
  if (products.length === 0) return <p className="pc-note">These products are no longer available.</p>
  return (
    <div className="pc-row">
      {products.map((p) => (
        <Card key={p.id} p={p} onOpen={onOpen} onCartChanged={onCartChanged} notify={notify} />
      ))}
    </div>
  )
}

function Card({ p, onOpen, onCartChanged, notify }) {
  const [state, setState] = useState('idle') // idle | adding | added
  const out = p.stock <= 0

  const add = async () => {
    setState('adding')
    try {
      await api.cart.add(p.id, 1)
      setState('added')
      onCartChanged?.()
      notify?.(`Added ${p.name} to your cart`)
    } catch (e) {
      setState('idle')
      notify?.(e.message, 'error')
    }
  }

  return (
    <div className="pc-card">
      <button className="pc-photo" onClick={() => onOpen?.(p.id)} aria-label={`Open ${p.name}`}>
        {p.thumbnailUrl ? <img src={p.thumbnailUrl} alt="" loading="lazy" /> : <span>{p.name[0]}</span>}
      </button>
      <div className="pc-body">
        {p.category && <span className="pc-cat">{p.category}</span>}
        <button className="pc-name" onClick={() => onOpen?.(p.id)} title={p.name}>{p.name}</button>
        <div className="pc-row-price">
          <span className="pc-price">{money(p.price)}</span>
          {out ? <span className="pc-stock is-out">Out of stock</span>
            : p.stock <= 5 ? <span className="pc-stock is-low">Only {p.stock} left</span> : null}
        </div>
        <button className={`pc-add ${state === 'added' ? 'is-added' : ''}`} disabled={out || state === 'adding'} onClick={add}>
          {state === 'adding' ? 'Adding…' : state === 'added' ? 'Added ✓' : 'Add to cart'}
        </button>
      </div>
    </div>
  )
}
