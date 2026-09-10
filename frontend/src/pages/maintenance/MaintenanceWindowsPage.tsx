import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import { Plus } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import type { MaintenanceWindow, MaintenanceWindowListResponse, MaintenanceWindowStatus } from '../../types'

const STATUS_VARIANT: Record<MaintenanceWindowStatus, 'blue' | 'amber' | 'green' | 'gray'> = {
  planned: 'blue',
  in_progress: 'amber',
  completed: 'green',
  cancelled: 'gray',
}

type NewWindowForm = { scheduled_start: string; scheduled_end: string; description: string; read_only: boolean }

export default function MaintenanceWindowsPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['maintenance-windows'],
    queryFn: () => api.get<MaintenanceWindowListResponse>('/maintenance-windows').then((r) => r.data),
    refetchInterval: 30_000,
  })

  const [showCreate, setShowCreate] = useState(false)
  const form = useForm<NewWindowForm>({ defaultValues: { read_only: false } })
  const createMutation = useMutation({
    // datetime-local gives a naive *local* string; the backend stores naive
    // UTC — convert here so a window scheduled for "14:00" means 14:00 the
    // operator's time, not 14:00 UTC.
    mutationFn: (v: NewWindowForm) => api.post('/maintenance-windows', {
      ...v,
      scheduled_start: new Date(v.scheduled_start).toISOString(),
      scheduled_end: new Date(v.scheduled_end).toISOString(),
    }),
    onSuccess: () => { toast.success('Maintenance window created'); setShowCreate(false); form.reset({ read_only: false }); qc.invalidateQueries({ queryKey: ['maintenance-windows'] }) },
    onError: () => toast.error('Failed to create maintenance window'),
  })

  const patchMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/maintenance-windows/${id}`, body),
    onSuccess: () => { toast.success('Updated'); qc.invalidateQueries({ queryKey: ['maintenance-windows'] }) },
    onError: () => toast.error('Failed to update'),
  })

  const columns: Column<MaintenanceWindow>[] = [
    { key: 'scheduled_start', header: 'Start', render: (w) => formatDate(w.scheduled_start) },
    { key: 'scheduled_end', header: 'End', render: (w) => formatDate(w.scheduled_end) },
    { key: 'description', header: 'Description' },
    { key: 'scope', header: 'Scope', render: (w) => w.deployment_id ? 'Single deployment' : 'Fleet-wide' },
    { key: 'read_only', header: 'Mode', render: (w) => w.read_only ? <Badge variant="red">Read-only</Badge> : <span className="text-muted text-xs">Banner only</span> },
    { key: 'status', header: 'Status', render: (w) => <Badge variant={STATUS_VARIANT[w.status]}>{w.status.replace('_', ' ')}</Badge> },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (w) => (
        <div className="flex justify-end gap-1">
          {w.status === 'in_progress' && (
            <Button size="sm" variant="ghost" onClick={() => patchMutation.mutate({ id: w.id, body: { status: 'completed' } })}>Complete</Button>
          )}
          {(w.status === 'planned' || w.status === 'in_progress') && (
            <Button size="sm" variant="ghost" onClick={() => patchMutation.mutate({ id: w.id, body: { status: 'cancelled' } })}>Cancel</Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Maintenance Windows</h1>
        <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Window</Button>
      </div>
      <DataTable columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(w) => w.id} emptyMessage="No maintenance windows scheduled." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Maintenance Window" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={form.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input type="datetime-local" label="Start" {...form.register('scheduled_start', { required: true })} />
          <Input type="datetime-local" label="End" {...form.register('scheduled_end', { required: true })} />
          <Input label="Description" placeholder="e.g. Fleet-wide DB maintenance" {...form.register('description', { required: true })} />
          <label className="flex items-start gap-2 text-sm text-text">
            <input type="checkbox" className="mt-0.5" {...form.register('read_only')} />
            <span>
              Read-only mode
              <span className="block text-xs text-muted">
                Blocks all writes on the deployment (returns 503) while the window is active.
                Leave off for a banner-only heads-up.
              </span>
            </span>
          </label>
          <p className="text-xs text-muted">Fleet-wide only for now — leave deployment unset. Clients see an in-app banner; their admins get an email once Phase 2 ships.</p>
        </form>
      </Modal>
    </div>
  )
}
