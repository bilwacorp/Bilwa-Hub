import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import { Plus } from 'lucide-react'
import api from '../../lib/api'
import { formatDate, errorMessage as errorDetail } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Select } from '../../components/ui/Select'
import { Button } from '../../components/ui/Button'
import type { Role, StaffUser, StaffUserListResponse } from '../../types'

// Staff sign in via Authentik SSO (docs/adr/ADR-012-authentik-sso.md) —
// there's no password here to set or reset. `email` is required: it's the
// only field a login is matched against, so a row created without one
// could never sign in.
type NewStaffForm = { username: string; full_name: string; email: string; phone: string; role: string }

export default function StaffUsersPage() {
  const qc = useQueryClient()
  const currentUserId = useAuthStore((s) => s.user?.id)
  const can = useAuthStore((s) => s.can)
  const canUpdate = can('staff.update')

  const { data, isLoading } = useQuery({
    queryKey: ['staff-users'],
    queryFn: () => api.get<StaffUserListResponse>('/users').then((r) => r.data),
  })

  const { data: roles } = useQuery({
    queryKey: ['roles'],
    queryFn: () => api.get<Role[]>('/rbac/roles').then((r) => r.data),
  })
  const roleOptions = (roles ?? []).map((r) => ({ value: r.name, label: r.description ? `${r.name} — ${r.description}` : r.name }))

  const [showCreate, setShowCreate] = useState(false)
  const createForm = useForm<NewStaffForm>()
  const createMutation = useMutation({
    mutationFn: (v: NewStaffForm) => api.post('/users', {
      username: v.username, full_name: v.full_name || null, email: v.email, phone: v.phone || null,
      role: v.role,
    }),
    onSuccess: () => {
      toast.success('Staff account created')
      setShowCreate(false)
      createForm.reset({ username: '', full_name: '', email: '', phone: '', role: '' })
      qc.invalidateQueries({ queryKey: ['staff-users'] })
    },
    onError: (e) => toast.error(errorDetail(e, 'Failed to create staff account')),
  })

  const patchMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/users/${id}`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['staff-users'] }) },
    onError: (e) => toast.error(errorDetail(e, 'Failed to update staff account')),
  })

  const columns: Column<StaffUser>[] = [
    { key: 'username', header: 'Username' },
    { key: 'full_name', header: 'Full name', render: (u) => u.full_name ?? '—' },
    {
      key: 'email', header: 'Email',
      render: (u) => (
        <Input
          className="h-8 text-xs w-44"
          type="email"
          defaultValue={u.email ?? ''}
          placeholder="name@example.com"
          disabled={!canUpdate || patchMutation.isPending}
          onBlur={(e) => {
            const v = e.target.value.trim()
            if (v !== (u.email ?? '')) patchMutation.mutate({ id: u.id, body: { email: v || null } })
          }}
        />
      ),
    },
    {
      key: 'phone', header: 'Phone (WhatsApp)',
      render: (u) => (
        <Input
          className="h-8 text-xs w-36"
          defaultValue={u.phone ?? ''}
          placeholder="+91XXXXXXXXXX"
          disabled={!canUpdate || patchMutation.isPending}
          onBlur={(e) => {
            const v = e.target.value.trim()
            if (v !== (u.phone ?? '')) patchMutation.mutate({ id: u.id, body: { phone: v || null } })
          }}
        />
      ),
    },
    {
      key: 'role', header: 'Role',
      render: (u) => {
        const isSelf = u.id === currentUserId
        return (
          <Select
            className="h-8 text-xs w-auto"
            value={u.role ?? ''}
            disabled={!canUpdate || isSelf || patchMutation.isPending}
            options={roleOptions}
            onChange={(e) => patchMutation.mutate({ id: u.id, body: { role: e.target.value } })}
            aria-label={`Role for ${u.username}`}
            title={isSelf ? 'Ask another admin to change your own role' : undefined}
          />
        )
      },
    },
    {
      key: 'is_active', header: 'Status',
      render: (u) => <Badge variant={u.is_active ? 'green' : 'gray'}>{u.is_active ? 'Active' : 'Inactive'}</Badge>,
    },
    { key: 'created_at', header: 'Added', render: (u) => formatDate(u.created_at) },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (u) => {
        const isSelf = u.id === currentUserId
        return canUpdate ? (
          <div className="flex justify-end gap-1">
            <Button
              size="sm" variant="ghost"
              disabled={isSelf}
              title={isSelf ? 'Ask another admin to deactivate your account' : undefined}
              onClick={() => patchMutation.mutate({ id: u.id, body: { is_active: !u.is_active } })}
            >
              {u.is_active ? 'Deactivate' : 'Reactivate'}
            </Button>
          </div>
        ) : null
      },
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Staff</h1>
        {can('staff.create') && (
          <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Staff Account</Button>
        )}
      </div>
      <DataTable columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(u) => u.id} emptyMessage="No staff accounts yet." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Staff Account" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={createForm.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input label="Username" {...createForm.register('username', { required: true })} />
          <Input label="Full name" {...createForm.register('full_name')} />
          <Input
            label="Email" type="email" {...createForm.register('email', { required: true })}
          />
          <p className="text-xs text-muted -mt-3">Must match this person's Authentik sign-in email.</p>
          <Input label="Phone (WhatsApp)" placeholder="+91XXXXXXXXXX" {...createForm.register('phone')} />
          <Select label="Role" placeholder="Select a role" options={roleOptions} {...createForm.register('role', { required: true })} />
        </form>
      </Modal>
    </div>
  )
}
