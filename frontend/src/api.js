// Thin fetch wrapper. Every call is recorded so the Requests panel can show
// exactly what the backend received and returned. No caching, and no retries except one kind:
// Stage 7, a request with an Idempotency-Key whose outcome is unknown (no response, 409 "in
// progress", 502/503/504) is retried with the SAME key, like a payment SDK does. Every attempt
// still shows up in the panel.

let log = []
const listeners = new Set()
let currentUserId = null

export function setUserId(id) {
  currentUserId = id
}

export function subscribe(fn) {
  listeners.add(fn)
  fn(log)
  return () => listeners.delete(fn)
}

export function clearLog() {
  log = []
  listeners.forEach((fn) => fn(log))
}

function record(entry) {
  log = [entry, ...log].slice(0, 100)
  listeners.forEach((fn) => fn(log))
}

export class ApiError extends Error {
  constructor(status, problem, retryAfter = null) {
    super(problem?.detail || problem?.title || `Request failed with status ${status}`)
    this.status = status
    this.problem = problem
    this.retryAfter = retryAfter
  }
}

// Stage 7: one key per user action (a tap of "Add", one checkout attempt), reused on its retries.
export const newKey = () => crypto.randomUUID()

const MAX_RETRIES = 2
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// Retry only when we can't know whether the server acted. A definite answer (200, 400, 409
// "Cart is empty", ...) is never retried.
function unknownOutcome(status, problem) {
  return status === 0 || status === 502 || status === 503 || status === 504
    || (status === 409 && problem?.title === 'Request in progress')
}

// Retry-After when the server sent one (capped at 5 s), else 0.4 s, 0.8 s ... plus jitter.
function backoff(attempt, retryAfter) {
  if (retryAfter != null) return Math.min(5, retryAfter) * 1000
  return 400 * 2 ** (attempt - 1) + Math.random() * 200
}

async function request(method, path, body, opts = {}) {
  const attempts = opts.idempotencyKey ? 1 + MAX_RETRIES : 1
  for (let attempt = 1; ; attempt++) {
    const last = attempt >= attempts
    if (opts.meta) {
      const r = await requestOnce(method, path, body, { ...opts, attempt })
      if (r.ok || last || !unknownOutcome(r.status, r.data)) return r
      await sleep(backoff(attempt, r.retryAfter))
      continue
    }
    try {
      return await requestOnce(method, path, body, { ...opts, attempt })
    } catch (e) {
      if (last || !(e instanceof ApiError) || !unknownOutcome(e.status, e.problem)) throw e
      await sleep(backoff(attempt, e.retryAfter))
    }
  }
}

// opts.userId: act as a different shopper for this call (the rush simulator uses it).
// opts.quiet:  leave it out of the Requests panel (background polling).
// opts.meta:   never throw; return { ok, status, data, queries, ms } instead.
// opts.service: 'ai' sends it to the AI service (/ai → kirana-ai on :8000) instead of Spring Boot.
// opts.idempotencyKey: sent as Idempotency-Key (Stage 7); enables the retries above.
async function requestOnce(method, path, body, opts = {}) {
  const ai = opts.service === 'ai'
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const userId = opts.userId ?? currentUserId
  if (userId) headers['X-User-Id'] = String(userId)
  if (opts.idempotencyKey) headers['Idempotency-Key'] = opts.idempotencyKey
  const log = opts.quiet ? () => {} : record

  const entry = {
    id: crypto.randomUUID(),
    method,
    path: ai ? '/ai' + path : path,
    service: ai ? 'ai' : 'backend',
    userId,
    requestBody: body,
    idempotencyKey: opts.idempotencyKey,
    attempt: opts.attempt ?? 1,
    at: new Date(),
  }
  const started = performance.now()

  let res
  try {
    res = await fetch((ai ? '/ai' : '/api') + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    const problem = ai
      ? { title: 'AI service not reachable', detail: 'The request never got a response. Is kirana-ai running on port 8000 (uvicorn kirana_ai.api:app --port 8000)?' }
      : { title: 'Backend not reachable', detail: 'The request never got a response. Is Spring Boot running on port 8080?' }
    log({ ...entry, status: 0, ms: Math.round(performance.now() - started), responseBody: problem })
    if (opts.meta) return { ok: false, status: 0, data: problem, queries: null, ms: 0 }
    throw new ApiError(0, problem)
  }

  const text = await res.text()
  let data = null
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      data = text
    }
  }
  // Set by the backend's QueryMetricsFilter (Stage 2): SQL statements run and time spent in the DB.
  const queries = res.headers.get('X-Query-Count')
  const dbMs = res.headers.get('X-DB-Time-Ms')
  // Stage 4: HIT (answered by Redis), MISS (went to Postgres), BYPASS (Redis unavailable).
  const cache = res.headers.get('X-Cache')
  // AI service: what the turn cost. Cost is absent when the model has no price in pricing.yaml.
  const aiUsage = res.headers.get('X-AI-LLM-Calls') === null ? null : {
    calls: Number(res.headers.get('X-AI-LLM-Calls')),
    inputTokens: Number(res.headers.get('X-AI-Input-Tokens')),
    outputTokens: Number(res.headers.get('X-AI-Output-Tokens')),
    cost: res.headers.get('X-AI-Cost-USD'),
  }
  const ms = Math.round(performance.now() - started)
  const retryAfterHeader = res.headers.get('Retry-After')
  const retryAfter = retryAfterHeader === null ? null : Number(retryAfterHeader)
  log({
    ...entry,
    status: res.status,
    replayed: res.headers.get('Idempotent-Replayed') === 'true',
    ms,
    queries: queries === null ? null : Number(queries),
    dbMs: dbMs === null ? null : Number(dbMs),
    cache,
    aiUsage,
    responseBody: data,
  })

  if (opts.meta) {
    return { ok: res.ok, status: res.status, data, queries: queries === null ? null : Number(queries), ms, retryAfter }
  }
  if (!res.ok) {
    if (!text && res.status >= 500) {
      throw new ApiError(res.status, {
        status: res.status,
        title: ai ? 'AI service not reachable' : 'Empty error response',
        detail: ai
          ? `Got ${res.status} with no body: the dev proxy answers this way when kirana-ai is not running on port 8000.`
          : `Got ${res.status} with no body. If the backend is down, the dev proxy answers this way.`,
      })
    }
    const problem = data && typeof data === 'object' ? data : { status: res.status, detail: String(data || res.statusText) }
    throw new ApiError(res.status, problem, retryAfter)
  }
  return data
}

