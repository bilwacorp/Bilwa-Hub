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

type NewWindowForm = { scheduled_start: string; scheduled_end: string; description: string }

export default function MaintenanceWindowsPage() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['maintenance-windows'],
    queryFn: () => api.get<MaintenanceWindowListResponse>('/maintenance-windows').then((r) => r.data),
  })

  const [showCreate, setShowCreate] = useState(false)
  const form = useForm<NewWindowForm>()
  const createMutation = useMutation({
    mutationFn: (v: NewWindowForm) => api.post('/maintenance-windows', v),
    onSuccess: () => { toast.success('Maintenance window created'); setShowCreate(false); form.reset(); qc.invalidateQueries({ queryKey: ['maintenance-windows'] }) },
    onError: () => toast.error('Failed to create maintenance window'),
  })

  const cancelMutation = useMutation({
    mutationFn: (id: string) => api.patch(`/maintenance-windows/${id}`, { status: 'cancelled' }),
    onSuccess: () => { toast.success('Cancelled'); qc.invalidateQueries({ queryKey: ['maintenance-windows'] }) },
    onError: () => toast.error('Failed to cancel'),
  })

  const columns: Column<MaintenanceWindow>[] = [
    { key: 'scheduled_start', header: 'Start', render: (w) => formatDate(w.scheduled_start) },
    { key: 'scheduled_end', header: 'End', render: (w) => formatDate(w.scheduled_end) },
    { key: 'description', header: 'Description' },
    { key: 'scope', header: 'Scope', render: (w) => w.deployment_id ? 'Single deployment' : 'Fleet-wide' },
    { key: 'status', header: 'Status', render: (w) => <Badge variant={STATUS_VARIANT[w.status]}>{w.status.replace('_', ' ')}</Badge> },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (w) => w.status === 'planned' && (
        <Button size="sm" variant="ghost" onClick={() => cancelMutation.mutate(w.id)}>Cancel</Button>
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
          <p className="text-xs text-muted">Fleet-wide only for now — leave deployment unset. No automation or reminders are sent.</p>
        </form>
      </Modal>
    </div>
  )
}
