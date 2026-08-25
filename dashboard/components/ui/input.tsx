import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Aivana input: #F8FAFC background, #E2E8F0 border, focus ring brand-strong (#8FA8BF).
 */
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        ref={ref}
        className={cn(
          'flex h-10 w-full rounded-lg border border-white/72 bg-input-background/92 px-3 py-2 text-sm text-foreground shadow-[inset_0_1px_2px_hsl(var(--charcoal)/0.04),0_1px_2px_hsl(var(--charcoal)/0.05)] ring-offset-background transition-all',
          'placeholder:text-metadata',
          'focus-visible:border-brand-strong focus-visible:bg-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40 focus-visible:ring-offset-1',
          'disabled:cursor-not-allowed disabled:opacity-50',
          'file:border-0 file:bg-transparent file:text-sm file:font-medium',
          className
        )}
        {...props}
      />
    )
  }
)
Input.displayName = 'Input'

export { Input }
