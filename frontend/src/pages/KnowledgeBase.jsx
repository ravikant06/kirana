import { useCallback, useEffect, useRef, useState } from 'react'
import { api, uploadToStorage } from '../api.js'
import Problem from '../components/Problem.jsx'
import { AlertIcon, CheckIcon, ClockIcon, FileIcon, TrashIcon, UploadIcon } from '../components/icons.jsx'
import './KnowledgeBase.css'

// Manage → Knowledge base (AI Phase 2): the store's policy documents that the assistant answers from.
//
// An upload takes three hops, and this tab shows each one:
//   1. AI service: sign an upload policy, create the document row (pending)
//   2. browser → MinIO directly (the file never passes through the AI service)
//   3. MinIO → Kafka kb.documents.v1 → ingest worker → chunks in Qdrant (the status column)
// Until the worker exists (Phase 2 M3), uploads stop at "Waiting for the worker".

const MAX_BYTES = 10 * 1024 * 1024
const TYPES = { pdf: 'application/pdf', md: 'text/markdown', markdown: 'text/markdown', txt: 'text/plain' }
const DOC_TYPES = [['policy', 'Policy'], ['faq', 'FAQ'], ['guide', 'Guide']]
const UPLOAD_STEPS = ['Get a signed upload link', 'Upload to MinIO', 'MinIO publishes the event to Kafka']

const STATUS = {
  pending: { label: 'Waiting for the worker', cls: 'kb-wait', Icon: ClockIcon,
    hint: 'No upload event processed yet. MinIO has published it to Kafka; the ingest worker picks it up from there.' },
  uploaded: { label: 'Queued', cls: 'kb-wait', Icon: ClockIcon, hint: 'The upload event arrived; indexing starts next.' },
  indexing: { label: 'Indexing…', cls: 'kb-busy', Icon: null, hint: 'Parsing, chunking and embedding.' },
  ready: { label: 'Ready', cls: 'kb-ready', Icon: CheckIcon, hint: 'Searchable in chat.' },
  failed: { label: 'Failed', cls: 'kb-failed', Icon: AlertIcon, hint: '' },
  deleting: { label: 'Deleting…', cls: 'kb-busy', Icon: null, hint: 'File removed; waiting for the delete event to clear its chunks.' },
}

