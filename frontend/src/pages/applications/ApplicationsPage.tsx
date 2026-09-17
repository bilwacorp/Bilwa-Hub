import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import { Plus } from 'lucide-react'
import api from '../../lib/api'
import { formatDate, errorMessage as errMsg } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Button } from '../../components/ui/Button'
import type { Application } from '../../types'

type NewApplicationForm = { name: string; slug: string; description: string }

export default function ApplicationsPage() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const { data, isLoading } = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.get<Application[]>('/applications').then((r) => r.data),
  })

  const [showCreate, setShowCreate] = useState(false)
  const form = useForm<NewApplicationForm>({ defaultValues: { name: '', slug: '', description: '' } })
  const createMutation = useMutation({
    mutationFn: (v: NewApplicationForm) => api.post('/applications', { name: v.name, slug: v.slug, description: v.description || null }),
    onSuccess: () => { toast.success('Application created'); setShowCreate(false); form.reset(); qc.invalidateQueries({ queryKey: ['applications'] }) },
    onError: (e) => toast.error(errMsg(e, 'Failed to create application')),
  })

  const columns: Column<Application>[] = [
    { key: 'name', header: 'Name' },
    { key: 'slug', header: 'Slug', className: 'font-mono text-xs' },
    { key: 'description', header: 'Description', render: (a) => a.description || '—' },
    { key: 'created_at', header: 'Created', render: (a) => formatDate(a.created_at) },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Applications</h1>
        {can('applications.manage') && (
          <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Application</Button>
        )}
      </div>
      <p className="text-sm text-muted mb-4">
        The software product a deployment runs (e.g. "PoultryOS-CBP") — set from that deployment's detail page.
      </p>
      <DataTable columns={columns} data={data ?? []} loading={isLoading} keyExtractor={(a) => a.id} emptyMessage="No applications yet." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Application" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={form.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input label="Name" placeholder="e.g. PoultryOS-CBP" {...form.register('name', { required: true })} />
          <Input label="Slug" placeholder="e.g. poultryos-cbp" {...form.register('slug', { required: true })} />
          <Input label="Description (optional)" {...form.register('description')} />
        </form>
      </Modal>
    </div>
  )
}
