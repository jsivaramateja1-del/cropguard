import { Check } from 'lucide-react'
import { cn } from '../lib/utils.js'

export const CROPS = [
  { id: 'Tomato', label: 'Tomato', hint: 'Early blight, late blight, leaf mould' },
  { id: 'Rice', label: 'Rice', hint: 'Bacterial blight, brown spot, leaf smut' },
  { id: 'Potato', label: 'Potato', hint: 'Early blight, late blight' },
  { id: 'Wheat', label: 'Wheat', hint: 'Yellow rust, brown rust, Septoria' },
]

/**
 * Crop selection as four large targets.
 *
 * A dropdown would be two taps on a small control, and choosing the wrong crop is the
 * most common way to get a misleading result — which is why the backend routes a
 * crop/photo mismatch to expert review.
 */
export default function CropPicker({ value, onChange, disabled }) {
  return (
    <fieldset disabled={disabled} className="space-y-2">
      <legend className="mb-2 block text-sm font-semibold text-foreground">
        Which crop is in the photo?
      </legend>
      <div className="grid grid-cols-2 gap-2">
        {CROPS.map((crop) => {
          const selected = value === crop.id
          return (
            <button
              key={crop.id}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(crop.id)}
              className={cn(
                'flex min-h-touch flex-col items-start gap-0.5 rounded-lg border px-3 py-2.5 text-left transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background',
                selected
                  ? 'border-primary bg-primary/5 ring-1 ring-primary'
                  : 'border-border bg-card hover:border-primary/40 hover:bg-primary/[0.03]',
              )}
            >
              <span className="flex w-full items-center justify-between">
                <span className="text-sm font-semibold text-foreground">{crop.label}</span>
                {selected ? <Check size={15} className="text-primary" aria-hidden="true" /> : null}
              </span>
              <span className="text-2xs leading-snug text-muted-foreground">{crop.hint}</span>
            </button>
          )
        })}
      </div>
      <p className="pt-1 text-2xs leading-relaxed text-muted-foreground">
        Wheat has no trained classes yet — the model covers 16 of the 21 supported diseases. A
        wheat photo is routed to expert review.
      </p>
    </fieldset>
  )
}
