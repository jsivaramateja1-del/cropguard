import { cn } from '../../lib/utils.js'

export function Label({ className, children, hint, htmlFor, ...props }) {
  return (
    <label
      htmlFor={htmlFor}
      className={cn('block text-sm font-semibold leading-none text-foreground', className)}
      {...props}
    >
      {children}
      {hint ? (
        <span className="ml-1.5 text-xs font-normal text-muted-foreground">{hint}</span>
      ) : null}
    </label>
  )
}
