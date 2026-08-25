import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

/**
 * Clinical status badge (pill). Statuses use subtle background + strong text.
 * Never rely on color alone — always pair with a label (and optionally an icon).
 * Critical states must be unmistakable.
 */
const badgeVariants = cva(
  'inline-flex max-w-full items-center gap-1 break-words rounded-pill border border-transparent px-2.5 py-0.5 text-xs font-medium leading-snug shadow-[inset_0_1px_0_hsl(0_0%_100%/0.45)] [&_svg]:size-3.5 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        neutral: 'border-white/60 bg-surface-elevated/90 text-supporting',
        brand: 'border-brand/10 bg-brand-soft/90 text-supporting',
        success: 'border-success/10 bg-success-subtle text-success-strong',
        warning: 'border-warning/10 bg-warning-subtle text-warning-strong',
        critical: 'border-critical/10 bg-critical-subtle text-critical-strong',
        information: 'border-information/10 bg-information-subtle text-information-strong',
      },
    },
    defaultVariants: {
      variant: 'neutral',
    },
  }
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />
}

export { Badge, badgeVariants }
