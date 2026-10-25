import { Check } from 'lucide-react'

export const CROPS = [
  { id: 'Tomato', label: 'Tomato', hint: 'Early blight, late blight, leaf mould…' },
  { id: 'Rice', label: 'Rice', hint: 'Bacterial blight, brown spot, leaf smut' },
  { id: 'Potato', label: 'Potato', hint: 'Early blight, late blight' },
  { id: 'Wheat', label: 'Wheat', hint: 'Yellow rust, brown rust, Septoria' },
]

/**
 * Crop selection.
 *
 * Four large targets rather than a dropdown: a dropdown on a phone in the field is a
 * two-tap, small-target interaction, and picking the wrong crop is the single most
 * common way to get a misleading result — the backend routes a crop/photo mismatch to
 * expert review precisely because it happens.
 */
export default function CropPicker({ value, onChange, disabled }) {
  return (
    <fieldset disabled={disabled} className="space-y-2">
      <legend className="field-label">Which crop is in the photo?</legend>
      <div className="grid grid-cols-2 gap-2">
        {CROPS.map((crop) => {
          const selected = value === crop.id
          return (
            <button
              key={crop.id}
              type="button"
              aria-pressed={selected}
              onClick={() => onChange(crop.id)}
              className={`flex flex-col items-start gap-0.5 rounded-lg border px-3 py-2.5 text-left transition-colors ${
                selected
                  ? 'border-leaf-600 bg-leaf-50 ring-1 ring-leaf-600'
                  : 'border-soil-300 bg-white hover:border-leaf-400 hover:bg-leaf-50/50'
              }`}
            >
              <span className="flex w-full items-center justify-between">
                <span className="text-base font-semibold text-leaf-900">{crop.label}</span>
                {selected && <Check size={16} className="text-leaf-700" aria-hidden="true" />}
              </span>
              <span className="text-[11px] leading-snug text-soil-600">{crop.hint}</span>
            </button>
          )
        })}
      </div>
      <p className="field-hint">
        Wheat has no trained classes yet — the model covers 16 of the 21 supported
        diseases. A wheat photo will be routed to expert review.
      </p>
    </fieldset>
  )
}
