import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import axios from 'axios'
import { Plus } from 'lucide-react'
import api from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import type { WorkflowDefinition } from '../../types'

type NewWorkflowForm = { key: string; name: string; description: string }

function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}

export default function WorkflowListPage() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)

  const { data, isLoading } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => api.get<WorkflowDefinition[]>('/workflows').then((r) => r.data),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => api.patch(`/workflows/${id}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workflows'] }),
    onError: (e) => toast.error(errorDetail(e) || 'Failed to update workflow'),
  })

  const [showCreate, setShowCreate] = useState(false)
  const form = useForm<NewWorkflowForm>()
  const createMutation = useMutation({
    mutationFn: (v: NewWorkflowForm) => api.post('/workflows', { key: v.key, name: v.name, description: v.description || null }),
    onSuccess: () => {
      toast.success('Workflow created')
      setShowCreate(false)
      form.reset({ key: '', name: '', description: '' })
      qc.invalidateQueries({ queryKey: ['workflows'] })
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to create workflow'),
  })

  const columns: Column<WorkflowDefinition>[] = [
    {
      key: 'name', header: 'Name',
      render: (w) => (
        <Link to={`/workflows/${w.id}`} className="font-medium text-text hover:text-accent">
          {w.name}
        </Link>
      ),
    },
    { key: 'key', header: 'Key', render: (w) => <span className="font-mono text-xs text-muted">{w.key}</span> },
    { key: 'is_system', header: 'Type', render: (w) => <Badge variant={w.is_system ? 'blue' : 'gray'}>{w.is_system ? 'Built-in' : 'Custom'}</Badge> },
    {
      key: 'is_active', header: 'Status',
      render: (w) => (
        <button
          disabled={!can('workflows.update') || toggleMutation.isPending}
          onClick={() => toggleMutation.mutate({ id: w.id, is_active: !w.is_active })}
          className="disabled:cursor-not-allowed"
        >
          <Badge variant={w.is_active ? 'green' : 'gray'}>{w.is_active ? 'Active' : 'Inactive'}</Badge>
        </button>
      ),
    },
    { key: 'description', header: 'Description', render: (w) => <span className="text-muted">{w.description ?? '—'}</span> },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-xl font-semibold text-text">Workflows</h1>
        {can('workflows.create') && (
          <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Workflow</Button>
        )}
      </div>
      <p className="text-sm text-muted mb-4">
        BPMN-backed approval flows. The built-in deployment renew/suspend/change-plan flows can be turned off here
        without deleting them — toggle "Active" to fall back to immediate execution.
      </p>

      <DataTable columns={columns} data={data ?? []} loading={isLoading} keyExtractor={(w) => w.id} emptyMessage="No workflows yet." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Workflow" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={form.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input label="Key" placeholder="e.g. custom_approval" {...form.register('key', { required: true, pattern: /^[a-z0-9_]+$/ })} />
          <Input label="Name" {...form.register('name', { required: true })} />
          <Input label="Description" {...form.register('description')} />
        </form>
      </Modal>
    </div>
  )
}
