import { useSearchParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import type { SupportTicket, SupportTicketListResponse, SupportTicketStatus } from '../../types'

const STATUS_VARIANT: Record<SupportTicketStatus, 'amber' | 'blue' | 'green' | 'gray'> = {
  open: 'amber',
  in_progress: 'blue',
  resolved: 'green',
  closed: 'gray',
}

export default function SupportTicketsPage() {
  const navigate = useNavigate()
  // A support-ticket notification's "View ticket" link lands here with
  // ?deployment_id=... (see backend/app/services/notification_triggers.py)
  // — /tickets is the list; a row opens SupportTicketDetailPage.tsx
  // (HUB-Expansion.md Phase 6 — links + timeline live there).
  const [searchParams, setSearchParams] = useSearchParams()
  const deploymentId = searchParams.get('deployment_id')

  const { data, isLoading } = useQuery({
    queryKey: ['tickets', deploymentId],
    queryFn: () => api.get<SupportTicketListResponse>('/tickets', { params: { deployment_id: deploymentId || undefined } }).then((r) => r.data),
    refetchInterval: 60_000,
  })

  const columns: Column<SupportTicket>[] = [
    { key: 'created_at', header: 'Submitted', render: (t) => formatDate(t.created_at) },
    { key: 'subject', header: 'Subject', render: (t) => <span className="font-semibold">{t.subject}</span> },
    {
      key: 'submitted_by', header: 'From',
      render: (t) => <div><div>{t.submitted_by_name ?? '—'}</div>{t.submitted_by_email && <div className="text-xs text-muted">{t.submitted_by_email}</div>}</div>,
    },
    { key: 'priority', header: 'Priority', render: (t) => <span className="capitalize">{t.priority}</span> },
    { key: 'status', header: 'Status', render: (t) => <Badge variant={STATUS_VARIANT[t.status]}>{t.status.replace('_', ' ')}</Badge> },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Support Tickets</h1>
        {deploymentId && (
          <Button size="sm" variant="secondary" onClick={() => setSearchParams({})}>
            Filtered to one deployment — Clear
          </Button>
        )}
      </div>
      <DataTable
        columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(t) => t.id}
        emptyMessage="No support tickets yet." onRowClick={(t) => navigate(`/tickets/${t.id}`)}
      />
    </div>
  )
}
