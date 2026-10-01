import { useCallback, useEffect, useRef, useState } from 'react'
import { api, money, uploadToStorage } from '../api.js'
import { useLoad, usePoll } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'
import Pager from '../components/Pager.jsx'
import Modal from '../components/Modal.jsx'
import { BoltIcon, BoxIcon, CheckIcon, ChevronRight, PlusIcon, SearchIcon, TrashIcon, UserIcon, XIcon } from '../components/icons.jsx'

const TABS = [
  ['catalog', 'Catalogue', BoxIcon],
  ['shoppers', 'Shoppers', UserIcon],
]

function StatusPill({ p, onSale }) {
  if (onSale) return <span className="pill pill-flash"><BoltIcon size={12} /> Flash sale</span>
  if (p.stock === null || p.stock === undefined) return <span className="pill">Unknown</span>
  if (p.stock <= 0) return <span className="pill pill-out">Out of stock</span>
  if (p.stock <= 5) return <span className="pill pill-low">Low stock</span>
  return <span className="pill pill-ok">In stock</span>
}

export default function Manage({ notify, onUsersChanged, users, initialTab, userId, onChooseUser }) {
  const [tab, setTab] = useState(initialTab === 'shoppers' ? 'shoppers' : 'catalog')
  const [page, setPage] = useState(0)
  const list = useLoad(() => api.products.list(page, 10), [page])
  // null = closed, 'new' = create form, { id, name } = edit that product
  const [selected, setSelected] = useState(null)

  const refresh = () => list.reload()
  const sales = usePoll(() => api.flashSale.active(), 3000, [])
  const onSale = new Set((sales || []).map((s) => s.productId))
  const close = useCallback(() => setSelected(null), [])
  const rows = list.data?.content || []

  return (
    <section className="manage-page">
      <header className="page-head manage-head">
        <div>
          <span className="eyebrow">Admin</span>
          <h1>Manage store</h1>
          <p>Products, stock, flash sales, images and shoppers. Click a product to edit it.</p>
        </div>
        {list.data && (
          <div className="kpis">
            <div className="kpi"><strong>{(list.data.totalElements ?? 0).toLocaleString('en-IN')}</strong><span>Products</span></div>
            <div className="kpi"><strong>{sales?.length ?? 0}</strong><span>Flash sales live</span></div>
          </div>
        )}
      </header>

      <div className="manage-bar">
        <div className="seg" role="tablist" aria-label="Manage sections">
          {TABS.map(([key, label, Icon]) => (
            <button key={key} role="tab" aria-selected={tab === key} className={`seg-btn ${tab === key ? 'is-active' : ''}`} onClick={() => setTab(key)}>
              <Icon size={16} /> {label}
            </button>
          ))}
        </div>
        {tab === 'catalog' && (
          <button className="btn btn-add" onClick={() => setSelected('new')}><PlusIcon size={16} /> New product</button>
        )}
      </div>

      {tab === 'catalog' && (
        <div className="card ptable-card">
          {list.error && <div className="ptable-msg"><Problem error={list.error} compact /></div>}
          {list.data && rows.length === 0 && (
            <div className="panel-empty">
              <div className="empty-art" aria-hidden="true"><BoxIcon size={32} /></div>
              <h2>No products yet</h2>
              <p className="muted">Create your first product to fill the shelf.</p>
              <button className="btn btn-add" onClick={() => setSelected('new')}><PlusIcon size={16} /> New product</button>
            </div>
          )}
          {(rows.length > 0 || list.loading) && (
            <table className={`ptable ${list.loading && list.data ? 'is-loading' : ''}`}>
              <thead>
                <tr><th>Product</th><th>Price</th><th>Stock</th><th>Status</th><th aria-label="Actions" /></tr>
              </thead>
              <tbody>
                {!list.data && Array.from({ length: 6 }, (_, i) => (
                  <tr key={i} aria-hidden="true"><td colSpan="5"><div className="sk sk-line" /></td></tr>
                ))}
                {rows.map((p) => (
                  <tr key={p.id} className="ptable-row" onClick={() => setSelected({ id: p.id, name: p.name })}>
                    <td data-label="Product">
                      <div className="ptable-product">
                        <Thumb src={p.thumbnailUrl} alt={p.name} className="ptable-img" />
                        <div>
                          <button className="ptable-name" onClick={(e) => { e.stopPropagation(); setSelected({ id: p.id, name: p.name }) }}>
                            {p.name}
                          </button>
                          <span className="ptable-id">ID {p.id}</span>
                        </div>
                      </div>
                    </td>
                    <td data-label="Price" className="ptable-num">{money(p.price)}</td>
                    <td data-label="Stock" className="ptable-num">
                      <span className={p.stock === 0 ? 'is-out' : ''}>{p.stock ?? '—'}</span>
                    </td>
                    <td data-label="Status"><StatusPill p={p} onSale={onSale.has(p.id)} /></td>
                    <td className="ptable-act"><span className="ptable-edit">Edit <ChevronRight size={16} /></span></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div className="ptable-foot">
            <Pager page={list.data?.page ?? page} totalPages={list.data?.totalPages} onChange={setPage} />
          </div>
        </div>
      )}

      {tab === 'shoppers' && <Users users={users} onChanged={onUsersChanged} notify={notify} userId={userId} onChoose={onChooseUser} />}

      {selected === 'new' && (
        <Modal title="New product" subtitle="Add the basics now. Stock, flash sale and images open after you create it." onClose={close}>
          <ProductForm
            key="new"
            onSaved={(p) => {
              notify(`Created ${p.name}`)
              refresh()
              setSelected({ id: p.id, name: p.name })
            }}
          />
        </Modal>
      )}
      {selected && selected !== 'new' && (
        <Modal title={selected.name} subtitle={`Product ID ${selected.id}`} size="lg" onClose={close}>
          <ProductEditor
            key={selected.id}
            id={selected.id}
            notify={notify}
            onChanged={refresh}
            onDeleted={() => {
              setSelected(null)
              refresh()
            }}
          />
        </Modal>
      )}
    </section>
  )
}

function ProductForm({ product, onSaved, onReload }) {
  const [form, setForm] = useState({
    name: product?.name || '',
    description: product?.description || '',
    price: product?.price ?? '',
  })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const fieldErr = (f) => error?.problem?.errors?.find((e) => e.field === f)?.message

  const save = async (e) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    // Price is sent exactly as typed (a string). Decide in the backend how to parse it.
    // version: the one this form loaded. If someone saved since, the backend answers 409.
    const body = { name: form.name, description: form.description, price: form.price, version: product?.version }
    try {
      const saved = product ? await api.products.update(product.id, body) : await api.products.create(body)
      onSaved(saved)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={save} noValidate>
      {product && <h2>Details</h2>}
      <label>
        <span>Name</span>
        <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        {fieldErr('name') && <em className="field-err">{fieldErr('name')}</em>}
      </label>
      <label>
        <span>Description</span>
        <textarea rows="3" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
        {fieldErr('description') && <em className="field-err">{fieldErr('description')}</em>}
      </label>
      <label>
        <span>Price (₹)</span>
        <input inputMode="decimal" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} />
        {fieldErr('price') && <em className="field-err">{fieldErr('price')}</em>}
      </label>
      {error && !error.problem?.errors?.length && <Problem error={error} compact />}
      {error?.status === 409 && onReload && (
        <button type="button" className="btn-quiet" onClick={onReload}>Reload product (your edits here will be replaced)</button>
      )}
      <button className="btn" disabled={busy}>{busy ? 'Saving…' : product ? 'Save changes' : 'Create product'}</button>
    </form>
  )
}

function ProductEditor({ id, notify, onChanged, onDeleted }) {
  const { data: p, error, reload } = useLoad(() => api.products.get(id), [id])

  const del = async () => {
    if (!window.confirm(`Delete ${p.name}? This cannot be undone.`)) return
    try {
      await api.products.remove(id)
      notify(`Deleted ${p.name}`)
      onDeleted()
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  if (error) return <Problem error={error} />
  if (!p) return <div className="editor" aria-hidden="true"><div className="sk sk-line" /><div className="sk sk-line short" /><div className="sk sk-btn" /></div>

  return (
    <div className="editor">
      <div className="editor-grid">
        <div className="editor-col">
          <ProductForm
            key={p.version}
            product={p}
            onReload={reload}
            onSaved={(saved) => {
              notify(`Saved ${saved.name}`)
              reload()
              onChanged()
            }}
          />
          <Images product={p} notify={notify} onChanged={() => { reload(); onChanged() }} />
        </div>
        <div className="editor-col">
          <Stock productId={id} notify={notify} onChanged={onChanged} />
          <FlashSale productId={id} productName={p.name} notify={notify} onChanged={onChanged} />
        </div>
      </div>
      <div className="danger">
        <div>
          <strong>Delete this product</strong>
          <p className="muted">It disappears from the shop and listings. This cannot be undone.</p>
        </div>
        <button className="btn-danger" onClick={del}><TrashIcon size={16} /> Delete product</button>
      </div>
    </div>
  )
}

// Stage 4 flash-sale gate. While on, Redis decides who gets the remaining units and turns
// everyone else away before checkout touches the database.
function FlashSale({ productId, productName, notify, onChanged }) {
  const [status, setStatus] = useState(null)
  const [busy, setBusy] = useState(false)
  const live = usePoll(() => api.flashSale.status(productId, true), 2000, [productId, status])
  const s = live || status

  const run = async (fn, msg) => {
    setBusy(true)
    try {
      setStatus(await fn())
      onChanged()
      notify(msg)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className={`form flash-panel ${s?.active ? 'is-on' : ''}`}>
      <div className="flash-panel-head">
        <h2>Flash sale</h2>
        {s && <span className={`badge ${s.active ? 'badge-flash' : ''}`}>{s.active ? 'On' : 'Off'}</span>}
      </div>
      {s?.active ? (
        <>
          <div className="stock-now">
            <strong>{s.remaining}</strong> units left at the gate (live from Redis)
          </div>
          <p className="muted">
            Shoppers see this product in the Shop's flash-sale strip. Buyers beyond these units get "Out of stock"
            after one cart query, with no database transaction.
          </p>
          <div className="stock-actions">
            <button className="btn-quiet" disabled={busy} onClick={() => run(() => api.flashSale.start(productId), 'Gate re-synced from stock')}>
              Re-sync from stock
            </button>
            <button className="btn-quiet" disabled={busy} onClick={() => run(async () => { await api.flashSale.stop(productId); return { active: false } }, 'Flash sale stopped')}>
              Stop flash sale
            </button>
          </div>
          <Rush productId={productId} productName={productName} notify={notify} onDone={onChanged} />
        </>
      ) : (
        <>
          <p className="muted">
            Starting copies this product's current stock into Redis. Tip: set stock low (say 5) to watch it sell out.
          </p>
          <button className="btn" disabled={busy} onClick={() => run(() => api.flashSale.start(productId), 'Flash sale started')}>
            Start flash sale
          </button>
        </>
      )}
    </div>
  )
}

// Simulates many shoppers checking out at the same moment: creates throwaway shoppers, puts one
// unit in each cart, then fires every checkout at once. Each checkout also shows in Requests.
function Rush({ productId, productName, notify, onDone }) {
  const [buyers, setBuyers] = useState(20)
  const [phase, setPhase] = useState(null)
  const [result, setResult] = useState(null)

  const go = async () => {
    setResult(null)
    try {
      const n = Math.min(Math.max(Number(buyers) || 1, 1), 50)
      const run = Date.now().toString(36)
      setPhase(`Creating ${n} shoppers and filling their carts…`)
      const ids = []
      for (let i = 1; i <= n; i++) {
        const u = await api.rush.createShopper(`Rush buyer ${run}-${i}`, `rush-${run}-${i}@kirana.test`)
        await api.rush.addToCart(u.id, productId)
        ids.push(u.id)
      }
      setPhase(`${n} checkouts at once…`)
      const started = performance.now()
      const outcomes = await Promise.all(ids.map((id) => api.rush.checkout(id)))
      const took = Math.round(performance.now() - started)
      const won = outcomes.filter((o) => o.status === 201)
      const refusedAtGate = outcomes.filter((o) => o.status === 409 && (o.queries ?? 99) <= 1)
      const refusedByDb = outcomes.filter((o) => o.status === 409 && (o.queries ?? 0) > 1)
      const other = outcomes.filter((o) => o.status !== 201 && o.status !== 409)
      const avgSql = (list) => (list.length ? (list.reduce((t, o) => t + (o.queries || 0), 0) / list.length).toFixed(1) : '–')
      setResult({ n, took, won, refusedAtGate, refusedByDb, other, avgSql })
      notify(`Rush done: ${won.length} of ${n} bought ${productName}`)
      onDone()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setPhase(null)
    }
  }

  return (
    <div className="rush">
      <h3>Simulate a rush</h3>
      <div className="stock-actions">
        <label className="inline">
          <span>Buyers</span>
          <input id="rush-buyers" type="number" min="1" max="50" value={buyers} onChange={(e) => setBuyers(e.target.value)} />
        </label>
        <button className="btn" disabled={!!phase} onClick={go}>{phase ? 'Running…' : 'Start rush'}</button>
      </div>
      {phase && <p className="muted">{phase}</p>}
      {result && (
        <table className="rush-result">
          <tbody>
            <tr><th>Bought</th><td>{result.won.length}</td><td className="muted">avg {result.avgSql(result.won)} SQL each: the full checkout</td></tr>
            <tr><th>Refused at the Redis gate</th><td>{result.refusedAtGate.length}</td><td className="muted">avg {result.avgSql(result.refusedAtGate)} SQL each: one cart read, no transaction</td></tr>
            <tr><th>Refused by the database</th><td>{result.refusedByDb.length}</td><td className="muted">passed the gate, stopped by the final guard (gate out of sync)</td></tr>
            {result.other.length > 0 && (
              <tr><th>Other errors</th><td>{result.other.length}</td><td className="muted">statuses {[...new Set(result.other.map((o) => o.status))].join(', ')}</td></tr>
            )}
            <tr><th>All {result.n} checkouts</th><td>{result.took} ms</td><td className="muted">browsers send about 6 requests at a time to one host, so this is a gentle rush</td></tr>
          </tbody>
        </table>
      )}
    </div>
  )
}

function Stock({ productId, notify, onChanged }) {
  const { data, error, reload } = useLoad(() => api.inventory.get(productId), [productId])
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (data) setValue(String(data.quantity))
  }, [data])

  const run = async (fn, msg) => {
    setBusy(true)
    try {
      await fn()
      await reload()
      onChanged()
      notify(msg)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="form">
      <h2>Stock</h2>
      {error && <Problem error={error} compact />}
      {data && (
        <>
          <div className="stock-now">
            <strong>{data.quantity}</strong> units on hand
          </div>
          <div className="stock-actions">
            <label className="inline">
              <span>Set to</span>
              <input type="number" min="0" value={value} onChange={(e) => setValue(e.target.value)} />
            </label>
            <button className="btn" disabled={busy} onClick={() => run(() => api.inventory.set(productId, Number(value)), 'Stock set')}>
              Set stock
            </button>
            <span className="stock-sep" />
            {[-1, +1, +10].map((d) => (
              <button key={d} className="btn-quiet" disabled={busy} onClick={() => run(() => api.inventory.adjust(productId, d), `Stock adjusted by ${d > 0 ? '+' : ''}${d}`)}>
                {d > 0 ? `+${d}` : d}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

const STEPS = ['Ask backend for a signed upload policy', 'Upload file straight to MinIO', 'Ask backend to confirm the upload']

// Browser-side limit is for user experience only. MinIO enforces the real limit from the signed policy.
const UI_MAX_BYTES = 5 * 1024 * 1024

function Images({ product, notify, onChanged }) {
  const [step, setStep] = useState(-1) // -1 idle, 0..2 running, 3 done
  const [failedAt, setFailedAt] = useState(null)
  const [error, setError] = useState(null)
  const [skipUiCheck, setSkipUiCheck] = useState(false)

  const upload = async (file) => {
    setError(null)
    setFailedAt(null)
    if (!skipUiCheck && file.size > UI_MAX_BYTES) {
      setStep(-1)
      setError({ problem: { title: 'File too large', detail: `This file is ${(file.size / 1048576).toFixed(1)} MB. The limit is 5 MB.` } })
      return
    }
    let current = 0
    try {
      setStep(0)
      const ticket = await api.images.requestUpload(product.id, {
        fileName: file.name,
        contentType: file.type,
        sizeBytes: file.size,
      })
      current = 1
      setStep(1)
      await uploadToStorage(ticket, file)
      current = 2
      setStep(2)
      await api.images.confirm(product.id, ticket.imageId)
      setStep(3)
      notify('Image added')
      onChanged()
    } catch (e) {
      setFailedAt(current)
      setError(e)
    }
  }

  const remove = async (img) => {
    try {
      await api.images.remove(product.id, img.id)
      notify('Image removed')
      onChanged()
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  return (
    <div className="form">
      <h2>Images</h2>
      <div className="img-grid">
        {(product.images || []).map((img) => (
          <figure key={img.id}>
            <Thumb src={img.url} alt="" />
            <figcaption>
              <span className="muted">{img.contentType}, {Math.round((img.sizeBytes || 0) / 1024)} KB</span>
              <button className="btn-quiet" onClick={() => remove(img)}>Remove</button>
            </figcaption>
          </figure>
        ))}
      </div>
      <label className="file">
        <input
          type="file"
          accept="image/*"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) upload(f)
            e.target.value = ''
          }}
        />
        <span className="btn">Upload image</span>
      </label>
      <label className="check">
        <input type="checkbox" checked={skipUiCheck} onChange={(e) => setSkipUiCheck(e.target.checked)} />
        <span>Skip the browser size check, to prove the backend policy enforces the limit</span>
      </label>
      {step >= 0 && (
        <ol className="steps">
          {STEPS.map((s, i) => {
            const state = failedAt === i ? 'failed' : step > i ? 'done' : step === i && failedAt === null ? 'running' : 'waiting'
            return (
              <li key={s} className={`step step-${state}`}>
                <span className="step-dot" aria-hidden="true" />
                {s}
              </li>
            )
          })}
        </ol>
      )}
      {error && <Problem error={error} compact />}
    </div>
  )
}

// Shoppers: a bounded search over GET /users (20 newest, or 20 matches). "Shop as" only changes
// which X-User-Id the app sends from now on, exactly like the menu in the top bar.
function Users({ users, onChanged, notify, userId, onChoose }) {
  const [q, setQ] = useState('')
  const [adding, setAdding] = useState(false)
  const qRef = useRef('')
  const first = useRef(true)

  useEffect(() => {
    qRef.current = q
    if (first.current) {
      first.current = false
      return
    }
    const t = setTimeout(() => onChanged(undefined, q.trim()), 250)
    return () => clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])
  // Leave the shared list unfiltered for the top-bar menu when this tab goes away.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => () => { if (qRef.current) onChanged(undefined, '') }, [])

  const list = users || []

  return (
    <>
      <div className="users-bar">
        <label className="table-search">
          <SearchIcon size={16} />
          <input type="search" placeholder="Search shoppers by name or email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search shoppers" />
          {q && <button type="button" className="search-clear" onClick={() => setQ('')} aria-label="Clear search"><XIcon size={14} /></button>}
        </label>
        <button className="btn btn-add" onClick={() => setAdding(true)}><PlusIcon size={16} /> Add shopper</button>
      </div>

      <div className="card ptable-card">
        <div className="table-note">
          <UserIcon size={16} />
          <span>
            No login yet. The shopper you pick is sent as the <code>X-User-Id</code> header.{' '}
            {q ? `Showing up to 20 matches for “${q}”.` : 'Showing the 20 newest shoppers; search to find others.'}
          </span>
        </div>
        {list.length === 0 ? (
          <div className="panel-empty">
            <div className="empty-art" aria-hidden="true"><UserIcon size={32} /></div>
            <h2>{q ? 'No shopper matches' : 'No shoppers yet'}</h2>
            <p className="muted">{q ? 'Try another name or email.' : 'Add one to start shopping.'}</p>
            {!q && <button className="btn btn-add" onClick={() => setAdding(true)}><PlusIcon size={16} /> Add shopper</button>}
          </div>
        ) : (
          <table className="ptable utable">
            <thead>
              <tr><th>Shopper</th><th>Email</th><th>ID</th><th aria-label="Actions" /></tr>
            </thead>
            <tbody>
              {list.map((u) => {
                const current = u.id === userId
                return (
                  <tr key={u.id} className={current ? 'is-current' : ''}>
                    <td data-label="Shopper">
                      <div className="ptable-product">
                        <span className="avatar">{(u.name || '?').slice(0, 1).toUpperCase()}</span>
                        <div>
                          <strong className="utable-name">{u.name}</strong>
                        </div>
                      </div>
                    </td>
                    <td data-label="Email" className="utable-email" title={u.email}>{u.email}</td>
                    <td data-label="ID"><code className="ptable-id">{u.id}</code></td>
                    <td className="utable-act">
                      {current ? (
                        <span className="utable-check"><CheckIcon size={16} /> Current</span>
                      ) : (
                        <button className="btn-quiet" onClick={() => { onChoose(u); notify(`Now shopping as ${u.name}`) }}>Shop as</button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {adding && (
        <Modal title="Add shopper" subtitle="You'll switch to the new shopper once they're created." onClose={() => setAdding(false)}>
          <UserForm
            notify={notify}
            onCreated={(u) => {
              setAdding(false)
              setQ('')
              onChanged(u.id)
            }}
          />
        </Modal>
      )}
    </>
  )
}

function UserForm({ notify, onCreated }) {
  const [form, setForm] = useState({ name: '', email: '' })
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const fieldErr = (f) => error?.problem?.errors?.find((e) => e.field === f)?.message

  const create = async (e) => {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const u = await api.users.create(form)
      notify(`Created shopper ${u.name}`)
      onCreated(u)
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form className="form" onSubmit={create} noValidate>
      <label>
        <span>Name</span>
        <input autoFocus placeholder="e.g. Asha Verma" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        {fieldErr('name') && <em className="field-err">{fieldErr('name')}</em>}
      </label>
      <label>
        <span>Email</span>
        <input type="email" placeholder="asha@example.com" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        {fieldErr('email') && <em className="field-err">{fieldErr('email')}</em>}
      </label>
      {error && !error.problem?.errors?.length && <Problem error={error} compact />}
      <button className="btn btn-add" disabled={busy}><PlusIcon size={16} /> {busy ? 'Adding…' : 'Add shopper'}</button>
    </form>
  )
}
