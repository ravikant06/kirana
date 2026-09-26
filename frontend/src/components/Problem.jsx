// Renders an RFC 7807 ProblemDetail the way the backend sent it.
// If your error responses look messy here, fix them in the backend.
export default function Problem({ error, compact }) {
  if (!error) return null
  const p = error.problem || { detail: error.message }
  const fieldErrors = Array.isArray(p.errors) ? p.errors : []
  return (
    <div className={`problem ${compact ? 'problem-compact' : ''}`} role="alert">
      <div className="problem-head">
        {p.status ? <span className="problem-status">{p.status}</span> : null}
        <strong>{p.title || 'Request failed'}</strong>
      </div>
      {p.detail ? <p>{p.detail}</p> : null}
      {fieldErrors.length > 0 && (
        <ul>
          {fieldErrors.map((e, i) => (
            <li key={i}>
              <code>{e.field}</code> {e.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
