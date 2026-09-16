import { forwardRef, useState } from 'react'
import { Eye, EyeOff } from 'lucide-react'
import { cn } from '../../lib/utils'

interface PasswordInputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'type' | 'prefix'> {
  label?: string
  error?: string
  helper?: string
}

// Same visual shape as Input, plus a show/hide toggle — kept as its own
// component (rather than an Input prop) since the toggle needs to sit
// inside a wrapper around just the <input>, not the label/error block, to
// stay vertically centered regardless of whether a label is present.
export const PasswordInput = forwardRef<HTMLInputElement, PasswordInputProps>(
  ({ label, error, helper, className, id, ...props }, ref) => {
    const [visible, setVisible] = useState(false)
    const inputId = id || label?.toLowerCase().replace(/\s+/g, '-')
    return (
      <div className="flex flex-col gap-1.5">
        {label && (
          <label htmlFor={inputId} className="text-sm font-medium text-text">
            {label}
          </label>
        )}
        <div className="relative">
          <input
            ref={ref}
            id={inputId}
            type={visible ? 'text' : 'password'}
            className={cn(
              'w-full h-10 rounded border bg-surface text-base text-text placeholder:text-hint pl-3 pr-10',
              'focus:outline-none focus:ring-2 focus:ring-accent/30 focus:border-accent transition-colors',
              error ? 'border-danger focus:ring-danger/30' : 'border-border',
              className,
            )}
            {...props}
          />
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setVisible((v) => !v)}
            aria-label={visible ? 'Hide password' : 'Show password'}
            className="absolute right-0 top-0 h-10 w-10 flex items-center justify-center text-muted hover:text-text"
          >
            {visible ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
        {error && <p className="text-xs text-danger">{error}</p>}
        {helper && !error && <p className="text-xs text-muted">{helper}</p>}
      </div>
    )
  },
)

PasswordInput.displayName = 'PasswordInput'
