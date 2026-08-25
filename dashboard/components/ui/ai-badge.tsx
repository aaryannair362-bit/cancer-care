import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { Sparkles } from 'lucide-react'

import { cn } from '@/lib/utils'

/**
 * AI marker. AI-generated content must be distinguishable from human-verified
 * / final information. Use to flag provenance in the clinical workflow.
 * - generated: AI highlight (#DDE7F0) + AI emphasis text — the default cue
 * - emphasis : solid brand — for stronger emphasis on AI actions/processing
 */
const aiBadgeVariants = cva(
  'inline-flex items-center gap-1 rounded-pill px-2.5 py-0.5 text-xs font-medium [&_svg]:size-3.5',
  {
    variants: {
      variant: {
        generated: 'border border-ai-emphasis/20 bg-gradient-to-r from-ai-highlight to-information-subtle text-information-strong shadow-[inset_0_1px_0_hsl(0_0%_100%/0.65)]',
        emphasis: 'aivana-gradient text-primary-foreground shadow-soft-sm',
      },
    },
    defaultVariants: {
      variant: 'generated',
    },
  }
)

export interface AiBadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof aiBadgeVariants> {
  /** Hide the leading sparkle icon */
  hideIcon?: boolean
}

function AiBadge({ className, variant, hideIcon, children, ...props }: AiBadgeProps) {
  return (
    <span className={cn(aiBadgeVariants({ variant, className }))} {...props}>
      {!hideIcon && <Sparkles aria-hidden="true" />}
      {children ?? 'AI'}
    </span>
  )
}

export { AiBadge, aiBadgeVariants }
