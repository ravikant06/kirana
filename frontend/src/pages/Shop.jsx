import { useRef, useState } from 'react'
import { api, money } from '../api.js'
import { useLoad, usePoll } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'
import Pager from '../components/Pager.jsx'
import { BoltIcon, ClockIcon, MinusIcon, PlusIcon, SearchIcon, ShieldIcon, XIcon } from '../components/icons.jsx'

export function StockLabel({ stock }) {
  if (stock === null || stock === undefined) return <span className="stock stock-unknown">Stock unknown</span>
  if (stock <= 0) return <span className="stock stock-out">Out of stock</span>
  if (stock <= 5) return <span className="stock stock-low">Only {stock} left</span>
  return <span className="stock stock-ok">{stock} in stock</span>
}

// ADD button that turns into a − qty + stepper once the product is in the cart.
// Uses the same cart endpoints as the Cart page: POST /cart/items, PUT and DELETE /cart/items/{id}.
export function CartControl({ product, qty, userId, busy, onAdd, onSet, compact }) {
  const out = product.stock !== null && product.stock !== undefined && product.stock <= 0
  if (qty > 0) {
    return (
      <div className={`qty-ctl ${compact ? 'is-compact' : ''}`} aria-label={`Quantity of ${product.name} in cart`}>
        <button disabled={busy} onClick={() => onSet(qty - 1)} aria-label="Remove one">
          <MinusIcon size={16} />
        </button>
        <span aria-live="polite">{qty}</span>
        <button
          disabled={busy || (product.stock != null && qty >= product.stock)}
          onClick={() => onSet(qty + 1)}
          aria-label="Add one more"
        >
          <PlusIcon size={16} />
        </button>
      </div>
    )
  }
  return (
    <button
      className={`btn btn-add ${compact ? 'is-compact' : ''}`}
      disabled={!userId || out || busy}
      title={!userId ? 'Pick a shopper in the top bar first' : undefined}
      onClick={onAdd}
    >
      {busy ? 'Adding…' : out ? 'Sold out' : 'Add to cart'}
    </button>
  )
}

function Hero({ salesCount }) {
  return (
    <section className="hero" aria-label="Welcome">
      <div className="hero-copy">
        <span className="hero-kicker">Your neighbourhood store, online</span>
        <h1>Everyday essentials,<br />without the queue.</h1>
        <p>Browse the shelf, fill your cart and pay in one go. Your items are held for 10 minutes while you pay.</p>
        <div className="hero-perks">
          <span><ShieldIcon size={18} /> Secure payments</span>
          <span><ClockIcon size={18} /> Items held at checkout</span>
          <span><BoltIcon size={18} /> {salesCount ? `${salesCount} flash ${salesCount === 1 ? 'deal' : 'deals'} live` : 'Live flash deals'}</span>
        </div>
      </div>
      <div className="hero-art" aria-hidden="true">
        <div className="hero-blob hero-blob-1" />
        <div className="hero-blob hero-blob-2" />
        <div className="hero-card hero-card-1"><span>🥭</span><b>Fresh picks</b></div>
        <div className="hero-card hero-card-2"><span>🧺</span><b>Daily needs</b></div>
        <div className="hero-card hero-card-3"><span>⚡</span><b>Flash deals</b></div>
      </div>
    </section>
  )
}

