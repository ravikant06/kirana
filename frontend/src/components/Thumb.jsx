export default function Thumb({ src, alt, className = '' }) {
  if (!src) {
    return (
      <div className={`thumb thumb-empty ${className}`} aria-label="No image yet">
        <span>{(alt || '?').slice(0, 1).toUpperCase()}</span>
      </div>
    )
  }
  return <img className={`thumb ${className}`} src={src} alt={alt || ''} loading="lazy" />
}
