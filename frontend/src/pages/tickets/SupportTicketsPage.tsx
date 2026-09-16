import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Select } from '../../components/ui/Select'
import { Button } from '../../components/ui/Button'
import type { SupportTicket, SupportTicketListResponse, SupportTicketStatus } from '../../types'

const STATUS_VARIANT: Record<SupportTicketStatus, 'amber' | 'blue' | 'green' | 'gray'> = {
  open: 'amber',
  in_progress: 'blue',
  resolved: 'green',
  closed: 'gray',
}

export default function SupportTicketsPage() {
  const qc = useQueryClient()
  // A support-ticket notification's "View ticket" link lands here with
  // ?deployment_id=... (see backend/app/services/notification_triggers.py)
  // — /tickets is the only place a ticket can be viewed, the deployment
  // detail page doesn't show tickets.
  const [searchParams, setSearchParams] = useSearchParams()
  const deploymentId = searchParams.get('deployment_id')

  const { data, isLoading } = useQuery({
    queryKey: ['tickets', deploymentId],
    queryFn: () => api.get<SupportTicketListResponse>('/tickets', { params: { deployment_id: deploymentId || undefined } }).then((r) => r.data),
    refetchInterval: 60_000,
  })

  const [target, setTarget] = useState<SupportTicket | null>(null)
  const [nextStatus, setNextStatus] = useState<SupportTicketStatus>('in_progress')

  const updateMutation = useMutation({
    mutationFn: () => api.patch(`/tickets/${target!.id}`, { status: nextStatus }),
    onSuccess: () => { toast.success('Ticket updated'); setTarget(null); qc.invalidateQueries({ queryKey: ['tickets'] }) },
    onError: () => toast.error('Failed to update ticket'),
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
    {
      key: 'actions', header: '', className: 'text-right',
      render: (t) => <Button size="sm" variant="secondary" onClick={() => { setTarget(t); setNextStatus(t.status) }}>Update</Button>,
    },
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
      <DataTable columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(t) => t.id} emptyMessage="No support tickets yet." />

      <Modal
        open={!!target} onClose={() => setTarget(null)} title={`Ticket — ${target?.subject ?? ''}`} size="sm"
        footer={<><Button variant="secondary" onClick={() => setTarget(null)}>Cancel</Button><Button loading={updateMutation.isPending} onClick={() => updateMutation.mutate()}>Save</Button></>}
      >
        {target && (
          <div className="space-y-4">
            <p className="text-sm text-muted whitespace-pre-wrap">{target.description}</p>
            <Select
              label="Status"
              options={[
                { value: 'open', label: 'Open' },
                { value: 'in_progress', label: 'In Progress' },
                { value: 'resolved', label: 'Resolved' },
                { value: 'closed', label: 'Closed' },
              ]}
              value={nextStatus}
              onChange={(e) => setNextStatus(e.target.value as SupportTicketStatus)}
            />
          </div>
        )}
      </Modal>
    </div>
  )
}
