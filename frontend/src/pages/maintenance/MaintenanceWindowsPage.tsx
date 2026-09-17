import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import { Plus } from 'lucide-react'
import api from '../../lib/api'
import { formatDate, errorMessage as errMsg } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Select } from '../../components/ui/Select'
import { Button } from '../../components/ui/Button'
import type {
  Deployment, DeploymentListResponse, MaintenanceMode,
  MaintenanceWindow, MaintenanceWindowListResponse, MaintenanceWindowStatus,
} from '../../types'

// HUB-Expansion.md Phase 7 widened this from 4 to 9 statuses.
const STATUS_VARIANT: Record<MaintenanceWindowStatus, 'blue' | 'amber' | 'green' | 'gray' | 'red'> = {
  draft: 'gray',
  approval_required: 'amber',
  approved: 'blue',
  planned: 'blue',
  notification: 'blue',
  in_progress: 'amber',
  completed: 'green',
  failed: 'red',
  cancelled: 'gray',
}

const MODE_OPTIONS: { value: MaintenanceMode; label: string; hint: string }[] = [
  { value: 'banner', label: 'Banner only', hint: 'Just an in-app heads-up. Nothing is blocked.' },
  { value: 'read_only', label: 'Read-only', hint: 'Blocks all writes on the deployment (503) while active. Viewing still works.' },
  { value: 'lockout', label: 'Lockout (block sign-in)', hint: 'Read-only PLUS new sign-ins are refused. Existing sessions keep working until they expire.' },
]

const MODE_BADGE: Record<MaintenanceMode, { variant: 'gray' | 'amber' | 'red'; label: string }> = {
  banner: { variant: 'gray', label: 'Banner only' },
  read_only: { variant: 'amber', label: 'Read-only' },
  lockout: { variant: 'red', label: 'Lockout' },
}

type NewWindowForm = {
  deployment_id: string
  scheduled_start: string
  scheduled_end: string
  description: string
  mode: MaintenanceMode
  expected_impact: string
  save_as_draft: boolean
}

