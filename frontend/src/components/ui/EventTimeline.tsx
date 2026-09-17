import { formatDate } from '../../lib/utils'
import { Badge } from './Badge'
import type { OperationalEvent } from '../../types'

// HUB-Expansion.md Phase 9 — shared by SupportTicketDetailPage.tsx
// (Phase 6, the original of this component) and DeploymentDetailPage.tsx.
// Both pages fetch a curated OperationalEvent list server-side; this is
// purely presentational.
export function EventTimeline({ events, emptyMessage = 'No timeline events yet.' }: { events: OperationalEvent[]; emptyMessage?: string }) {
  return (
    <div className="space-y-0">
      {events.map((e, i) => (
        <div key={e.id} className="flex gap-3">
          <div className="flex flex-col items-center">
            <span className="h-2 w-2 rounded-full bg-accent shrink-0 mt-1.5" />
            {i < events.length - 1 && <span className="w-px flex-1 bg-border" />}
          </div>
          <div className="pb-4 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-text">{e.event_type}</span>
              {e.status !== 'info' && <Badge variant={e.status === 'success' ? 'green' : e.status === 'failure' ? 'red' : 'amber'}>{e.status}</Badge>}
            </div>
            <div className="text-xs text-muted mt-0.5">{formatDate(e.created_at)}</div>
          </div>
        </div>
      ))}
      {events.length === 0 && <p className="text-sm text-muted">{emptyMessage}</p>}
    </div>
  )
}
