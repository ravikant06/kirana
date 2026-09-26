export default function Pager({ page, totalPages, onChange }) {
  if (!totalPages || totalPages <= 1) return null
  return (
    <nav className="pager" aria-label="Pages">
      <button className="btn-quiet" disabled={page <= 0} onClick={() => onChange(page - 1)}>Previous</button>
      <span>Page {page + 1} of {totalPages}</span>
      <button className="btn-quiet" disabled={page >= totalPages - 1} onClick={() => onChange(page + 1)}>Next</button>
    </nav>
  )
}
