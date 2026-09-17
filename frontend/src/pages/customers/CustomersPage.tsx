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
import type { Customer } from '../../types'

type NewCustomerForm = { name: string; slug: string; notes: string }

export default function CustomersPage() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const { data, isLoading } = useQuery({
    queryKey: ['customers'],
    queryFn: () => api.get<Customer[]>('/customers').then((r) => r.data),
  })

  const [showCreate, setShowCreate] = useState(false)
  const form = useForm<NewCustomerForm>({ defaultValues: { name: '', slug: '', notes: '' } })
  const createMutation = useMutation({
    mutationFn: (v: NewCustomerForm) => api.post('/customers', { name: v.name, slug: v.slug, notes: v.notes || null }),
    onSuccess: () => { toast.success('Customer created'); setShowCreate(false); form.reset(); qc.invalidateQueries({ queryKey: ['customers'] }) },
    onError: (e) => toast.error(errMsg(e, 'Failed to create customer')),
  })

  const columns: Column<Customer>[] = [
    { key: 'name', header: 'Name' },
    { key: 'slug', header: 'Slug', className: 'font-mono text-xs' },
    { key: 'notes', header: 'Notes', render: (c) => c.notes || '—' },
    { key: 'created_at', header: 'Created', render: (c) => formatDate(c.created_at) },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Customers</h1>
        {can('customers.manage') && (
          <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Customer</Button>
        )}
      </div>
      <p className="text-sm text-muted mb-4">
        Groups deployments by the organization they belong to (HUB-Expansion.md Phase 4). A deployment's own
        client name is unaffected — this is an additional lineage link, set from that deployment's detail page.
      </p>
      <DataTable columns={columns} data={data ?? []} loading={isLoading} keyExtractor={(c) => c.id} emptyMessage="No customers yet." />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Customer" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={form.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input label="Name" placeholder="e.g. Acme Farms" {...form.register('name', { required: true })} />
          <Input label="Slug" placeholder="e.g. acme-farms" {...form.register('slug', { required: true })} />
          <Input label="Notes (optional)" {...form.register('notes')} />
        </form>
      </Modal>
    </div>
  )
}
