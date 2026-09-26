import { useCallback, useEffect, useRef, useState } from 'react'
import { api, setUserId as setApiUser, subscribe } from './api.js'
import Inspector from './components/Inspector.jsx'
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

export default function App() {
  const [view, setView] = useState({ name: 'shop' })
  const [users, setUsers] = useState([])
  const [userId, setUserId] = useState(readSavedUser)
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

  const loadUsers = useCallback(async (selectId) => {
    try {
      const list = await api.users.list()
      setUsers(list || [])
      if (selectId) setUserId(selectId)
      else if (list?.length && !list.some((u) => u.id === readSavedUser())) setUserId(list[0].id)
    } catch {
      setUsers([])
    }
  }, [])

  useEffect(() => {
    loadUsers()
  }, [loadUsers])

  useEffect(() => {
    try {
      if (userId) localStorage.setItem('kirana.userId', String(userId))
    } catch {
      /* storage unavailable, fine */
    }
  }, [userId])

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
            <select value={userId ?? ''} onChange={(e) => setUserId(e.target.value ? Number(e.target.value) : null)}>
              {users.length === 0 && <option value="">No shoppers yet</option>}
              {users.map((u) => (
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

      {toast && (
        <div className={`toast toast-${toast.kind}`} role="status">
          {toast.message}
        </div>
      )}
    </div>
  )
}
