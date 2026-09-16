import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import axios from 'axios'
import { Plus, KeyRound } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { PasswordInput } from '../../components/ui/PasswordInput'
import { Select } from '../../components/ui/Select'
import { Button } from '../../components/ui/Button'
import type { Role, StaffUser, StaffUserListResponse } from '../../types'

type NewStaffForm = { username: string; full_name: string; email: string; phone: string; password: string; role: string }
type ResetPasswordForm = { new_password: string }

function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}

export default function StaffUsersPage() {
  const qc = useQueryClient()
  const currentUserId = useAuthStore((s) => s.user?.id)

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
      username: v.username, full_name: v.full_name || null, email: v.email || null, phone: v.phone || null,
      password: v.password, role: v.role,
    }),
    onSuccess: () => {
      toast.success('Staff account created')
      setShowCreate(false)
      createForm.reset({ username: '', full_name: '', email: '', phone: '', password: '', role: '' })
      qc.invalidateQueries({ queryKey: ['staff-users'] })
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to create staff account'),
  })

  const patchMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) => api.patch(`/users/${id}`, body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['staff-users'] }) },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to update staff account'),
  })

  const [resetTarget, setResetTarget] = useState<StaffUser | null>(null)
  const resetForm = useForm<ResetPasswordForm>()
  const resetMutation = useMutation({
    mutationFn: ({ id, body }: { id: string; body: ResetPasswordForm }) => api.post(`/users/${id}/reset-password`, body),
    onSuccess: () => { toast.success('Password reset'); setResetTarget(null); resetForm.reset() },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to reset password'),
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
          disabled={patchMutation.isPending}
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
          disabled={patchMutation.isPending}
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
            disabled={isSelf || patchMutation.isPending}
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
        return (
          <div className="flex justify-end gap-1">
            <Button size="sm" variant="ghost" icon={<KeyRound size={14} />} onClick={() => setResetTarget(u)}>Reset password</Button>
            <Button
              size="sm" variant="ghost"
              disabled={isSelf}
              title={isSelf ? 'Ask another admin to deactivate your account' : undefined}
              onClick={() => patchMutation.mutate({ id: u.id, body: { is_active: !u.is_active } })}
            >
              {u.is_active ? 'Deactivate' : 'Reactivate'}
            </Button>
          </div>
        )
      },
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Staff</h1>
        <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Staff Account</Button>
      </div>
      <DataTable columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(u) => u.id} emptyMessage="No staff accounts yet." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Staff Account" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={createForm.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input label="Username" {...createForm.register('username', { required: true })} />
          <Input label="Full name" {...createForm.register('full_name')} />
          <Input label="Email" type="email" {...createForm.register('email')} />
          <Input label="Phone (WhatsApp)" placeholder="+91XXXXXXXXXX" {...createForm.register('phone')} />
          <PasswordInput label="Password" {...createForm.register('password', { required: true, minLength: 8 })} />
          <Select label="Role" placeholder="Select a role" options={roleOptions} {...createForm.register('role', { required: true })} />
        </form>
      </Modal>

      <Modal
        open={!!resetTarget} onClose={() => setResetTarget(null)} title={`Reset password — ${resetTarget?.username}`} size="sm"
        footer={<><Button variant="secondary" onClick={() => setResetTarget(null)}>Cancel</Button><Button loading={resetMutation.isPending} onClick={resetForm.handleSubmit((v) => resetTarget && resetMutation.mutate({ id: resetTarget.id, body: v }))}>Reset</Button></>}
      >
        <form className="space-y-4">
          <PasswordInput label="New password" {...resetForm.register('new_password', { required: true, minLength: 8 })} />
          <p className="text-xs text-muted">This immediately signs the user out of any existing session.</p>
        </form>
      </Modal>
    </div>
  )
}
