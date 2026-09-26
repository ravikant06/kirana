import { useEffect, useState } from 'react'
import { api, money, uploadToStorage } from '../api.js'
import { useLoad } from '../hooks.js'
import Problem from '../components/Problem.jsx'
import Thumb from '../components/Thumb.jsx'
import Pager from '../components/Pager.jsx'

export default function Manage({ notify, onUsersChanged, users }) {
  const [page, setPage] = useState(0)
  const list = useLoad(() => api.products.list(page, 10), [page])
  const [selected, setSelected] = useState(null) // null = nothing, 'new' = create form, id = edit

  const refresh = () => list.reload()

  return (
    <section>
      <header className="page-head">
        <h1>Manage</h1>
        <p>Admin tools for products, stock, images and shoppers.</p>
      </header>

      <div className="manage">
        <div className="panel">
          <div className="panel-head">
            <h2>Products</h2>
            <button className="btn" onClick={() => setSelected('new')}>New product</button>
          </div>
          {list.error && <Problem error={list.error} compact />}
          {list.data && list.data.content.length === 0 && <p className="muted">No products yet.</p>}
          <ul className="plist">
            {(list.data?.content || []).map((p) => (
              <li key={p.id}>
                <button className={`plist-row ${selected === p.id ? 'is-active' : ''}`} onClick={() => setSelected(p.id)}>
                  <Thumb src={p.thumbnailUrl} alt={p.name} className="plist-img" />
                  <span className="plist-name">{p.name}</span>
                  <span className="muted">{money(p.price)}</span>
                  <span className="plist-stock">{p.stock ?? '—'}</span>
                </button>
              </li>
            ))}
          </ul>
          <Pager page={list.data?.page ?? page} totalPages={list.data?.totalPages} onChange={setPage} />
        </div>

        <div className="panel panel-wide">
          {selected === null && <p className="muted">Pick a product to edit, or create a new one.</p>}
          {selected === 'new' && (
            <ProductForm
              key="new"
              onSaved={(p) => {
                notify(`Created ${p.name}`)
                refresh()
                setSelected(p.id)
              }}
            />
          )}
          {selected !== null && selected !== 'new' && (
            <ProductEditor
              key={selected}
              id={selected}
              notify={notify}
              onChanged={refresh}
              onDeleted={() => {
                setSelected(null)
                refresh()
              }}
            />
          )}
        </div>
      </div>

      <Users users={users} onChanged={onUsersChanged} notify={notify} />
    </section>
  )
}

function ProductForm({ product, onSaved }) {
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
    const body = { name: form.name, description: form.description, price: form.price }
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
      <h2>{product ? 'Details' : 'New product'}</h2>
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
  if (!p) return <p className="muted">Loading…</p>

  return (
    <div className="editor">
      <ProductForm
        product={p}
        onSaved={(saved) => {
          notify(`Saved ${saved.name}`)
          reload()
          onChanged()
        }}
      />
      <Stock productId={id} notify={notify} onChanged={onChanged} />
      <Images product={p} notify={notify} onChanged={() => { reload(); onChanged() }} />
      <div className="danger">
        <button className="btn-danger" onClick={del}>Delete product</button>
      </div>
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

function Users({ users, onChanged, notify }) {
  const [form, setForm] = useState({ name: '', email: '' })
  const [error, setError] = useState(null)

  const create = async (e) => {
    e.preventDefault()
    setError(null)
    try {
      const u = await api.users.create(form)
      notify(`Created shopper ${u.name}`)
      setForm({ name: '', email: '' })
      onChanged(u.id)
    } catch (err) {
      setError(err)
    }
  }

  return (
    <div className="panel users">
      <h2>Shoppers</h2>
      <p className="muted">No login yet. The top bar picks a shopper and sends their ID in the X-User-Id header.</p>
      <ul className="user-list">
        {(users || []).map((u) => (
          <li key={u.id}><strong>{u.name}</strong> <span className="muted">{u.email}, id {u.id}</span></li>
        ))}
      </ul>
      <form className="user-form" onSubmit={create} noValidate>
        <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        <input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <button className="btn">Add shopper</button>
      </form>
      {error && <Problem error={error} compact />}
    </div>
  )
}