// Direct browser → MinIO upload using a presigned POST policy from the backend.
// The backend signs a policy (size range, content type, exact key); MinIO enforces it.
// This request never touches Spring Boot, which is the whole point.
export async function uploadToStorage(ticket, file) {
  const u = new URL(ticket.uploadUrl)
  const form = new FormData()
  // Policy fields first (key, Content-Type, policy, x-amz-*). S3 requires the file field LAST.
  Object.entries(ticket.formFields || {}).forEach(([k, v]) => form.append(k, v))
  form.append('file', file)

  const entry = {
    id: crypto.randomUUID(),
    method: 'POST',
    path: `${u.host}${u.pathname}`,
    direct: true,
    requestBody: {
      formFields: ticket.formFields,
      file: `<binary ${file.type || 'unknown type'}, ${file.size} bytes>`,
    },
    at: new Date(),
  }
  const started = performance.now()
  let res
  try {
    // No Content-Type header here: the browser sets multipart/form-data with the boundary.
    res = await fetch(ticket.uploadUrl, { method: 'POST', body: form })
  } catch {
    const problem = {
      title: 'Upload to storage failed',
      detail:
        'The browser could not complete the POST to MinIO. Usual causes: MinIO not running, CORS blocked by the storage server, or an upload URL whose host the browser cannot resolve.',
    }
    record({ ...entry, status: 0, ms: Math.round(performance.now() - started), responseBody: problem })
    throw new ApiError(0, problem)
  }
  const text = await res.text()
  record({ ...entry, status: res.status, ms: Math.round(performance.now() - started), responseBody: text || null })
  if (!res.ok) {
    let detail = text.slice(0, 300) || 'No details returned.'
    if (text.includes('EntityTooLarge')) detail = 'EntityTooLarge: the file is bigger than the size range in the signed policy. MinIO enforced your limit.'
    else if (text.includes('EntityTooSmall')) detail = 'EntityTooSmall: the file is smaller than the minimum in the signed policy.'
    else if (text.includes('AccessDenied') || text.includes('Policy')) detail = 'Policy check failed: a form field (key, Content-Type, expiry) does not satisfy the signed policy.'
    else if (text.includes('SignatureDoesNotMatch')) detail = 'SignatureDoesNotMatch: the policy or credentials used to sign it do not match what MinIO expects.'
    throw new ApiError(res.status, { status: res.status, title: 'Storage rejected the upload', detail })
  }
}

const q = encodeURIComponent

