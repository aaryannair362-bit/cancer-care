import * as React from 'react'

import { cn } from '@/lib/utils'

/**
 * Consistent page container. Every workspace page wraps its content in this to
 * keep max width, horizontal gutters, and vertical rhythm uniform across modules.
 */
export function PageContainer({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn('mx-auto min-w-0 w-full max-w-[1480px] overflow-x-clip px-4 py-6 sm:px-6 lg:px-8 lg:py-8', className)}
      {...props}
    >
      {children}
    </div>
  )
}

interface PageHeaderProps extends React.HTMLAttributes<HTMLDivElement> {
  title: string
  description?: string
  /** Optional actions rendered on the right (e.g. a primary button). */
  actions?: React.ReactNode
}

/**
 * Standard page heading block: title + optional description and actions.
 * One clear primary action per local context.
 */
export function PageHeader({
  title,
  description,
  actions,
  className,
  ...props
}: PageHeaderProps) {
  const actionLabel = React.isValidElement<{ children?: React.ReactNode }>(actions)
    ? actions.props.children
    : null
  const showActions = !(
    typeof actionLabel === 'string' &&
    /^(fictional demo data|demo environment)$/i.test(actionLabel)
  )
  return (
    <div
      className={cn('mb-6 flex flex-wrap items-start justify-between gap-4', className)}
      {...props}
    >
      <div className="min-w-0">
        <h2 className="font-display text-3xl font-semibold tracking-[-0.04em] text-foreground sm:text-[2.15rem] sm:leading-tight">{title}</h2>
        {description ? (
          <p className="mt-2 max-w-3xl text-[15px] leading-6 text-metadata">{description}</p>
        ) : null}
      </div>
      {showActions && actions ? <div className="flex items-center gap-2">{actions}</div> : null}
    </div>
  )
}
