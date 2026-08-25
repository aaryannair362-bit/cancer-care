import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'

import { cn } from '@/lib/utils'

/**
 * Aivana button. One clear primary action per local context.
 * - primary   : brand (#6F8FAF) + white
 * - secondary : soft brand (#DDE7F0) + supporting text (#334155)
 * - destructive: critical (#C94F4F) + white
 */
const buttonVariants = cva(
  'inline-flex max-w-full min-w-0 items-center justify-center gap-2 whitespace-normal break-words rounded-xl text-center text-sm font-semibold leading-snug ring-offset-background transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 [&_svg]:size-4 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'aivana-gradient border border-white/10 text-primary-foreground shadow-[0_8px_20px_-12px_hsl(var(--brand-deep)/0.75)] hover:-translate-y-px hover:brightness-[0.98] hover:shadow-[0_12px_24px_-14px_hsl(var(--brand-deep)/0.8)]',
        secondary:
          'border border-white/62 bg-[linear-gradient(135deg,hsl(var(--surface-elevated)/0.92),hsl(var(--brand-soft)/0.9))] text-secondary-foreground shadow-soft-sm hover:border-brand/15 hover:brightness-[0.98]',
        outline:
          'border border-white/68 bg-surface/72 text-foreground shadow-soft-sm backdrop-blur-sm hover:border-brand/18 hover:bg-accent/75 hover:text-accent-foreground',
        ghost: 'text-foreground hover:bg-accent hover:text-accent-foreground',
        destructive:
          'bg-destructive text-destructive-foreground hover:bg-destructive/90',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        sm: 'min-h-9 px-3 py-2',
        default: 'min-h-10 px-4 py-2',
        lg: 'min-h-11 px-6 py-2.5',
        icon: 'h-10 w-10',
      },
    },
    defaultVariants: {
      variant: 'primary',
      size: 'default',
    },
  }
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={cn(buttonVariants({ variant, size, className }))}
        {...props}
      />
    )
  }
)
Button.displayName = 'Button'

export { Button, buttonVariants }
