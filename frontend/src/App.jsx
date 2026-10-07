import { useCallback, useEffect, useRef, useState } from 'react'
import { api, can, getSession, onSessionChange, signOut, subscribe } from './api.js'
import Login from './pages/Login.jsx'
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

const initial = (name) => (name || '?').trim().slice(0, 1).toUpperCase()

// The account menu: who is signed in, their role, and Sign out.
function AccountMenu({ user, onSignOut }) {
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
        <span className="avatar">{initial(user.name)}</span>
        <span className="acct-text">
          <small>Signed in{user.role === 'ADMIN' ? ' · admin' : ''}</small>
          <strong>{user.name}</strong>
        </span>
        <ChevronDown size={16} className="acct-caret" />
      </button>
      {open && (
        <div className="acct-pop" role="dialog" aria-label="Account">
          <div className="acct-pop-head">
            <strong>{user.name}</strong>
            <span className="muted">{user.email}</span>
            <span className={`role-badge role-${user.role?.toLowerCase()}`}>{user.role}</span>
          </div>
          <button className="acct-foot" onClick={() => { setOpen(false); onSignOut() }}>
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

export default function App() {
  const [view, setView] = useState({ name: 'shop' })
  const [session, setSession] = useState(getSession)
  const [users, setUsers] = useState([])
  const userId = session?.user?.id ?? null
  const userName = session?.user?.name ?? null
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

  useEffect(() => subscribe((log) => setReqCount(log.length)), [])
  // Signed out (button, expired token, Kirana restarted with a new key): back to the login page.
  useEffect(() => onSessionChange(setSession), [])

  const notify = useCallback((message, kind = 'ok') => {
    clearTimeout(toastTimer.current)
    setToast({ message, kind, key: Date.now() })
    toastTimer.current = setTimeout(() => setToast(null), 3500)
  }, [])

  // Manage → Shoppers: a bounded search over GET /users (admins only).
  const loadUsers = useCallback(async (_selectId, q = '') => {
    if (!can('users:read')) return setUsers([])
    try {
      setUsers((await api.users.list(q)) || [])
    } catch {
      setUsers([])
    }
  }, [])
  useEffect(() => {
    if (view.name === 'manage') loadUsers()
  }, [view.name, loadUsers, session])

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

  // Re-read the cart on every page change and whenever this tab comes back into view: another
  // tab may have changed it (placed the order, added items), and the badge must not lag the page.
  useEffect(() => {
    refreshCart()
  }, [refreshCart, view.name])
  useEffect(() => {
    const onVisible = () => document.visibilityState === 'visible' && refreshCart()
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('focus', onVisible)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('focus', onVisible)
    }
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

  // Hiding is convenience only: Kirana and the AI service answer 403 without the permission.
  const isAdmin = can('catalog:write')
  const navItems = [
    ['shop', 'Home', HomeIcon],
    ['orders', 'Orders', BoxIcon],
    ['cart', 'Cart', CartIcon],
    ...(isAdmin ? [['manage', 'Manage', StoreIcon]] : []),
  ]

  if (!session) {
    return <Login notify={notify} />
  }

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
            <AccountMenu user={session.user} onSignOut={() => { signOut(); go('shop') }} />
            <nav className="topnav" aria-label="Main">
              <button className={`topnav-item ${activeTab === 'orders' ? 'is-active' : ''}`} onClick={() => go('orders')}>
                <BoxIcon /> <span>Orders</span>
              </button>
              {isAdmin && (
                <button className={`topnav-item ${activeTab === 'manage' ? 'is-active' : ''}`} onClick={() => go('manage')}>
                  <StoreIcon /> <span>Manage</span>
                </button>
              )}
              <button className={`topnav-item topnav-cart ${activeTab === 'cart' ? 'is-active' : ''}`} onClick={() => go('cart')}>
                <span className="cart-ico">
                  <CartIcon />
                  {cartCount > 0 && <span className="cart-badge" key={cartCount}>{cartCount}</span>}
                </span>
                <span>Cart</span>
              </button>
            </nav>
            <div className="devtools" role="group" aria-label="Developer tools">
              {can('system') && (
                <button
                  className={`req-toggle ${labOpen ? 'is-on' : ''}`}
                  onClick={() => setLabOpen(true)}
                  title="Resilience lab: circuit breakers, bulkhead, fault injection"
                >
                  <GaugeIcon size={16} /> <span className="req-label">Lab</span>
                </button>
              )}
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
        {view.name === 'manage' && isAdmin && (
          <Manage key={view.tab || 'catalog'} initialTab={view.tab} notify={notify} users={users} onUsersChanged={loadUsers} />
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

      <ChatDock userId={userId} userName={userName} inspectorOpen={inspectorOpen}
                onOpenProduct={(id) => go('product', { id })} onCartChanged={refreshCart} notify={notify} />

      {toast && (
        <div className={`toast toast-${toast.kind}`} role="status" key={toast.key}>
          <span className="toast-ico">{toast.kind === 'error' ? <AlertIcon size={18} /> : <CheckIcon size={18} />}</span>
          <span>{toast.message}</span>
        </div>
      )}
    </div>
  )
}
