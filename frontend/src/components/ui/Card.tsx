import { cn } from '../../lib/utils'

interface CardProps {
  children: React.ReactNode
  className?: string
  header?: React.ReactNode
  footer?: React.ReactNode
  padding?: 'none' | 'sm' | 'md' | 'lg'
}

const paddings = {
  none: '',
  sm: 'p-4',
  md: 'p-5',
  lg: 'p-6',
}

export function Card({ children, className, header, footer, padding = 'md' }: CardProps) {
  return (
    <div className={cn('bg-surface rounded-lg shadow-subtle border border-border', className)}>
      {header && (
        <div className="px-5 py-4 border-b border-border font-semibold text-text">
          {header}
        </div>
      )}
      <div className={paddings[padding]}>{children}</div>
      {footer && (
        <div className="px-5 py-3 border-t border-border text-sm text-muted">
          {footer}
        </div>
      )}
    </div>
  )
}
