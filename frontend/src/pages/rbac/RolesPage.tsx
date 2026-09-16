import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import axios from 'axios'
import { Plus, ShieldCheck, Trash2 } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import type { Role } from '../../types'

type NewRoleForm = { name: string; description: string }

function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}

export default function RolesPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data, isLoading } = useQuery({
    queryKey: ['roles'],
    queryFn: () => api.get<Role[]>('/rbac/roles').then((r) => r.data),
  })

  const [showCreate, setShowCreate] = useState(false)
  const createForm = useForm<NewRoleForm>()
  const createMutation = useMutation({
    mutationFn: (v: NewRoleForm) => api.post('/rbac/roles', { name: v.name, description: v.description || null }),
    onSuccess: (res) => {
      toast.success('Role created')
      setShowCreate(false)
      createForm.reset()
      qc.invalidateQueries({ queryKey: ['roles'] })
      navigate(`/roles/${res.data.id}/permissions`)
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to create role'),
  })

  const [deleteTarget, setDeleteTarget] = useState<Role | null>(null)
  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/rbac/roles/${id}`),
    onSuccess: () => { toast.success('Role deleted'); setDeleteTarget(null); qc.invalidateQueries({ queryKey: ['roles'] }) },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to delete role'),
  })

  const columns: Column<Role>[] = [
    { key: 'name', header: 'Name', render: (r) => <span className="font-mono text-sm">{r.name}</span> },
    { key: 'description', header: 'Description', render: (r) => r.description ?? '—' },
    {
      key: 'type', header: 'Type',
      render: (r) => <Badge variant={r.is_system ? 'blue' : 'gray'}>{r.is_system ? 'Built-in' : 'Custom'}</Badge>,
    },
    { key: 'created_at', header: 'Created', render: (r) => formatDate(r.created_at) },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (r) => (
        <div className="flex justify-end gap-1">
          <Button size="sm" variant="secondary" icon={<ShieldCheck size={14} />} onClick={() => navigate(`/roles/${r.id}/permissions`)}>
            Permissions
          </Button>
          {!r.is_system && (
            <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => setDeleteTarget(r)} />
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-semibold text-text">Roles & Permissions</h1>
          <p className="text-sm text-muted mt-1">Built-in roles' permissions can be edited but not renamed or deleted.</p>
        </div>
        <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Role</Button>
      </div>
      <DataTable columns={columns} data={data ?? []} loading={isLoading} keyExtractor={(r) => r.id} emptyMessage="No roles yet." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Role" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={createForm.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input
            label="Name" placeholder="e.g. support_engineer"
            helper="Lowercase letters, digits, and underscores only."
            error={createForm.formState.errors.name?.message}
            {...createForm.register('name', { required: true, pattern: { value: /^[a-z0-9_]+$/, message: 'Lowercase letters, digits, underscores only' } })}
          />
          <Input label="Description (optional)" {...createForm.register('description')} />
          <p className="text-xs text-muted">You'll pick this role's permissions on the next screen.</p>
        </form>
      </Modal>

      <Modal
        open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title={`Delete role — ${deleteTarget?.name}`} size="sm"
        footer={<><Button variant="secondary" onClick={() => setDeleteTarget(null)}>Cancel</Button><Button variant="danger" loading={deleteMutation.isPending} onClick={() => deleteTarget && deleteMutation.mutate(deleteTarget.id)}>Delete</Button></>}
      >
        <p className="text-sm text-text">
          Any staff member assigned only this role will lose all access until reassigned. This can't be undone.
        </p>
      </Modal>
    </div>
  )
}
