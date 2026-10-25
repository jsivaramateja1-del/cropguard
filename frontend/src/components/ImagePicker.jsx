import { useRef, useState } from 'react'
import { Camera, ImagePlus, RotateCcw, X } from 'lucide-react'
import { Button } from './ui/button.jsx'
import { Alert } from './ui/alert.jsx'

const MAX_BYTES = 8 * 1024 * 1024
const ACCEPTED = ['image/jpeg', 'image/jpg', 'image/png', 'image/webp']

/**
 * Leaf capture. Two native inputs: `capture="environment"` opens the rear camera
 * directly on Android Chrome and iOS Safari, which is more reliable across the browsers
 * a farmer actually has than getUserMedia plus a custom preview. The second input has
 * no capture attribute, so it opens the photo picker instead.
 */
export default function ImagePicker({ file, previewUrl, onSelect, onClear, disabled }) {
  const cameraRef = useRef(null)
  const galleryRef = useRef(null)
  const [error, setError] = useState(null)

  function handleFiles(event) {
    const chosen = event.target.files?.[0]
    event.target.value = '' // so choosing the same file twice still fires
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
      <span className="block text-sm font-semibold text-foreground">Leaf photo</span>

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
          <Button type="button" onClick={() => cameraRef.current?.click()} disabled={disabled}>
            <Camera size={17} aria-hidden="true" />
            Take photo
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={() => galleryRef.current?.click()}
            disabled={disabled}
          >
            <ImagePlus size={17} aria-hidden="true" />
            From gallery
          </Button>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-card">
          <img
            src={previewUrl}
            alt="Selected leaf"
            className="max-h-72 w-full bg-muted object-contain"
          />
          <div className="flex items-center justify-between gap-2 border-t border-border px-3 py-2">
            <span className="truncate text-2xs text-muted-foreground">
              {file?.name} · {(file?.size / 1024).toFixed(0)} KB
            </span>
            <div className="flex shrink-0 gap-1">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => cameraRef.current?.click()}
                disabled={disabled}
              >
                <RotateCcw size={14} aria-hidden="true" />
                Retake
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="text-destructive hover:bg-destructive/10"
                onClick={onClear}
                disabled={disabled}
              >
                <X size={14} aria-hidden="true" />
                Remove
              </Button>
            </div>
          </div>
        </div>
      )}

      {error ? (
        <Alert variant="destructive">{error}</Alert>
      ) : (
        <p className="text-2xs leading-relaxed text-muted-foreground">
          Fill the frame with one affected leaf. Avoid shadow, glare and blur — they are the
          most common cause of a low-confidence result.
        </p>
      )}
    </div>
  )
}
