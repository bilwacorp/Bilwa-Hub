import { cn } from '../../lib/utils'

interface Tab {
  key: string
  label: string
  count?: number
}

interface TabsProps {
  tabs: Tab[]
  active: string
  onChange: (key: string) => void
  className?: string
}

export function Tabs({ tabs, active, onChange, className }: TabsProps) {
  return (
    <div className={cn('flex gap-1 p-1 bg-background rounded-lg border border-border overflow-x-auto', className)}>
      {tabs.map((tab) => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded text-sm font-medium transition-all shrink-0',
            active === tab.key ? 'bg-surface text-text shadow-subtle' : 'text-muted hover:text-text',
          )}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className={cn('text-xs px-1.5 py-0.5 rounded-full', active === tab.key ? 'bg-accent/10 text-accent' : 'bg-border text-muted')}>
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}