export default function MaintenanceWindowsPage() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const { data, isLoading } = useQuery({
    queryKey: ['maintenance-windows'],
    queryFn: () => api.get<MaintenanceWindowListResponse>('/maintenance-windows').then((r) => r.data),
    refetchInterval: 30_000,
  })
  const { data: deployments } = useQuery({
    queryKey: ['deployments'],
    queryFn: () => api.get<DeploymentListResponse>('/deployments').then((r) => r.data),
  })
  const deploymentName = (id: string | null) =>
    id ? (deployments?.items.find((d) => d.id === id)?.client_name ?? 'Single deployment') : 'Fleet-wide'

  const [showCreate, setShowCreate] = useState(false)
  const form = useForm<NewWindowForm>({ defaultValues: { mode: 'banner', deployment_id: '', save_as_draft: false } })
  const createMutation = useMutation({
    // datetime-local gives a naive *local* string; the backend stores naive
    // UTC — convert here so a window scheduled for "14:00" means 14:00 the
    // operator's time, not 14:00 UTC.
    mutationFn: (v: NewWindowForm) => api.post('/maintenance-windows', {
      deployment_id: v.deployment_id || null,
      description: v.description,
      mode: v.mode,
      expected_impact: v.expected_impact || null,
      save_as_draft: v.save_as_draft,
      scheduled_start: new Date(v.scheduled_start).toISOString(),
      scheduled_end: new Date(v.scheduled_end).toISOString(),
    }),
    onSuccess: (_data, v) => {
      toast.success(v.save_as_draft ? 'Draft saved' : 'Maintenance window created')
      setShowCreate(false)
      form.reset({ mode: 'banner', deployment_id: '', save_as_draft: false })
      qc.invalidateQueries({ queryKey: ['maintenance-windows'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to create maintenance window')),
  })

  const patchMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/maintenance-windows/${id}`, body),
    onSuccess: () => { toast.success('Updated'); qc.invalidateQueries({ queryKey: ['maintenance-windows'] }) },
    onError: () => toast.error('Failed to update'),
  })

  const submitMutation = useMutation({
    mutationFn: (id: string) => api.post(`/maintenance-windows/${id}/submit`),
    onSuccess: (r) => {
      const status = (r.data as MaintenanceWindow).status
      toast.success(status === 'approval_required' ? 'Submitted for approval' : 'Window scheduled')
      qc.invalidateQueries({ queryKey: ['maintenance-windows'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to submit')),
  })

  const columns: Column<MaintenanceWindow>[] = [
    { key: 'scheduled_start', header: 'Start', render: (w) => formatDate(w.scheduled_start) },
    { key: 'scheduled_end', header: 'End', render: (w) => formatDate(w.scheduled_end) },
    { key: 'description', header: 'Description' },
    { key: 'scope', header: 'Scope', render: (w) => deploymentName(w.deployment_id) },
    { key: 'mode', header: 'Mode', render: (w) => {
      const b = MODE_BADGE[w.mode] ?? MODE_BADGE.banner
      return <Badge variant={b.variant}>{b.label}</Badge>
    } },
    { key: 'status', header: 'Status', render: (w) => <Badge variant={STATUS_VARIANT[w.status]}>{w.status.replace('_', ' ')}</Badge> },
    ...(can('maintenance.update') || can('maintenance.create') ? [{
      key: 'actions', header: '', className: 'text-right',
      render: (w: MaintenanceWindow) => (
        <div className="flex justify-end gap-1">
          {w.status === 'draft' && can('maintenance.create') && (
            <Button size="sm" variant="secondary" loading={submitMutation.isPending} onClick={() => submitMutation.mutate(w.id)}>Submit</Button>
          )}
          {can('maintenance.update') && w.status === 'in_progress' && (
            <Button size="sm" variant="ghost" onClick={() => patchMutation.mutate({ id: w.id, body: { status: 'completed' } })}>Complete</Button>
          )}
          {can('maintenance.update') && ['draft', 'approved', 'planned', 'notification', 'in_progress'].includes(w.status) && (
            <Button size="sm" variant="ghost" onClick={() => patchMutation.mutate({ id: w.id, body: { status: 'cancelled' } })}>Cancel</Button>
          )}
        </div>
      ),
    }] : []),
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Maintenance Windows</h1>
        {can('maintenance.create') && (
          <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Window</Button>
        )}
      </div>
      <DataTable columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(w) => w.id} emptyMessage="No maintenance windows scheduled." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Maintenance Window" size="sm"
        footer={<>
          <Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button>
          <Button loading={createMutation.isPending} onClick={form.handleSubmit((v) => createMutation.mutate(v))}>
            {form.watch('save_as_draft') ? 'Save Draft' : 'Create'}
          </Button>
        </>}
      >
        <form className="space-y-4">
          <Select
            label="Applies to"
            placeholder="Fleet-wide (all deployments)"
            options={(deployments?.items ?? [])
              .filter((d: Deployment) => d.status === 'active')
              .map((d: Deployment) => ({ value: d.id, label: d.client_name }))}
            {...form.register('deployment_id')}
          />
          <Input type="datetime-local" label="Start" {...form.register('scheduled_start', { required: true })} />
          <Input type="datetime-local" label="End" {...form.register('scheduled_end', { required: true })} />
          <Input label="Description" placeholder="e.g. Database upgrade" {...form.register('description', { required: true })} />
          <Input label="Expected impact (optional)" placeholder="e.g. Brief API downtime" {...form.register('expected_impact')} />
          <div>
            <Select
              label="Enforcement"
              options={MODE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              {...form.register('mode')}
            />
            <p className="mt-1 text-xs text-muted">
              {MODE_OPTIONS.find((o) => o.value === form.watch('mode'))?.hint}
            </p>
          </div>
          {/* HUB-Expansion.md Phase 7/8 — mirrors app/approvals/maintenance_hooks
              .py's _is_high_risk exactly (fleet-wide OR lockout). */}
          {(!form.watch('deployment_id') || form.watch('mode') === 'lockout') && !form.watch('save_as_draft') && (
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1.5">
              Fleet-wide or lockout windows need admin approval before they're scheduled.
            </p>
          )}
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input type="checkbox" {...form.register('save_as_draft')} />
            Save as draft (submit for scheduling later)
          </label>
          <p className="text-xs text-muted">Clients see an in-app banner immediately; their admins also get an email.</p>
        </form>
      </Modal>
    </div>
  )
}
