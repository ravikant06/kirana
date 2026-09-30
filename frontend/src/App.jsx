import { useCallback, useEffect, useRef, useState } from 'react'
import { api, setUserId as setApiUser, subscribe } from './api.js'
import Inspector from './components/Inspector.jsx'
import ChatDock from './components/chat/ChatDock.jsx'
import Shop from './pages/Shop.jsx'
import ProductDetail from './pages/ProductDetail.jsx'
import Cart from './pages/Cart.jsx'
import Orders from './pages/Orders.jsx'
import Manage from './pages/Manage.jsx'

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

export default function App() {
  const [view, setView] = useState({ name: 'shop' })
  const [users, setUsers] = useState([])
  const [userQuery, setUserQuery] = useState('')
  const [userId, setUserId] = useState(readSavedUser)
  // The chosen shopper may not be in the current search results, so remember their name too.
  const [userName, setUserName] = useState(readSavedUserName)
  const [cartCount, setCartCount] = useState(0)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [reqCount, setReqCount] = useState(0)
  const [toast, setToast] = useState(null)
  const toastTimer = useRef()

  setApiUser(userId)

  useEffect(() => subscribe((log) => setReqCount(log.length)), [])

  const notify = useCallback((message, kind = 'ok') => {
    clearTimeout(toastTimer.current)
    setToast({ message, kind })
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
    if (!userId) return setCartCount(0)
    try {
      const cart = await api.cart.get()
      setCartCount((cart?.items || []).reduce((n, it) => n + (it.quantity || 0), 0))
    } catch {
      setCartCount(0)
    }
  }, [userId])

  useEffect(() => {
    refreshCart()
  }, [refreshCart])

  const go = (name, extra = {}) => setView({ name, ...extra })
  const tabs = [
    ['shop', 'Shop'],
    ['cart', cartCount ? `Cart (${cartCount})` : 'Cart'],
    ['orders', 'Orders'],
    ['manage', 'Manage'],
  ]
  const activeTab = view.name === 'product' ? 'shop' : view.name

  return (
    <div className={`app ${inspectorOpen ? 'with-inspector' : ''}`}>
      <header className="topbar">
        <button className="brand" onClick={() => go('shop')}>Kirana</button>
        <nav className="tabs">
          {tabs.map(([key, label]) => (
            <button key={key} className={`tab ${activeTab === key ? 'is-active' : ''}`} onClick={() => go(key)}>
              {label}
            </button>
          ))}
        </nav>
        <div className="topbar-right">
          <label className="who">
            <span>Shopping as</span>
            <input
              id="shopper-search"
              className="who-search"
              type="search"
              placeholder="Find shopper"
              aria-label="Find shopper by name or email"
              value={userQuery}
              onChange={(e) => setUserQuery(e.target.value)}
            />
            <select
              id="shopper-select"
              value={userId ?? ''}
              onChange={(e) => chooseUser(shopperOptions.find((u) => u.id === Number(e.target.value)) || null)}
            >
              {shopperOptions.length === 0 && <option value="">{userQuery ? 'No match' : 'No shoppers yet'}</option>}
              {shopperOptions.map((u) => (
                <option key={u.id} value={u.id}>{u.name}</option>
              ))}
            </select>
          </label>
          <button className={`req-toggle ${inspectorOpen ? 'is-on' : ''}`} onClick={() => setInspectorOpen((o) => !o)}>
            Requests <span className="req-count">{reqCount}</span>
          </button>
        </div>
      </header>

      <main className="main">
        {view.name === 'shop' && (
          <Shop userId={userId} onOpen={(id) => go('product', { id })} onCartChanged={refreshCart} notify={notify} goManage={() => go('manage')} />
        )}
        {view.name === 'product' && (
          <ProductDetail id={view.id} userId={userId} onBack={() => go('shop')} onCartChanged={refreshCart} notify={notify} />
        )}
        {view.name === 'cart' && (
          <Cart userId={userId} onCartChanged={refreshCart} onOrdered={(id) => go('orders', { highlight: id })} notify={notify} goShop={() => go('shop')} />
        )}
        {view.name === 'orders' && <Orders userId={userId} highlight={view.highlight} />}
        {view.name === 'manage' && <Manage notify={notify} users={users} onUsersChanged={loadUsers} />}
      </main>

      <Inspector open={inspectorOpen} onClose={() => setInspectorOpen(false)} />

      <ChatDock userId={userId} userName={userName} inspectorOpen={inspectorOpen} />

      {toast && (
        <div className={`toast toast-${toast.kind}`} role="status">
          {toast.message}
        </div>
      )}
    </div>
  )
}
