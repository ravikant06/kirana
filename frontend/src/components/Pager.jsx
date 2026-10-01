import { ChevronLeft, ChevronRight } from './icons.jsx'

// Page numbers around the current one, with the first and last always reachable.
function pages(page, total) {
  const set = new Set([0, total - 1, page - 1, page, page + 1].filter((p) => p >= 0 && p < total))
  const sorted = [...set].sort((a, b) => a - b)
  const out = []
  sorted.forEach((p, i) => {
    if (i > 0 && p - sorted[i - 1] > 1) out.push(`gap-${p}`)
    out.push(p)
  })
  return out
}

export default function Pager({ page, totalPages, onChange }) {
  if (!totalPages || totalPages <= 1) return null
  return (
    <nav className="pager" aria-label="Pages">
      <button className="pager-btn pager-step" disabled={page <= 0} onClick={() => onChange(page - 1)} aria-label="Previous page">
        <ChevronLeft size={16} /> <span>Previous</span>
      </button>
      <span className="pager-nums">
        {pages(page, totalPages).map((p) =>
          typeof p === 'string' ? (
            <span key={p} className="pager-gap">…</span>
          ) : (
            <button
              key={p}
              className={`pager-btn pager-num ${p === page ? 'is-active' : ''}`}
              aria-current={p === page ? 'page' : undefined}
              onClick={() => p !== page && onChange(p)}
            >
              {(p + 1).toLocaleString('en-IN')}
            </button>
          ),
        )}
      </span>
      <span className="pager-compact">Page {page + 1} of {totalPages.toLocaleString('en-IN')}</span>
      <button className="pager-btn pager-step" disabled={page >= totalPages - 1} onClick={() => onChange(page + 1)} aria-label="Next page">
        <span>Next</span> <ChevronRight size={16} />
      </button>
    </nav>
  )
}
