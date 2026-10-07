import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { api, streamChat } from '../../api.js'
import Problem from '../Problem.jsx'
import Markdown, { withoutSourcesLine } from './Markdown.jsx'
import ProductCards from './ProductCards.jsx'
import ApprovalCard from './ApprovalCard.jsx'
import { Back, Chevron, Close, Doc, Expand, History, Plus, Search, Send, Shrink, Sparkle, Trash } from './icons.jsx'
import './chat.css'

// The "Ask Kirana" assistant: a floating launcher and a chat panel, talking to
// kirana-ai through the /ai proxy. Threads belong to the shopper picked in the top
// bar (X-User-Id), so switching shopper switches conversations.
//
// Replies arrive whole, after the agent's 2–3 LLM calls: there is no streaming yet
// (Phase 3). The live seconds counter while waiting is there on purpose, so the
// latency that streaming fixes is something you can see.

const MAX_CHARS = 2000
const SUGGESTIONS = [
  'Can I return opened rice?',
  'How much is express delivery?',
  'When will my card refund arrive?',
  'How fresh is the paneer you deliver?',
]

const num = new Intl.NumberFormat('en-IN')
const threadKey = (userId) => `kirana.chat.thread.${userId}`

function savedThread(userId) {
  try {
    return localStorage.getItem(threadKey(userId))
  } catch {
    return null
  }
}

function saveThread(userId, id) {
  try {
    if (id) localStorage.setItem(threadKey(userId), id)
    else localStorage.removeItem(threadKey(userId))
  } catch {
    /* storage unavailable: the thread just is not remembered */
  }
}

