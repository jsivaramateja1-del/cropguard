import { useEffect, useRef, useState } from 'react'
import { Camera, ImagePlus, RotateCcw, X } from 'lucide-react'

const MAX_BYTES = 8 * 1024 * 1024
const ACCEPTED = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']

/**
 * Leaf photo capture.
 *
 * Two inputs, both native: `capture="environment"` opens the rear camera directly on
 * Android Chrome and iOS Safari, which is more reliable across the browsers a farmer
 * actually has than getUserMedia plus a custom preview. The gallery input has no
 * capture attribute so it opens the photo picker instead.
 */
export default function ImagePicker({ file, previewUrl, onSelect, onClear, disabled }) {
  const cameraRef = useRef(null)
  const galleryRef = useRef(null)
  const [error, setError] = useState(null)

  useEffect(() => () => setError(null), [])

  function handleFiles(event) {
    const chosen = event.target.files?.[0]
    // Reset so choosing the same file twice still fires onChange.
    event.target.value = ''
    if (!chosen) return

    if (!ACCEPTED.includes(chosen.type)) {
      setError('That file is not a JPEG, PNG or WebP image.')
      return
    }
    if (chosen.size > MAX_BYTES) {
      setError(`That photo is ${(chosen.size / 1024 / 1024).toFixed(1)} MB. Use one under 8 MB.`)
      return
    }
    setError(null)
    onSelect(chosen)
  }

  return (
    <div className="space-y-2">
      <span className="field-label">Leaf photo</span>

      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="sr-only"
        onChange={handleFiles}
        disabled={disabled}
        aria-label="Take a photo of the leaf"
      />
      <input
        ref={galleryRef}
        type="file"
        accept="image/*"
        className="sr-only"
        onChange={handleFiles}
        disabled={disabled}
        aria-label="Choose a leaf photo from your gallery"
      />

      {!previewUrl ? (
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            className="btn-primary"
            onClick={() => cameraRef.current?.click()}
            disabled={disabled}
          >
            <Camera size={18} aria-hidden="true" />
            Take photo
          </button>
          <button
            type="button"
            className="btn-secondary"
            onClick={() => galleryRef.current?.click()}
            disabled={disabled}
          >
            <ImagePlus size={18} aria-hidden="true" />
            From gallery
          </button>
        </div>
      ) : (
        <div className="card overflow-hidden">
          <img
            src={previewUrl}
            alt="Selected leaf"
            className="max-h-72 w-full bg-soil-100 object-contain"
          />
          <div className="flex items-center justify-between gap-2 border-t border-soil-200 px-3 py-2">
            <span className="truncate text-xs text-soil-600">
              {file?.name} · {(file?.size / 1024).toFixed(0)} KB
            </span>
            <div className="flex shrink-0 gap-1">
              <button
                type="button"
                className="btn-ghost px-2 py-1 text-sm"
                onClick={() => cameraRef.current?.click()}
                disabled={disabled}
              >
                <RotateCcw size={15} aria-hidden="true" />
                Retake
              </button>
              <button
                type="button"
                className="btn-ghost px-2 py-1 text-sm text-alert-600 hover:bg-red-50"
                onClick={onClear}
                disabled={disabled}
              >
                <X size={15} aria-hidden="true" />
                Remove
              </button>
            </div>
          </div>
        </div>
      )}

      {error ? (
        <p role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs font-medium text-alert-600">
          {error}
        </p>
      ) : (
        <p className="field-hint">
          Fill the frame with a single affected leaf. Avoid shadow, glare and blurry
          shots — they are the most common cause of a low-confidence result.
        </p>
      )}
    </div>
  )
}
