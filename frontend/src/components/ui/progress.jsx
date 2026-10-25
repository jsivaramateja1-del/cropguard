import { cn } from '../../lib/utils.js'

/** A labelled meter with an optional threshold marker. */
export function Progress({ value = 0, className, barClassName, markerAt, label, ...props }) {
  const clamped = Math.max(0, Math.min(100, value))
  return (
    <div className="w-full">
      {label ? (
        <div className="mb-1 flex items-baseline justify-between text-xs">
          <span className="font-medium text-muted-foreground">{label.text}</span>
          <span className={cn('tabular font-semibold', label.className)}>{label.value}</span>
        </div>
      ) : null}
      <div
        role="meter"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(clamped)}
        aria-label={props['aria-label'] || 'progress'}
        className={cn('relative h-2 w-full overflow-hidden rounded-full bg-muted', className)}
      >
        <div
          className={cn('h-full rounded-full bg-primary transition-[width] duration-300', barClassName)}
          style={{ width: `${clamped}%` }}
        />
        {markerAt != null ? (
          <span
            className="absolute top-0 h-full w-px bg-foreground/50"
            style={{ left: `${markerAt}%` }}
            title={`Threshold at ${markerAt}%`}
          />
        ) : null}
      </div>
    </div>
  )
}