function ago(iso) {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.floor(s / 60)} min ago`
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`
  return new Date(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

// API message -> what the UI renders.
// Product cards of a saved answer come from its steps: search_products recorded the ids it showed.
const productIdsOf = (steps) => [...new Set((steps || []).flatMap((s) => s.product_ids || []))]
// Phase 6: actions waiting for the shopper, recorded on the step that proposed them.
const approvalsOf = (steps) => (steps || []).filter((s) => s.approval).map((s) => s.approval)
const fromApi = (m) => ({ id: m.id, role: m.role, content: m.content, citations: m.citations || [],
  steps: m.steps || [], productIds: productIdsOf(m.steps) })

export default function ChatDock({ userId, userName, inspectorOpen, onOpenProduct, onCartChanged, notify }) {
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [view, setView] = useState('chat') // 'chat' | 'threads'
  const [threadId, setThreadId] = useState(null)
  const [messages, setMessages] = useState([])
  const [pending, setPending] = useState(null) // { startedAt } while a turn runs
  const [failure, setFailure] = useState(null) // { error, text } for the last failed turn
  const [threads, setThreads] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [draft, setDraft] = useState('')
  const scroller = useRef(null)
  const input = useRef(null)
  const abort = useRef(null)   // the running stream's AbortController (Stop button)
  // Bumped on every shopper switch. A response that comes back after the switch
  // belongs to the previous shopper, so it is dropped instead of shown.
  const epoch = useRef(0)

  // --- a shopper's conversation: restore their last thread when they change -------------
  // The remembered thread id is written only here and after a send, never by an effect
  // watching threadId: on a shopper switch that effect would run with the new shopper
  // and the old thread, and file one shopper's thread under the other.
  const openThread = useCallback(async (id) => {
    setFailure(null)
    setLoadError(null)
    setView('chat')
    if (!id) {
      saveThread(userId, null)
      setThreadId(null)
      setMessages([])
      return
    }
    const mine = epoch.current
    try {
      const t = await api.ai.thread(userId, id)
      if (mine !== epoch.current) return
      saveThread(userId, t.id)
      setThreadId(t.id)
      setMessages(t.messages.map(fromApi))
    } catch (e) {
      if (mine !== epoch.current) return
      // Deleted, or it belonged to another shopper: start fresh rather than show an error.
      if (e.status === 404) {
        saveThread(userId, null)
        setThreadId(null)
        setMessages([])
      } else setLoadError(e)
    }
  }, [userId])

  useEffect(() => {
    epoch.current += 1
    setThreads(null)
    setPending(null)
    setFailure(null)
    if (userId) openThread(savedThread(userId))
    else {
      setThreadId(null)
      setMessages([])
    }
  }, [userId, openThread])

  // --- sending --------------------------------------------------------------------------
  const send = useCallback(async (raw) => {
    const text = raw.trim()
    if (!text || pending || !userId) return
    setFailure(null)
    setDraft('')
    if (input.current) input.current.style.height = 'auto'   // shrink back after a long draft
    setMessages((ms) => [...ms, { id: `local-${Date.now()}`, role: 'user', content: text, local: true }])
    setPending({ startedAt: performance.now(), status: null, text: '', productIds: [] })
    const mine = epoch.current
    const citations = []   // arrive as their own events, just before `done`
    let partial = ''       // the answer so far, kept if the shopper presses Stop
    // Events from the stream update the live answer; every update checks the shopper hasn't changed.
    const onEvent = (kind, data) => {
      if (mine !== epoch.current) return
      if (kind === 'start') {
        saveThread(userId, data.thread_id)   // explicit, never from an effect: see openThread
        setThreadId(data.thread_id)
      } else if (kind === 'status') {
        setPending((p) => p && { ...p, status: data })
      } else if (kind === 'token') {
        partial += data.text
        setPending((p) => p && { ...p, text: p.text + data.text })
      } else if (kind === 'products') {
        // Ids only: the cards load price, stock and photo from Kirana themselves.
        setPending((p) => p && { ...p, productIds: [...new Set([...p.productIds, ...data.product_ids])] })
      } else if (kind === 'citation') {
        citations.push(data)
      } else if (kind === 'reset') {
        partial = ''
        setPending((p) => p && { ...p, text: '' })   // that text was a preamble to tool calls
      }
    }
    abort.current = new AbortController()
    try {
      const r = await streamChat(userId, text, threadId, onEvent, abort.current.signal)
      if (mine !== epoch.current) return
      setMessages((ms) => [...ms, {
        id: r.message_id, role: 'assistant', content: r.reply,
        citations, steps: r.steps, usage: r.usage, fresh: true, productIds: productIdsOf(r.steps),
      }])
      setThreads(null) // the list's order and titles changed
    } catch (error) {
      if (mine !== epoch.current) return
      if (error.problem?.stopped) {
        // Shown, not saved: the server never stores a half answer.
        setMessages((ms) => [...ms, { id: `stopped-${Date.now()}`, role: 'assistant', content: partial, stopped: true }])
      } else {
        setFailure({ error, text })
      }
    } finally {
      abort.current = null
      if (mine === epoch.current) setPending(null)
    }
  }, [pending, userId, threadId])

  const retry = () => {
    if (!failure) return
    // Drop the failed question's bubble; send() adds it again.
    setMessages((ms) => {
      const i = ms.map((m) => m.role).lastIndexOf('user')
      return i === -1 ? ms : ms.filter((_, j) => j !== i)
    })
    send(failure.text)
  }

  // --- threads drawer -------------------------------------------------------------------
  const showThreads = async () => {
    setView('threads')
    try {
      setThreads(await api.ai.threads(userId))
    } catch (e) {
      setThreads([])
      setLoadError(e)
    }
  }

  const removeThread = async (id) => {
    try {
      await api.ai.deleteThread(userId, id)
      setThreads((ts) => (ts || []).filter((t) => t.id !== id))
      if (id === threadId) openThread(null)
    } catch (e) {
      setLoadError(e)
    }
  }

  // --- behaviour ------------------------------------------------------------------------
  useLayoutEffect(() => {
    const el = scroller.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [messages, pending, failure, view])

  useEffect(() => {
    if (open && view === 'chat') input.current?.focus()
  }, [open, view, threadId])

  useEffect(() => {
    if (!open) return
    const onKey = (e) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const onComposerKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send(draft)
    }
  }

  const cardProps = { onOpen: (id) => { setExpanded(false); onOpenProduct?.(id) }, onCartChanged, notify }
  // The outcome of a confirmed action is written by the server from Kirana's answer; show it here too.
  // Stable across renders: the card's "still executing" poll depends on it.
  const onDecided = useCallback((v) => {
    if (v.message && v.status !== 'pending') {
      const id = `action-${v.id}-${v.status}`
      setMessages((ms) => (ms.some((m) => m.id === id) ? ms
        : [...ms, { id, role: 'assistant', content: v.message, fresh: true }]))
    }
    if (v.status === 'done') onCartChanged?.()
  }, [onCartChanged])
  const firstName = (userName || '').split(' ')[0]
  const empty = messages.length === 0 && !pending && !failure

  return (
    <div className={`chat-dock ${inspectorOpen ? 'beside-inspector' : ''}`}>
      {!open && (
        <button className="chat-launcher" onClick={() => setOpen(true)} aria-label="Open the Kirana assistant">
          <span className="chat-launcher-icon"><Sparkle width={18} height={18} /></span>
          <span>Ask Kirana</span>
        </button>
      )}

      {open && (
        <section className={`chat-panel ${expanded ? 'is-expanded' : ''}`} aria-label="Kirana assistant">
          <header className="chat-head">
            {view === 'threads' ? (
              <button className="chat-icon-btn" onClick={() => setView('chat')} aria-label="Back to chat"><Back /></button>
            ) : (
              <span className="chat-avatar" aria-hidden><Sparkle width={18} height={18} /></span>
            )}
            <div className="chat-head-text">
              <h2>{view === 'threads' ? 'Your conversations' : 'Kirana Assistant'}</h2>
              <p>
                {view === 'threads'
                  ? userName ? `Chats of ${userName}` : 'Pick a shopper first'
                  : <><span className="chat-online" /> Answers with sources</>}
              </p>
            </div>
            <div className="chat-head-actions">
              {view === 'chat' && userId && (
                <>
                  <button className="chat-icon-btn" onClick={showThreads} aria-label="Past conversations" title="Past conversations"><History /></button>
                  <button className="chat-icon-btn" onClick={() => openThread(null)} aria-label="New chat" title="New chat"><Plus /></button>
                </>
              )}
              <button className="chat-icon-btn hide-mobile" onClick={() => setExpanded((x) => !x)}
                      aria-label={expanded ? 'Shrink' : 'Expand'} title={expanded ? 'Shrink' : 'Expand'}>
                {expanded ? <Shrink /> : <Expand />}
              </button>
              <button className="chat-icon-btn" onClick={() => setOpen(false)} aria-label="Close" title="Close (Esc)"><Close /></button>
            </div>
          </header>

          <div className="chat-body" ref={scroller}>
            {loadError && <div className="chat-inline-problem"><Problem error={loadError} compact /></div>}

            {!userId ? (
              <div className="chat-welcome">
                <span className="chat-welcome-mark"><Sparkle width={28} height={28} /></span>
                <h3>Who's shopping?</h3>
                <p>Pick a shopper under <strong>Shopping as</strong> in the top bar. Conversations are saved per shopper.</p>
              </div>
            ) : view === 'threads' ? (
              <ThreadList threads={threads} current={threadId} onOpen={openThread} onDelete={removeThread}
                          onNew={() => openThread(null)} />
            ) : empty ? (
              <div className="chat-welcome">
                <span className="chat-welcome-mark"><Sparkle width={28} height={28} /></span>
                <h3>{firstName ? `Hi ${firstName}, how can I help?` : 'How can I help?'}</h3>
                <p>Ask about returns, refunds, cancellations, delivery or freshness. Every answer comes from Kirana's own policies, with its sources.</p>
                <div className="chat-suggestions">
                  {SUGGESTIONS.map((s) => (
                    <button key={s} className="chat-suggestion" onClick={() => send(s)}>
                      <span>{s}</span><Chevron width={16} height={16} />
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <ol className="chat-log" aria-live="polite">
                {messages.map((m) => (m.role === 'user'
                  ? <li key={m.id} className="msg msg-user"><div className="bubble">{m.content}</div></li>
                  : <AssistantMessage key={m.id} m={m} cards={cardProps} onDecided={onDecided} />))}
                {pending && (pending.text
                  ? <LiveAnswer text={pending.text} productIds={pending.productIds} cards={cardProps} />
                  : <Thinking startedAt={pending.startedAt} status={pending.status} productIds={pending.productIds} cards={cardProps} />)}
                {failure && (
                  <li className="msg msg-assistant">
                    <div className="msg-failure">
                      <Problem error={failure.error} compact />
                      <button className="chat-retry" onClick={retry}>Try again</button>
                    </div>
                  </li>
                )}
              </ol>
            )}
          </div>

          {view === 'chat' && userId && (
            <form className="chat-composer" onSubmit={(e) => { e.preventDefault(); send(draft) }}>
              <div className="chat-input-wrap">
                <textarea
                  ref={input}
                  rows={1}
                  value={draft}
                  maxLength={MAX_CHARS}
                  placeholder={pending ? 'Waiting for the answer…' : 'Ask about returns, delivery, refunds…'}
                  onChange={(e) => {
                    setDraft(e.target.value)
                    e.target.style.height = 'auto'
                    e.target.style.height = `${Math.min(e.target.scrollHeight, 140)}px`
                  }}
                  onKeyDown={onComposerKey}
                  aria-label="Message"
                />
                {pending ? (
                  <button className="chat-send is-stop" type="button" onClick={() => abort.current?.abort()}
                          aria-label="Stop the answer" title="Stop">
                    <span className="stop-square" aria-hidden />
                  </button>
                ) : (
                  <button className="chat-send" type="submit" disabled={!draft.trim()} aria-label="Send">
                    <Send width={18} height={18} />
                  </button>
                )}
              </div>
              <div className="chat-foot">
                <span>Enter to send · Shift+Enter for a new line</span>
                {draft.length > MAX_CHARS * 0.8 && (
                  <span className={draft.length >= MAX_CHARS ? 'is-over' : ''}>{num.format(draft.length)} / {num.format(MAX_CHARS)}</span>
                )}
              </div>
            </form>
          )}
        </section>
      )}
    </div>
  )
}

function AssistantMessage({ m, cards, onDecided }) {
  const [showSteps, setShowSteps] = useState(false)
  const u = m.usage
  return (
    <li className={`msg msg-assistant ${m.fresh ? 'is-fresh' : ''}`}>
      <span className="msg-avatar" aria-hidden><Sparkle width={14} height={14} /></span>
      <div className="msg-card">
        {m.content ? <Markdown text={withoutSourcesLine(m.content)} /> : <p className="msg-stopped-empty">No answer yet.</p>}
        {m.productIds?.length > 0 && <ProductCards ids={m.productIds} {...cards} />}
        {approvalsOf(m.steps).map((a) => (
          <ApprovalCard key={a.approval_id} approval={a} onDecided={onDecided} notify={cards.notify} />
        ))}
        {m.stopped && <div className="msg-stopped">Stopped · not saved</div>}

        {m.citations?.length > 0 && (
          <div className="msg-sources">
            <span className="msg-label">Sources</span>
            {m.citations.map((c) => (
              <span key={c.source} className="source-chip" title={c.source}>
                <Doc width={14} height={14} />
                {c.title || c.source}
                {c.pages?.length > 0 && <span className="source-pages">p. {c.pages.join(', ')}</span>}
              </span>
            ))}
          </div>
        )}

        {(m.steps?.length > 0 || u) && (
          <div className="msg-meta">
            {m.steps?.length > 0 && (
              <button className="msg-steps-toggle" onClick={() => setShowSteps((s) => !s)} aria-expanded={showSteps}>
                <Chevron width={14} height={14} className={showSteps ? 'is-open' : ''} />
                How I answered
              </button>
            )}
            {u && (
              <span className="msg-usage" title="LLM calls · tokens in+out (thinking included) · LLM time · cost">
                {u.llm_calls} {u.llm_calls === 1 ? 'call' : 'calls'} · {num.format(u.input_tokens + u.output_tokens)} tok
                {u.first_token_ms != null && ` · first token ${(u.first_token_ms / 1000).toFixed(1)} s`}
                {` · ${(u.latency_ms / 1000).toFixed(1)} s`}{u.cost_usd ? ` · $${Number(u.cost_usd).toFixed(4)}` : ''}
              </span>
            )}
          </div>
        )}

        {showSteps && (
          <ol className="msg-steps">
            {m.steps.map((s, i) => (
              <li key={i}>
                <Search width={14} height={14} />
                <div>
                  <code>{s.tool}</code>
                  {s.query && <span className="step-query"> “{s.query}”</span>}
                  <div className="step-sub">
                    {Object.keys(s.where || {}).length
                      ? Object.entries(s.where).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')
                      : 'no filters'} · {s.count} {s.count === 1 ? 'result' : 'results'}
                  </div>
                  {s.decision && (
                    <div className={`step-policy step-policy-${s.decision}`}>
                      policy: {s.decision}{s.reason ? ` (${s.reason})` : ''}{s.denied_by === 'kirana' ? ' · refused by Kirana' : ''}
                    </div>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
    </li>
  )
}

// The answer as it streams in: same card as a finished answer, plus a blinking caret.
function LiveAnswer({ text, productIds, cards }) {
  return (
    <li className="msg msg-assistant">
      <span className="msg-avatar is-busy" aria-hidden><Sparkle width={14} height={14} /></span>
      <div className="msg-card is-streaming">
        <Markdown text={withoutSourcesLine(text)} />
        <span className="stream-caret" aria-hidden />
        {productIds?.length > 0 && <ProductCards ids={productIds} {...cards} />}
      </div>
    </li>
  )
}

function searchTarget(status) {
  if (status.tool === 'search_products') {
    const parts = [status.where?.category, status.where?.max_price && `under ₹${status.where.max_price}`].filter(Boolean)
    return `products${parts.length ? ` (${parts.join(', ')})` : ''}`
  }
  if (status.tool === 'get_my_orders' || status.tool === 'get_order') return 'your orders'
  return status.where?.doc_type ? `${status.where.doc_type} documents` : 'store policies'
}

function Thinking({ startedAt, status, productIds, cards }) {
  const [now, setNow] = useState(performance.now())
  useEffect(() => {
    const t = setInterval(() => setNow(performance.now()), 100)
    return () => clearInterval(t)
  }, [])
  return (
    <li className="msg msg-assistant">
      <span className="msg-avatar is-busy" aria-hidden><Sparkle width={14} height={14} /></span>
      <div className="msg-stack">
        <div className="msg-card msg-thinking" role="status">
          <span className="dots" aria-hidden><i /><i /><i /></span>
          <span className="thinking-text">
            {status
              ? <>Searching {searchTarget(status)}{status.query ? <>: <em>“{status.query}”</em></> : '…'}</>
              : 'Thinking…'}
          </span>
          <span className="thinking-clock">{((now - startedAt) / 1000).toFixed(1)} s</span>
        </div>
        {/* products found before the answer text starts: show them right away */}
        {productIds?.length > 0 && <div className="msg-card"><ProductCards ids={productIds} {...cards} /></div>}
      </div>
    </li>
  )
}

function ThreadList({ threads, current, onOpen, onDelete, onNew }) {
  if (threads === null) return <div className="chat-threads-loading"><span className="dots"><i /><i /><i /></span></div>
  return (
    <div className="chat-threads">
      <button className="chat-new-thread" onClick={onNew}><Plus width={16} height={16} /> New conversation</button>
      {threads.length === 0 ? (
        <p className="chat-threads-empty">No conversations yet.</p>
      ) : (
        <ul>
          {threads.map((t) => (
            <li key={t.id} className={t.id === current ? 'is-current' : ''}>
              <button className="thread-open" onClick={() => onOpen(t.id)}>
                <span className="thread-title">{t.title}</span>
                <span className="thread-when">{ago(t.updated_at)}</span>
              </button>
              <button className="chat-icon-btn thread-delete" onClick={() => onDelete(t.id)} aria-label={`Delete “${t.title}”`}>
                <Trash width={16} height={16} />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
