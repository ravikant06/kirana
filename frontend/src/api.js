// Thin fetch wrapper. Every call is recorded so the Requests panel can show
// exactly what the backend received and returned. No retries, no caching:
// the frontend should never hide backend behaviour from you.

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
  constructor(status, problem) {
    super(problem?.detail || problem?.title || `Request failed with status ${status}`)
    this.status = status
    this.problem = problem
  }
}

async function request(method, path, body) {
  const headers = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (currentUserId) headers['X-User-Id'] = String(currentUserId)

  const entry = {
    id: crypto.randomUUID(),
    method,
    path,
    userId: currentUserId,
    requestBody: body,
    at: new Date(),
  }
  const started = performance.now()

  let res
  try {
    res = await fetch('/api' + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    const problem = {
      title: 'Backend not reachable',
      detail: 'The request never got a response. Is Spring Boot running on port 8080?',
    }
    record({ ...entry, status: 0, ms: Math.round(performance.now() - started), responseBody: problem })
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
  record({ ...entry, status: res.status, ms: Math.round(performance.now() - started), responseBody: data })

  if (!res.ok) {
    if (!text && res.status >= 500) {
      throw new ApiError(res.status, {
        status: res.status,
        title: 'Empty error response',
        detail: `Got ${res.status} with no body. If the backend is down, the dev proxy answers this way.`,
      })
    }
    const problem = data && typeof data === 'object' ? data : { status: res.status, detail: String(data || res.statusText) }
    throw new ApiError(res.status, problem)
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
    list: () => request('GET', '/users'),
    create: (body) => request('POST', '/users', body),
  },
  products: {
    list: (page = 0, size = 12) => request('GET', `/products?page=${page}&size=${size}`),
    get: (id) => request('GET', `/products/${q(id)}`),
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
    add: (productId, quantity) => request('POST', '/cart/items', { productId, quantity }),
    update: (productId, quantity) => request('PUT', `/cart/items/${q(productId)}`, { quantity }),
    remove: (productId) => request('DELETE', `/cart/items/${q(productId)}`),
  },
  orders: {
    place: () => request('POST', '/orders'),
    list: () => request('GET', '/orders'),
    get: (id) => request('GET', `/orders/${q(id)}`),
  },
}

const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })
export const money = (v) => (v === null || v === undefined || v === '' ? '—' : inr.format(Number(v)))
export const when = (v) =>
  v ? new Date(v).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' }) : '—'
