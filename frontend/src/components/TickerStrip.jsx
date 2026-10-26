import { Leaf } from 'lucide-react'

/**
 * A slow horizontal strip of real advisory facts.
 *
 * Content comes from the knowledge base rather than being invented, so nothing here can
 * claim something the app does not actually say elsewhere. The animation pauses on hover
 * and stops entirely under `prefers-reduced-motion` — see index.css.
 */
export default function TickerStrip({ items = [] }) {
  if (!items.length) return null

  // Duplicated once so the loop is seamless at -50%.
  const doubled = [...items, ...items]

  return (
    <div
      className="relative overflow-hidden rounded-xl border border-border bg-card"
      aria-label="CropGuard facts"
    >
      <div className="pointer-events-none absolute inset-y-0 left-0 z-10 w-8 bg-gradient-to-r from-card to-transparent" />
      <div className="pointer-events-none absolute inset-y-0 right-0 z-10 w-8 bg-gradient-to-l from-card to-transparent" />

      <div className="animate-ticker flex w-max items-center gap-6 py-2.5 pl-6">
        {doubled.map((item, index) => (
          <span
            key={`${item}-${index}`}
            className="flex shrink-0 items-center gap-1.5 text-2xs text-muted-foreground"
            // The second copy is decoration for the loop; do not read it twice.
            aria-hidden={index >= items.length ? 'true' : undefined}
          >
            <Leaf size={12} className="shrink-0 text-primary" aria-hidden="true" />
            {item}
          </span>
        ))}
      </div>
    </div>
  )
}
