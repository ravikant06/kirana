import { useState } from 'react'

// Shows an image, or a letter placeholder when there is none or it fails to load.
// Stage 5 graceful degradation: when MinIO is down, pages still render, just without photos.
export default function Thumb({ src, alt, className = '' }) {
  const [failed, setFailed] = useState(false)
  if (!src || failed) {
    return (
      <div className={`thumb thumb-empty ${className}`} aria-label={failed ? 'Image unavailable' : 'No image yet'}>
        <span>{(alt || '?').slice(0, 1).toUpperCase()}</span>
      </div>
    )
  }
  return <img className={`thumb ${className}`} src={src} alt={alt || ''} loading="lazy" onError={() => setFailed(true)} />
}
