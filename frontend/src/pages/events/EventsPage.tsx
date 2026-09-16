import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link2, X } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Select } from '../../components/ui/Select'
import { Button } from '../../components/ui/Button'
import type { OperationalEvent, OperationalEventListResponse, OperationalEventStatus } from '../../types'

const STATUS_VARIANT: Record<OperationalEventStatus, 'amber' | 'blue' | 'green' | 'red' | 'gray'> = {
  info: 'gray',
  pending: 'amber',
  success: 'green',
  failure: 'red',
}

// entity_type is an open string catalog (see backend's core/event_types.py)
// — this is just the set actually emitted today, not an exhaustive list.
const ENTITY_TYPE_OPTIONS = [
  { value: 'deployment', label: 'Deployment' },
  { value: 'ticket', label: 'Ticket' },
  { value: 'maintenance_window', label: 'Maintenance window' },
  { value: 'workflow_instance', label: 'Workflow instance' },
]

function short(id: string | null): string {
  return id ? id.slice(0, 8) : '—'
}

export default function EventsPage() {
  const [entityType, setEntityType] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [correlationId, setCorrelationId] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['events', entityType, statusFilter, correlationId],
    queryFn: () => api.get<OperationalEventListResponse>('/events', {
      params: {
        entity_type: entityType || undefined,
        correlation_id: correlationId || undefined,
        page_size: 100,
      },
    }).then((r) => r.data),
    refetchInterval: 30_000,
  })

  const items = statusFilter ? (data?.items ?? []).filter((e) => e.status === statusFilter) : (data?.items ?? [])

  const columns: Column<OperationalEvent>[] = [
    { key: 'created_at', header: 'When', render: (e) => formatDate(e.created_at) },
    { key: 'event_type', header: 'Event', render: (e) => <span className="font-mono text-xs text-text">{e.event_type}</span> },
    { key: 'status', header: 'Status', render: (e) => <Badge variant={STATUS_VARIANT[e.status]}>{e.status}</Badge> },
    {
      key: 'entity', header: 'Entity',
      render: (e) => <span className="text-muted">{e.entity_type ?? '—'} <span className="font-mono text-xs">{short(e.entity_id)}</span></span>,
    },
    {
      key: 'deployment_id', header: 'Deployment',
      render: (e) => <span className="font-mono text-xs text-muted">{short(e.deployment_id)}</span>,
    },
    {
      key: 'actor', header: 'Actor',
      render: (e) => <span className="text-muted capitalize">{e.actor_type.replace('_', ' ')}</span>,
    },
    {
      key: 'correlation_id', header: '',
      render: (e) => (
        <button
          title={`Show every event correlated with ${e.correlation_id}`}
          onClick={() => setCorrelationId(e.correlation_id)}
          className="text-muted hover:text-accent"
        >
          <Link2 size={14} />
        </button>
      ),
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-xl font-semibold text-text">Operational Events</h1>
      </div>
      <p className="text-sm text-muted mb-4">
        A cross-domain log of what happened, when, and who/what did it — deployments, tickets, maintenance, and
        approvals. Click <Link2 size={12} className="inline -mt-0.5" /> on a row to see every event that belongs to
        the same operation (e.g. a renewal request, its approval, and the resulting call to the deployment).
      </p>

      <div className="flex items-center gap-3 mb-4">
        <Select
          className="w-auto" placeholder="All entity types" value={entityType} onChange={(e) => setEntityType(e.target.value)}
          options={ENTITY_TYPE_OPTIONS}
        />
        <Select
          className="w-auto" placeholder="All statuses" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          options={[
            { value: 'info', label: 'Info' },
            { value: 'pending', label: 'Pending' },
            { value: 'success', label: 'Success' },
            { value: 'failure', label: 'Failure' },
          ]}
        />
        {correlationId && (
          <Button size="sm" variant="secondary" icon={<X size={13} />} onClick={() => setCorrelationId(null)}>
            Correlation: {short(correlationId)}
          </Button>
        )}
      </div>

      <DataTable columns={columns} data={items} loading={isLoading} keyExtractor={(e) => e.id} emptyMessage="No events recorded yet." />
    </div>
  )
}
