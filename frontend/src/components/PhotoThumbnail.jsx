import { useState } from 'react'
import { ImageOff } from 'lucide-react'
import { imageUrl } from '../api.js'

/**
 * A stored leaf photo.
 *
 * The API returns image paths relative to itself (`/api/images/<token>`), because the
 * backend may be on a different origin from the frontend. This resolves them and degrades
 * to a neutral placeholder when the bytes cannot be fetched — an image that failed to load
 * should never look like a broken page.
 */
export default function PhotoThumbnail({
  src,
  alt = 'Leaf photo',
  size = 'md',
  className = '',
}) {
  const [failed, setFailed] = useState(false)

  const dimensions = {
    sm: 'h-12 w-12',
    md: 'h-16 w-16',
    lg: 'h-24 w-24',
  }[size]

  const resolved = imageUrl(src)

  if (!resolved || failed) {
    return (
      <div
        className={`${dimensions} flex shrink-0 items-center justify-center rounded-lg border border-border bg-muted ${className}`}
        role="img"
        aria-label={resolved ? `${alt} — could not be loaded` : 'No photo available'}
        title={resolved ? 'The photo could not be loaded' : 'No photo stored for this report'}
      >
        <ImageOff size={16} className="text-muted-foreground" aria-hidden="true" />
      </div>
    )
  }

  return (
    <img
      src={resolved}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
      className={`${dimensions} shrink-0 rounded-lg border border-border object-cover ${className}`}
    />
  )
}