export const api = {
  users: {
    // Bounded search: newest shoppers first, filtered by name or email when q is given.
    list: (q = '', limit = 20) => request('GET', `/users?limit=${limit}${q ? `&q=${q && encodeURIComponent(q)}` : ''}`),
    create: (body) => request('POST', '/users', body),
  },
  products: {
    list: (page = 0, size = 12) => request('GET', `/products?page=${page}&size=${size}`),
    get: (id) => request('GET', `/products/${q(id)}`),
    // AI Phase 4: live price and stock for the products the assistant found (at most 50 ids).
    batch: (ids) => request('GET', `/products/batch?ids=${ids.map(Number).join(',')}`),
    create: (body) => request('POST', '/products', body),
    update: (id, body) => request('PUT', `/products/${q(id)}`, body),
    remove: (id) => request('DELETE', `/products/${q(id)}`),
  },
  images: {
    requestUpload: (productId, body) => request('POST', `/products/${q(productId)}/images/upload-url`, body),
    confirm: (productId, imageId) => request('POST', `/products/${q(productId)}/images/${q(imageId)}/confirm`),
    remove: (productId, imageId) => request('DELETE', `/products/${q(productId)}/images/${q(imageId)}`),
  },
  inventory: {
    get: (productId) => request('GET', `/products/${q(productId)}/inventory`),
    set: (productId, quantity) => request('PUT', `/products/${q(productId)}/inventory`, { quantity }),
    adjust: (productId, delta) => request('POST', `/products/${q(productId)}/inventory/adjustments`, { delta }),
  },
  cart: {
    get: () => request('GET', '/cart'),
    add: (productId, quantity, key = newKey()) =>
      request('POST', '/cart/items', { productId, quantity }, { idempotencyKey: key }),
    update: (productId, quantity) => request('PUT', `/cart/items/${q(productId)}`, { quantity }),
    remove: (productId) => request('DELETE', `/cart/items/${q(productId)}`),
  },
  flashSale: {
    active: () => request('GET', '/flash-sales', undefined, { quiet: true }),
    status: (productId, quiet = false) => request('GET', `/products/${q(productId)}/flash-sale`, undefined, { quiet }),
    start: (productId) => request('POST', `/products/${q(productId)}/flash-sale`),
    stop: (productId) => request('DELETE', `/products/${q(productId)}/flash-sale`),
  },
  // The rush simulator: set up throwaway shoppers quietly, then check out as each of them.
  rush: {
    createShopper: (name, email) => request('POST', '/users', { name, email }, { quiet: true }),
    addToCart: (userId, productId) =>
      request('POST', '/cart/items', { productId, quantity: 1 }, { userId, quiet: true, idempotencyKey: newKey() }),
    checkout: (userId) => request('POST', '/orders', undefined, { userId, meta: true, idempotencyKey: newKey() }),
  },
  // Stage 5 Resilience lab
  system: {
    status: () => request('GET', '/system/status', undefined, { quiet: true }),
    resetBreaker: (name) => request('POST', `/system/breakers/${q(name)}/reset`),
    paymentFault: (body) => request('POST', '/system/chaos/payment', body),
    networkFault: (proxy, body) => request('POST', `/system/chaos/network/${q(proxy)}`, body),
    redrive: (topic) => request('POST', `/system/kafka/dead-letters/${q(topic)}/redrive`),
  },
  payments: {
    providers: () => request('GET', '/payments/providers'),
  },
  orders: {
    // Stage 5: returns { order, payment, paymentProblem }. payment opens the gateway checkout.
    // Stage 7: pass the same key again to retry the same action (a new key = a new action).
    place: (paymentProvider, key = newKey()) =>
      request('POST', '/orders', paymentProvider ? { paymentProvider } : undefined, { idempotencyKey: key }),
    pay: (id, key = newKey()) => request('POST', `/orders/${q(id)}/payment`, undefined, { idempotencyKey: key }),
    verify: (id, body) => request('POST', `/orders/${q(id)}/payment/verify`, body),
    cancel: (id, key = newKey()) => request('POST', `/orders/${q(id)}/cancel`, undefined, { idempotencyKey: key }),
    list: () => request('GET', '/orders'),
    get: (id) => request('GET', `/orders/${q(id)}`),
  },
  // The AI assistant (kirana-ai). Same X-User-Id, same ProblemDetail errors, different service.
  ai: {
    // userId is passed explicitly: the dock knows whose chat it shows, and a hot reload of this
    // module resets currentUserId to null while the dock still holds a shopper.
    chat: (userId, message, threadId) =>
      request('POST', '/v1/chat', threadId ? { message, thread_id: threadId } : { message }, { service: 'ai', userId }),
    threads: (userId) => request('GET', '/v1/threads', undefined, { service: 'ai', userId }),
    thread: (userId, id) => request('GET', `/v1/threads/${q(id)}`, undefined, { service: 'ai', userId }),
    deleteThread: (userId, id) => request('DELETE', `/v1/threads/${q(id)}`, undefined, { service: 'ai', userId }),
    // Knowledge base (AI Phase 2). The upload itself goes browser -> MinIO with uploadToStorage.
    kb: {
      list: (quiet = false) => request('GET', '/v1/kb/documents', undefined, { service: 'ai', quiet }),
      requestUpload: async (body) => {
        const t = await request('POST', '/v1/kb/documents/upload-url', body, { service: 'ai' })
        // uploadToStorage expects Kirana's image-ticket shape.
        return { ...t, uploadUrl: t.upload_url, formFields: t.form_fields }
      },
      remove: (id) => request('DELETE', `/v1/kb/documents/${q(id)}`, undefined, { service: 'ai' }),
    },
  },
}