const kb = (n) => (n == null ? '—' : n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1048576).toFixed(1)} MB`)

function ago(iso) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 10) return 'just now'
  if (s < 60) return `${s} s ago`
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

function titleFrom(fileName) {
  const base = fileName.replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').trim()
  return base ? base[0].toUpperCase() + base.slice(1) : ''
}

export default function KnowledgeBase({ notify }) {
  const [docs, setDocs] = useState(null)
  const [loadError, setLoadError] = useState(null)

  const load = useCallback(async (quiet) => {
    try {
      setDocs(await api.ai.kb.list(quiet))
      setLoadError(null)
    } catch (e) {
      setLoadError(e)
    }
  }, [])

  // Statuses change on their own (events in Kafka), so refresh quietly while the tab is open.
  useEffect(() => {
    load(false)
    const t = setInterval(() => load(true), 3000)
    return () => clearInterval(t)
  }, [load])

  const counts = (docs || []).reduce((c, d) => {
    const k = d.status === 'ready' ? 'ready' : d.status === 'failed' ? 'failed' : 'busy'
    return { ...c, [k]: c[k] + 1 }
  }, { ready: 0, busy: 0, failed: 0 })

  return (
    <div className="kb">
      <Uploader notify={notify} onUploaded={() => load(false)} />

      <div className="card ptable-card">
        <div className="kb-table-head">
          <div>
            <h2>Documents</h2>
            <p className="muted">What the assistant can quote. Statuses update as events flow through Kafka.</p>
          </div>
          {docs && docs.length > 0 && (
            <div className="kb-counts">
              <span className="pill pill-ok">{counts.ready} ready</span>
              {counts.busy > 0 && <span className="pill kb-pill-wait">{counts.busy} in progress</span>}
              {counts.failed > 0 && <span className="pill kb-pill-failed">{counts.failed} failed</span>}
            </div>
          )}
        </div>
        {loadError && <div className="ptable-msg"><Problem error={loadError} compact /></div>}
        {docs && docs.length === 0 && (
          <div className="empty empty-center">
            <h2>No documents yet</h2>
            <p>Upload a policy, FAQ or guide above. Once it is indexed, the assistant answers from it and cites it.</p>
          </div>
        )}
        {docs && docs.length > 0 && (
          <table className="ptable kb-table">
            <thead>
              <tr><th>Document</th><th>Type</th><th>Status</th><th className="ptable-num">Pages</th><th className="ptable-num">Chunks</th><th>Updated</th><th /></tr>
            </thead>
            <tbody>
              {docs.map((d) => <DocRow key={d.id} d={d} notify={notify} onChanged={() => load(false)} />)}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function DocRow({ d, notify, onChanged }) {
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const s = STATUS[d.status] || { label: d.status, cls: '', hint: '' }

  const remove = async () => {
    setBusy(true)
    try {
      await api.ai.kb.remove(d.id)
      notify(`Deleting “${d.title}”`)
      onChanged()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
      setConfirming(false)
    }
  }

  return (
    <tr className="kb-row">
      <td data-label="Document">
        <div className="kb-doc">
          <span className="kb-file-icon"><FileIcon size={18} /></span>
          <div>
            <strong>{d.title}</strong>
            <span className="kb-file">{d.file_name} · {kb(d.size_bytes)}</span>
            {d.status === 'failed' && d.error && <span className="kb-error">{d.error}</span>}
          </div>
        </div>
      </td>
      <td data-label="Type"><span className="pill">{d.doc_type}</span></td>
      <td data-label="Status">
        <span className={`kb-status ${s.cls}`} title={s.hint || d.error || ''}>
          {s.Icon ? <s.Icon size={14} /> : <span className="kb-spinner" aria-hidden />}
          {s.label}
        </span>
      </td>
      <td data-label="Pages" className="ptable-num">{d.page_count ?? '—'}</td>
      <td data-label="Chunks" className="ptable-num">{d.chunk_count ?? '—'}</td>
      <td data-label="Updated" className="muted" title={new Date(d.updated_at).toLocaleString('en-IN')}>{ago(d.updated_at)}</td>
      <td className="ptable-act">
        {d.status === 'deleting' ? null : confirming ? (
          <span className="kb-confirm">
            <button className="btn-danger kb-btn-sm" disabled={busy} onClick={remove}>{busy ? 'Deleting…' : 'Delete'}</button>
            <button className="btn-quiet kb-btn-sm" onClick={() => setConfirming(false)}>Keep</button>
          </span>
        ) : (
          <button className="kb-icon-btn" onClick={() => setConfirming(true)} aria-label={`Delete ${d.title}`} title="Delete">
            <TrashIcon size={16} />
          </button>
        )}
      </td>
    </tr>
  )
}

function Uploader({ notify, onUploaded }) {
  const [file, setFile] = useState(null)
  const [title, setTitle] = useState('')
  const [docType, setDocType] = useState('policy')
  const [dragging, setDragging] = useState(false)
  const [step, setStep] = useState(-1) // -1 idle, 0..2 running, 3 done
  const [failedAt, setFailedAt] = useState(null)
  const [error, setError] = useState(null)
  const input = useRef(null)

  const ext = file ? file.name.split('.').pop().toLowerCase() : ''
  const contentType = TYPES[ext]
  const localProblem = !file ? null
    : !contentType ? 'Only PDF, Markdown (.md) and text (.txt) files can be indexed.'
      : file.size > MAX_BYTES ? `This file is ${kb(file.size)}; the limit is 10 MB.`
        : file.size === 0 ? 'This file is empty.' : null

  const pick = (f) => {
    if (!f) return
    setFile(f)
    setTitle(titleFrom(f.name))
    setStep(-1)
    setFailedAt(null)
    setError(null)
  }

  const upload = async (e) => {
    e.preventDefault()
    if (!file || localProblem || !title.trim()) return
    setError(null)
    setFailedAt(null)
    let current = 0
    try {
      setStep(0)
      const ticket = await api.ai.kb.requestUpload({
        title: title.trim(), doc_type: docType, file_name: file.name, content_type: contentType, size_bytes: file.size,
      })
      current = 1
      setStep(1)
      await uploadToStorage(ticket, file)
      // Nothing to confirm: MinIO's own event, already on its way to Kafka, is the confirmation.
      setStep(3)
      notify(`Uploaded “${title.trim()}”`)
      onUploaded()
      setFile(null)
      setTitle('')
    } catch (err) {
      setFailedAt(current)
      setError(err)
    }
  }

  const fieldErr = (f) => error?.problem?.errors?.find((x) => x.field === f)?.message
  const running = step >= 0 && step < 3 && failedAt === null

  return (
    <form className="card kb-upload" onSubmit={upload} noValidate>
      <div
        className={`kb-drop ${dragging ? 'is-dragging' : ''} ${file ? 'has-file' : ''}`}
        onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files?.[0]) }}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && input.current?.click()}
      >
        <input ref={input} type="file" accept=".pdf,.md,.markdown,.txt" hidden
               onChange={(e) => { pick(e.target.files?.[0]); e.target.value = '' }} />
        <span className="kb-drop-icon">{file ? <FileIcon size={26} /> : <UploadIcon size={26} />}</span>
        {file ? (
          <>
            <strong>{file.name}</strong>
            <span className="muted">{kb(file.size)} · click to choose another</span>
          </>
        ) : (
          <>
            <strong>Drop a document here, or click to browse</strong>
            <span className="muted">PDF, Markdown or text · up to 10 MB</span>
          </>
        )}
      </div>

      <div className="form kb-fields">
        <label>
          <span>Title</span>
          <input value={title} maxLength={120} placeholder="e.g. Returns Policy" onChange={(e) => setTitle(e.target.value)} />
          {fieldErr('title') && <em className="field-err">{fieldErr('title')}</em>}
        </label>
        <label>
          <span>Type</span>
          <select className="kb-select" value={docType} onChange={(e) => setDocType(e.target.value)}>
            {DOC_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </label>
        {localProblem && <em className="field-err">{localProblem}</em>}
        <button className="btn btn-add" disabled={!file || !!localProblem || !title.trim() || running}>
          <UploadIcon size={16} /> {running ? 'Uploading…' : 'Upload'}
        </button>
      </div>

      {step >= 0 && (
        <ol className="steps kb-steps">
          {UPLOAD_STEPS.map((label, i) => {
            const state = failedAt === i ? 'failed' : step > i ? 'done' : step === i && failedAt === null ? 'running' : 'waiting'
            return (
              <li key={label} className={`step step-${state}`}>
                <span className="step-dot" aria-hidden="true" />
                {label}
                {i === 2 && step === 3 && <span className="muted kb-step-note"> · watch its status below</span>}
              </li>
            )
          })}
        </ol>
      )}
      {error && !error.problem?.errors?.length && <div className="kb-upload-problem"><Problem error={error} compact /></div>}
    </form>
  )
}
