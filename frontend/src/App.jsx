import { useCallback, useEffect, useRef, useState } from 'react'
import { api, setUserId as setApiUser, subscribe } from './api.js'
import Inspector from './components/Inspector.jsx'
import ChatDock from './components/chat/ChatDock.jsx'
import Shop from './pages/Shop.jsx'
import ProductDetail from './pages/ProductDetail.jsx'
import Cart from './pages/Cart.jsx'
import Orders from './pages/Orders.jsx'
import Manage from './pages/Manage.jsx'
import Modal from './components/Modal.jsx'
import ResilienceLab from './components/ResilienceLab.jsx'
import {
  AlertIcon, BoxIcon, CartIcon, CheckIcon, ChevronDown, CodeIcon, GaugeIcon, HomeIcon, SearchIcon, StoreIcon, UserIcon, XIcon,
} from './components/icons.jsx'

function readSavedUser() {
  try {
    const v = localStorage.getItem('kirana.userId')
    return v ? Number(v) : null
  } catch {
    return null
  }
}

function readSavedUserName() {
  try {
    return localStorage.getItem('kirana.userName') || null
  } catch {
    return null
  }
}

const initial = (name) => (name || '?').trim().slice(0, 1).toUpperCase()

// The "account" menu: no login yet, so it picks which shopper's X-User-Id the app sends.
function ShopperMenu({ userId, userName, options, query, setQuery, onChoose, onManage }) {
  const [open, setOpen] = useState(false)
  const ref = useRef()

  useEffect(() => {
    if (!open) return
    const onDown = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false)
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="acct" ref={ref}>
      <button className={`acct-btn ${open ? 'is-open' : ''}`} onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="dialog">
        <span className="avatar">{userId ? initial(userName) : <UserIcon size={16} />}</span>
        <span className="acct-text">
          <small>{userId ? 'Shopping as' : 'Welcome'}</small>
          <strong>{userId ? userName || `Shopper ${userId}` : 'Pick a shopper'}</strong>
        </span>
        <ChevronDown size={16} className="acct-caret" />
      </button>
      {open && (
        <div className="acct-pop" role="dialog" aria-label="Switch shopper">
          <div className="acct-pop-head">
            <strong>Switch shopper</strong>
            <span className="muted">No login yet: this sets the X-User-Id header.</span>
          </div>
          <label className="acct-search">
            <SearchIcon size={16} />
            <input
              id="shopper-search"
              type="search"
              placeholder="Search by name or email"
              aria-label="Find shopper by name or email"
              value={query}
              autoFocus
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <ul className="acct-list" role="listbox" aria-label="Shoppers">
            {options.length === 0 && <li className="acct-none">{query ? 'No shopper matches' : 'No shoppers yet'}</li>}
            {options.map((u) => (
              <li key={u.id}>
                <button
                  role="option"
                  aria-selected={u.id === userId}
                  className={`acct-opt ${u.id === userId ? 'is-active' : ''}`}
                  onClick={() => {
                    onChoose(u)
                    setOpen(false)
                  }}
                >
                  <span className="avatar avatar-sm">{initial(u.name)}</span>
                  <span className="acct-opt-name">{u.name}</span>
                  {u.id === userId && <CheckIcon size={16} />}
                </button>
              </li>
            ))}
          </ul>
          <button className="acct-foot" onClick={() => { setOpen(false); onManage() }}>
            Add or manage shoppers
          </button>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [view, setView] = useState({ name: 'shop' })
  const [users, setUsers] = useState([])
  const [userQuery, setUserQuery] = useState('')
  const [userId, setUserId] = useState(readSavedUser)
  // The chosen shopper may not be in the current search results, so remember their name too.
  const [userName, setUserName] = useState(readSavedUserName)
  const [cartCount, setCartCount] = useState(0)
  // productId -> quantity, from the same GET /cart that feeds the count. Lets product cards show a stepper.
  const [cartQty, setCartQty] = useState(() => new Map())
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [labOpen, setLabOpen] = useState(false)
  const closeLab = useCallback(() => setLabOpen(false), [])
  const [reqCount, setReqCount] = useState(0)
  const [toast, setToast] = useState(null)
  const [searchText, setSearchText] = useState('')
  const [search, setSearch] = useState('')
  const toastTimer = useRef()

  setApiUser(userId)

  useEffect(() => subscribe((log) => setReqCount(log.length)), [])

  const notify = useCallback((message, kind = 'ok') => {
    clearTimeout(toastTimer.current)
    setToast({ message, kind, key: Date.now() })
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }, [])

  const chooseUser = useCallback((u) => {
    setUserId(u ? u.id : null)
    setUserName(u ? u.name : null)
  }, [])

  // GET /users is a bounded search (20 newest, or 20 matches), never the whole table.
  const loadUsers = useCallback(async (selectId, q = '') => {
    try {
      const list = (await api.users.list(q)) || []
      setUsers(list)
      if (selectId) chooseUser(list.find((u) => u.id === selectId) || { id: selectId, name: `Shopper ${selectId}` })
      else if (!readSavedUser() && list.length) chooseUser(list[0])
    } catch {
      setUsers([])
    }
  }, [chooseUser])

  // Loads on mount (empty query), then searches as the person types, a quarter second after they stop.
  useEffect(() => {
    const t = setTimeout(() => loadUsers(undefined, userQuery), 250)
    return () => clearTimeout(t)
  }, [userQuery, loadUsers])

  useEffect(() => {
    try {
      if (userId) localStorage.setItem('kirana.userId', String(userId))
      if (userName) localStorage.setItem('kirana.userName', userName)
    } catch {
      /* storage unavailable, fine */
    }
  }, [userId, userName])

  const shopperOptions = userId && !users.some((u) => u.id === userId)
    ? [{ id: userId, name: userName || `Shopper ${userId}` }, ...users]
    : users

  const refreshCart = useCallback(async () => {
    if (!userId) {
      setCartQty(new Map())
      return setCartCount(0)
    }
    try {
      const cart = await api.cart.get()
      const items = cart?.items || []
      setCartCount(items.reduce((n, it) => n + (it.quantity || 0), 0))
      setCartQty(new Map(items.map((it) => [it.productId, it.quantity || 0])))
    } catch {
      setCartCount(0)
      setCartQty(new Map())
    }
  }, [userId])

  useEffect(() => {
    refreshCart()
  }, [refreshCart])

  const go = (name, extra = {}) => {
    setView({ name, ...extra })
    window.scrollTo({ top: 0 })
  }
  const activeTab = view.name === 'product' ? 'shop' : view.name

  const submitSearch = (e) => {
    e.preventDefault()
    setSearch(searchText.trim())
    go('shop')
  }
  const clearSearch = () => {
    setSearchText('')
    setSearch('')
  }

  const navItems = [
    ['shop', 'Home', HomeIcon],
    ['orders', 'Orders', BoxIcon],
    ['cart', 'Cart', CartIcon],
    ['manage', 'Manage', StoreIcon],
  ]

  return (
    <div className={`app ${inspectorOpen ? 'with-inspector' : ''}`}>
      <header className="topbar">
        <div className="topbar-inner">
          <button className="brand" onClick={() => { clearSearch(); go('shop') }} aria-label="Kirana home">
            <span className="brand-mark" aria-hidden="true">
              <svg viewBox="0 0 32 32" width="30" height="30">
                <rect width="32" height="32" rx="9" fill="currentColor" />
                <path d="M8 12h16l-1.6 10.2a2 2 0 0 1-2 1.8h-8.8a2 2 0 0 1-2-1.8Z" fill="#fff" />
                <path d="M12 12V10a4 4 0 0 1 8 0v2" stroke="#fff" strokeWidth="2.2" fill="none" strokeLinecap="round" />
                <circle cx="16" cy="18" r="2.2" fill="var(--accent)" />
              </svg>
            </span>
            <span className="brand-word">kirana<span className="brand-dot">.</span></span>
          </button>

          <form className="search" role="search" onSubmit={submitSearch}>
            <SearchIcon size={18} className="search-icon" />
            <input
              id="site-search"
              type="search"
              placeholder="Search for groceries, snacks, essentials…"
              aria-label="Search products"
              value={searchText}
              onChange={(e) => {
                setSearchText(e.target.value)
                if (!e.target.value) setSearch('')
              }}
            />
            {searchText && (
              <button type="button" className="search-clear" onClick={clearSearch} aria-label="Clear search">
                <XIcon size={16} />
              </button>
            )}
            <button className="search-go" type="submit">Search</button>
          </form>

          <div className="topbar-right">
            <ShopperMenu
              userId={userId}
              userName={userName}
              options={shopperOptions}
              query={userQuery}
              setQuery={setUserQuery}
              onChoose={chooseUser}
              onManage={() => go('manage', { tab: 'shoppers' })}
            />
            <nav className="topnav" aria-label="Main">
              <button className={`topnav-item ${activeTab === 'orders' ? 'is-active' : ''}`} onClick={() => go('orders')}>
                <BoxIcon /> <span>Orders</span>
              </button>
              <button className={`topnav-item ${activeTab === 'manage' ? 'is-active' : ''}`} onClick={() => go('manage')}>
                <StoreIcon /> <span>Manage</span>
              </button>
              <button className={`topnav-item topnav-cart ${activeTab === 'cart' ? 'is-active' : ''}`} onClick={() => go('cart')}>
                <span className="cart-ico">
                  <CartIcon />
                  {cartCount > 0 && <span className="cart-badge" key={cartCount}>{cartCount}</span>}
                </span>
                <span>Cart</span>
              </button>
            </nav>
            <div className="devtools" role="group" aria-label="Developer tools">
              <button
                className={`req-toggle ${labOpen ? 'is-on' : ''}`}
                onClick={() => setLabOpen(true)}
                title="Resilience lab: circuit breakers, bulkhead, fault injection"
              >
                <GaugeIcon size={16} /> <span className="req-label">Lab</span>
              </button>
              <button
                className={`req-toggle ${inspectorOpen ? 'is-on' : ''}`}
                onClick={() => setInspectorOpen((o) => !o)}
                title="Every API call this page makes"
              >
                <CodeIcon size={16} /> <span className="req-label">Requests</span> <span className="req-count">{reqCount}</span>
              </button>
            </div>
          </div>
        </div>
      </header>

      <main className="main">
        {view.name === 'shop' && (
          <Shop
            userId={userId}
            cartQty={cartQty}
            search={search}
            onClearSearch={clearSearch}
            onOpen={(id) => go('product', { id })}
            onCartChanged={refreshCart}
            notify={notify}
            goManage={() => go('manage')}
          />
        )}
        {view.name === 'product' && (
          <ProductDetail
            id={view.id}
            userId={userId}
            inCart={cartQty.get(view.id) || 0}
            onBack={() => go('shop')}
            goCart={() => go('cart')}
            onCartChanged={refreshCart}
            notify={notify}
          />
        )}
        {view.name === 'cart' && (
          <Cart userId={userId} onCartChanged={refreshCart} onOrdered={(id) => go('orders', { highlight: id })} notify={notify} goShop={() => go('shop')} />
        )}
        {view.name === 'orders' && <Orders userId={userId} highlight={view.highlight} notify={notify} goShop={() => go('shop')} />}
        {view.name === 'manage' && (
          <Manage key={view.tab || 'catalog'} initialTab={view.tab} notify={notify} users={users} onUsersChanged={loadUsers} userId={userId} onChooseUser={chooseUser} />
        )}
      </main>

      <footer className="site-foot">
        <div className="site-foot-inner">
          <span className="brand-word small">kirana<span className="brand-dot">.</span></span>
          <span className="muted">A practice shop for learning backend system design. Open Requests to watch each call land.</span>
        </div>
      </footer>

      <nav className="bottomnav" aria-label="Main">
        {navItems.map(([key, label, Icon]) => (
          <button key={key} className={`bottomnav-item ${activeTab === key ? 'is-active' : ''}`} onClick={() => go(key)}>
            <span className="cart-ico">
              <Icon />
              {key === 'cart' && cartCount > 0 && <span className="cart-badge">{cartCount}</span>}
            </span>
            <span>{label}</span>
          </button>
        ))}
      </nav>

      {labOpen && (
        <Modal
          title="Resilience lab"
          subtitle="Break a dependency, then use the shop and watch what happens here. Updates every 2 seconds."
          size="lg"
          onClose={closeLab}
        >
          <ResilienceLab notify={notify} embedded />
        </Modal>
      )}

      <Inspector open={inspectorOpen} onClose={() => setInspectorOpen(false)} />

      <ChatDock userId={userId} userName={userName} inspectorOpen={inspectorOpen} />

      {toast && (
        <div className={`toast toast-${toast.kind}`} role="status" key={toast.key}>
          <span className="toast-ico">{toast.kind === 'error' ? <AlertIcon size={18} /> : <CheckIcon size={18} />}</span>
          <span>{toast.message}</span>
        </div>
      )}
    </div>
  )
}
