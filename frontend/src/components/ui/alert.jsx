import { cva } from 'class-variance-authority'
import { cn } from '../../lib/utils.js'

const alertVariants = cva('relative flex gap-2.5 rounded-lg border px-3 py-2.5 text-xs leading-relaxed', {
  variants: {
    variant: {
      default: 'border-border bg-card text-foreground',
      info: 'border-border bg-muted text-secondary-foreground',
      warning: 'border-accent/40 bg-accent/10 text-accent-foreground',
      destructive: 'border-destructive/30 bg-destructive/8 text-destructive',
      success: 'border-primary/25 bg-primary/8 text-primary',
    },
  },
  defaultVariants: { variant: 'default' },
})

export function Alert({ className, variant, children, ...props }) {
  return (
    <div role="alert" className={cn(alertVariants({ variant }), className)} {...props}>
      {children}
    </div>
  )
}

export function AlertTitle({ className, ...props }) {
  return <p className={cn('font-semibold', className)} {...props} />
}

export { alertVariants }