// AI chat as Server-Sent Events (AI Phase 3). EventSource can't send a POST body, so this reads
// the response stream itself and splits it into "event: x / data: {...}" frames. onEvent gets each
// one as it arrives. Resolves with the `done` data; an `error` event (a ProblemDetail sent after
// the 200, because the stream had already started) rejects with an ApiError like any request.
export async function streamChat(userId, message, threadId, onEvent, signal) {
  const body = threadId ? { message, thread_id: threadId } : { message }
  const entry = { id: crypto.randomUUID(), method: 'POST', path: '/ai/v1/chat', service: 'ai', userId,
    requestBody: body, at: new Date(), streamed: true }
  const started = performance.now()
  // Every SSE event with its arrival time: the Requests panel shows this as a timeline.
  const events = []
  const finish = (status, responseBody, aiUsage = null) =>
    record({ ...entry, status, ms: Math.round(performance.now() - started), responseBody, aiUsage, events })
  const stopped = () => {
    const problem = { title: 'Stopped', detail: 'You stopped the answer. Nothing was saved; the server recorded the call in flight as abandoned.' }
    finish(499, problem)   // 499: nginx's "client closed request"
    return new ApiError(499, { ...problem, stopped: true })
  }

  let res
  try {
    res = await fetch('/ai/v1/chat', {
      method: 'POST',
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json', 'X-User-Id': String(userId) },
      body: JSON.stringify(body),
      signal,
    })
  } catch (e) {
    if (e.name === 'AbortError') throw stopped()
    const problem = { title: 'AI service not reachable', detail: 'Is kirana-ai running on port 8000?' }
    finish(0, problem)
    throw new ApiError(0, problem)
  }
  if (!res.ok) {
    // Rejected before streaming started (a foreign thread, a bad request): a normal ProblemDetail.
    const text = await res.text()
    let problem
    try { problem = JSON.parse(text) } catch { problem = { status: res.status, detail: text || res.statusText } }
    finish(res.status, problem)
    throw new ApiError(res.status, problem)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let done = null
  let failed = null
  for (;;) {
    let chunk
    try {
      chunk = await reader.read()
    } catch (e) {
      // Aborting the fetch closes the connection: the server sees the hang-up and closes the turn.
      if (e.name === 'AbortError') throw stopped()
      throw e
    }
    const { value, done: ended } = chunk
    if (ended) break
    buffer += decoder.decode(value, { stream: true })
    let cut
    while ((cut = buffer.indexOf('\n\n')) !== -1) {
      const frame = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      const fields = Object.fromEntries(frame.split('\n').map((l) => [l.slice(0, l.indexOf(':')), l.slice(l.indexOf(':') + 2)]))
      const data = fields.data ? JSON.parse(fields.data) : {}
      if (fields.event === 'done') done = data
      if (fields.event === 'error') failed = data
      events.push({ t: Math.round(performance.now() - started), event: fields.event, data })
      onEvent(fields.event, data)
    }
  }
  const u = done?.usage
  finish(200, done || failed, u ? { calls: u.llm_calls, inputTokens: u.input_tokens, outputTokens: u.output_tokens,
    cost: u.cost_usd, firstTokenMs: u.first_token_ms } : null)
  if (failed || !done) throw new ApiError(failed?.status || 0, failed || { title: 'Stream ended early', detail: 'The answer stopped before it finished. Try again.' })
  return done
}

// Product categories offered in the admin form. The API accepts any text (max 100); this list
// keeps the catalogue consistent, and the AI's product search filters on these exact values.
export const CATEGORIES = [
  'Fruits & Vegetables', 'Dairy & Eggs', 'Bakery', 'Staples', 'Oils & Ghee', 'Spices & Masalas',
  'Snacks', 'Beverages', 'Breakfast & Cereals', 'Frozen', 'Personal Care', 'Household',
]

const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })
export const money = (v) => (v === null || v === undefined || v === '' ? '—' : inr.format(Number(v)))
export const when = (v) =>
  v ? new Date(v).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '—'
