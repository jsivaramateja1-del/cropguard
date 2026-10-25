import { cva } from 'class-variance-authority'
import { cn } from '../../lib/utils.js'

const badgeVariants = cva(
  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-2xs font-semibold transition-colors',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-primary text-primary-foreground',
        secondary: 'border-transparent bg-secondary text-secondary-foreground',
        outline: 'border-border text-foreground',
        muted: 'border-transparent bg-muted text-muted-foreground',
        accent: 'border-transparent bg-accent/15 text-accent-foreground',
        destructive: 'border-transparent bg-destructive/12 text-destructive',
        // Severity variants, matching the risk tokens.
        low: 'border-transparent bg-risk-low/12 text-risk-low',
        moderate: 'border-transparent bg-risk-moderate/15 text-[hsl(38_85%_28%)]',
        high: 'border-transparent bg-risk-high/15 text-risk-high',
        critical: 'border-transparent bg-risk-critical/12 text-risk-critical',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export function Badge({ className, variant, ...props }) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { badgeVariants }
