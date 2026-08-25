import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

/**
 * Aivana card.
 * - default : white + #E2E8F0 border, flat (clinical default)
 * - elevated: soft morphism shadow for workspace hierarchy separation
 * - ai      : #F1F5F9 panel + #DDE7F0 accent border (AI-generated context)
 * Do not wrap every field in a card; avoid nested card stacks.
 */
const cardVariants = cva('min-w-0 rounded-2xl [overflow-wrap:anywhere] text-card-foreground', {
  variants: {
    variant: {
      default: 'border border-white/70 bg-[linear-gradient(145deg,hsl(var(--surface)/0.98),hsl(var(--surface-elevated)/0.72))] shadow-[0_1px_2px_hsl(var(--charcoal)/0.04),0_16px_36px_-26px_hsl(var(--brand-deep)/0.3)]',
      elevated: 'border border-white/85 bg-[linear-gradient(145deg,hsl(var(--surface)/1),hsl(var(--brand-soft)/0.72))] shadow-[0_2px_4px_hsl(var(--charcoal)/0.045),0_20px_48px_-24px_hsl(var(--brand-deep)/0.34)]',
      ai: 'aivana-ai-surface border border-ai-highlight/80 shadow-[0_16px_42px_-28px_hsl(var(--brand-deep)/0.34)]',
      clinical: 'border border-white/78 bg-[linear-gradient(145deg,hsl(var(--surface)/0.97),hsl(var(--surface-clinical)/0.68))] shadow-[0_12px_32px_-24px_hsl(var(--brand-deep)/0.28)]',
      supporting: 'border border-white/55 bg-[linear-gradient(145deg,hsl(var(--surface-elevated)/0.82),hsl(var(--brand-soft)/0.58))]',
      gradient: 'aivana-gradient border border-primary/15 text-primary-foreground shadow-[0_16px_36px_-24px_hsl(var(--brand-deep)/0.55)]',
      alert: 'border border-warning/25 bg-warning-subtle text-warning-strong',
    },
  },
  defaultVariants: {
    variant: 'default',
  },
})

export interface CardProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof cardVariants> {}

const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ className, variant, ...props }, ref) => (
    <div ref={ref} className={cn(cardVariants({ variant, className }))} {...props} />
  )
)
Card.displayName = 'Card'

const CardHeader = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex min-w-0 flex-col space-y-1.5 p-5 [&>*]:min-w-0', className)} {...props} />
  )
)
CardHeader.displayName = 'CardHeader'

const CardTitle = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLHeadingElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      className={cn('min-w-0 break-words font-display text-lg font-semibold leading-tight tracking-tight', className)}
      {...props}
    />
  )
)
CardTitle.displayName = 'CardTitle'

const CardDescription = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('min-w-0 break-words text-sm text-metadata', className)} {...props} />
  )
)
CardDescription.displayName = 'CardDescription'

const CardContent = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('min-w-0 p-5 pt-0 [&>*]:min-w-0', className)} {...props} />
  )
)
CardContent.displayName = 'CardContent'

const CardFooter = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn('flex min-w-0 flex-wrap items-center gap-3 p-5 pt-0', className)} {...props} />
  )
)
CardFooter.displayName = 'CardFooter'

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardDescription,
  CardContent,
  cardVariants,
}