// Live rail of armed flash sales. Units left come straight from Redis, refreshed every 3 s.
function FlashStrip({ sales, userId, onOpen, onAdd, adding }) {
  if (!sales?.length) return null
  return (
    <section className="flash-strip" aria-labelledby="flash-title">
      <div className="flash-strip-head">
        <div>
          <h2 id="flash-title"><BoltIcon size={22} /> Flash deals</h2>
          <p>Grab them before they go. Units are handed out by Redis first, so the rush never reaches the database.</p>
        </div>
        <span className="live-pill"><span className="live-dot" /> Live · every 3 s</span>
      </div>
      <ul className="flash-cards">
        {sales.map((s) => (
          <li key={s.productId} className={`flash-card ${s.remaining <= 0 ? 'is-sold-out' : ''}`}>
            <button className="flash-name" onClick={() => onOpen(s.productId)}>{s.name}</button>
            <span className="price">{money(s.price)}</span>
            <span className="flash-left">
              {s.remaining > 0 ? <><strong>{s.remaining}</strong> left</> : <strong>Sold out</strong>}
            </span>
            <button
              className="btn btn-flash btn-block"
              disabled={!userId || s.remaining <= 0 || adding === s.productId}
              title={!userId ? 'Pick a shopper in the top bar first' : undefined}
              onClick={() => onAdd({ id: s.productId, name: s.name })}
            >
              {adding === s.productId ? 'Adding…' : 'Grab deal'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

function SkeletonGrid() {
  return (
    <ul className="grid" aria-hidden="true">
      {Array.from({ length: 8 }, (_, i) => (
        <li key={i} className="tile is-skeleton">
          <div className="sk sk-img" />
          <div className="tile-body">
            <div className="sk sk-line" />
            <div className="sk sk-line short" />
            <div className="sk sk-btn" />
          </div>
        </li>
      ))}
    </ul>
  )
}

export default function Shop({ userId, cartQty, search, onClearSearch, onOpen, onCartChanged, notify, goManage }) {
  const [page, setPage] = useState(0)
  const { data, error, loading, reload } = useLoad(() => api.products.list(page, 12), [page])
  const [adding, setAdding] = useState(null)
  const sales = usePoll(() => api.flashSale.active(), 3000, [])
  const onSale = new Map((sales || []).map((s) => [s.productId, s]))
  const gridRef = useRef()

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

  const setQty = async (p, qty) => {
    setAdding(p.id)
    try {
      if (qty <= 0) await api.cart.remove(p.id)
      else await api.cart.update(p.id, qty)
      await onCartChanged()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setAdding(null)
    }
  }

  const changePage = (p) => {
    setPage(p)
    gridRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  const all = data?.content || []
  // Search API comes later; until then the box filters the page that is loaded.
  const needle = search.toLowerCase()
  const products = needle ? all.filter((p) => (p.name || '').toLowerCase().includes(needle)) : all
  const showHero = page === 0 && !search

  return (
    <section className="shop">
      {showHero && <Hero salesCount={sales?.length || 0} />}

      <FlashStrip sales={sales} userId={userId} onOpen={onOpen} onAdd={add} adding={adding} />

      <div className="section-head" ref={gridRef}>
        <div>
          <h2>{search ? <>Results for “{search}”</> : 'All products'}</h2>
          {data && (
            <span className="muted">
              {search
                ? `${products.length} ${products.length === 1 ? 'match' : 'matches'} on this page`
                : `${(data.totalElements ?? all.length).toLocaleString('en-IN')} items`}
            </span>
          )}
        </div>
      </div>

      {search && (
        <div className="search-note">
          <SearchIcon size={16} />
          <span>Full catalogue search is coming soon. For now this filters the products on the current page.</span>
          <button className="link-btn" onClick={onClearSearch}><XIcon size={14} /> Clear</button>
        </div>
      )}

      {error && (
        <div className="stack">
          <Problem error={error} />
          <button className="btn" onClick={reload}>Try again</button>
        </div>
      )}
      {loading && !data && <SkeletonGrid />}

      {data && all.length === 0 && (
        <div className="empty">
          <div className="empty-art" aria-hidden="true">🧺</div>
          <h2>The shelf is empty</h2>
          <p>Create your first product in Manage, then come back here.</p>
          <button className="btn" onClick={goManage}>Go to Manage</button>
        </div>
      )}
      {data && all.length > 0 && products.length === 0 && (
        <div className="empty">
          <div className="empty-art" aria-hidden="true">🔍</div>
          <h2>Nothing matches “{search}” on this page</h2>
          <p>Try another page, or clear the search.</p>
          <button className="btn" onClick={onClearSearch}>Clear search</button>
        </div>
      )}

      <ul className={`grid ${loading && data ? 'is-loading' : ''}`}>
        {products.map((p) => {
          const sale = onSale.get(p.id)
          return (
            <li key={p.id} className="tile">
              <button className="tile-open" onClick={() => onOpen(p.id)} aria-label={`Open ${p.name}`}>
                <Thumb src={p.thumbnailUrl} alt={p.name} className="tile-img" />
                {sale && (
                  <span className="tile-flag">
                    <BoltIcon size={12} /> Flash · {sale.remaining} left
                  </span>
                )}
                {p.stock > 0 && p.stock <= 5 && !sale && <span className="tile-flag tile-flag-low">Only {p.stock} left</span>}
              </button>
              <div className="tile-body">
                <button className="tile-name" onClick={() => onOpen(p.id)} title={p.name}>{p.name}</button>
                <div className="tile-row">
                  <span className="price">{money(p.price)}</span>
                  <StockLabel stock={p.stock} />
                </div>
                <CartControl
                  product={p}
                  qty={cartQty?.get(p.id) || 0}
                  userId={userId}
                  busy={adding === p.id}
                  onAdd={() => add(p)}
                  onSet={(q) => setQty(p, q)}
                />
              </div>
            </li>
          )
        })}
      </ul>

      <Pager page={data?.page ?? page} totalPages={data?.totalPages} onChange={changePage} />
    </section>
  )
}
