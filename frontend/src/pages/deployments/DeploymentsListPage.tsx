import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { Plus, Copy } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import type { Deployment, DeploymentListResponse, DeploymentCreateOut } from '../../types'

function healthDot(d: Deployment): { color: string; label: string } {
  if (!d.latest_snapshot) return { color: 'bg-hint', label: 'No heartbeat yet' }
  const receivedAt = new Date(d.latest_snapshot.received_at).getTime()
  const hoursSince = (Date.now() - receivedAt) / (1000 * 60 * 60)
  // Heartbeats are every 2h — anything over 2x that interval without a
  // fresh one is worth flagging as stale rather than assumed-healthy.
  if (hoursSince > 4) return { color: 'bg-danger', label: `Last heartbeat ${formatDate(d.latest_snapshot.received_at)}` }
  return { color: 'bg-success', label: `Last heartbeat ${formatDate(d.latest_snapshot.received_at)}` }
}

export default function DeploymentsListPage() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['deployments'],
    queryFn: () => api.get<DeploymentListResponse>('/deployments').then((r) => r.data),
    refetchInterval: 60_000,
  })

  const [showCreate, setShowCreate] = useState(false)
  const [createdInfo, setCreatedInfo] = useState<DeploymentCreateOut | null>(null)
  const form = useForm<{ client_name: string; slug: string }>()
  const createMutation = useMutation({
    mutationFn: (v: { client_name: string; slug: string }) => api.post<DeploymentCreateOut>('/deployments', v).then((r) => r.data),
    onSuccess: (out) => {
      setShowCreate(false)
      form.reset()
      setCreatedInfo(out)
      qc.invalidateQueries({ queryKey: ['deployments'] })
    },
    onError: () => toast.error('Failed to create deployment'),
  })

  const columns: Column<Deployment>[] = [
    { key: 'client_name', header: 'Client', render: (d) => <span className="font-semibold">{d.client_name}</span> },
    { key: 'slug', header: 'Slug', render: (d) => <span className="font-mono text-xs">{d.slug}</span> },
    {
      key: 'status', header: 'Status',
      render: (d) => <Badge variant={d.status === 'active' ? 'green' : d.status === 'suspended' ? 'red' : 'gray'}>{d.status}</Badge>,
    },
    { key: 'plan', header: 'Plan', render: (d) => d.latest_snapshot?.plan_name ?? '—' },
    { key: 'expiry', header: 'Expiry', render: (d) => formatDate(d.latest_snapshot?.expiry_date) },
    {
      key: 'health', header: 'Health',
      render: (d) => {
        const h = healthDot(d)
        return <span className="inline-flex items-center gap-2"><span className={`h-2 w-2 rounded-full ${h.color}`} />{h.label}</span>
      },
    },
    {
      key: 'requests', header: 'Pending Requests',
      render: (d) => (d.latest_snapshot?.pending_requests?.length ?? 0) > 0
        ? <Badge variant="amber">{d.latest_snapshot!.pending_requests!.length}</Badge> : '—',
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-semibold text-text">Deployments</h1>
        <Button icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>New Deployment</Button>
      </div>
      <DataTable
        columns={columns} data={data?.items ?? []} loading={isLoading} keyExtractor={(d) => d.id}
        emptyMessage="No deployments yet — create one to get a registration token."
        onRowClick={(d) => navigate(`/deployments/${d.id}`)}
      />

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New Deployment" size="sm"
        footer={<>
          <Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button>
          <Button loading={createMutation.isPending} onClick={form.handleSubmit((v) => createMutation.mutate(v))}>Create</Button>
        </>}
      >
        <form className="space-y-4">
          <Input label="Client name" placeholder="e.g. Shiva Agrovet" {...form.register('client_name', { required: true })} />
          <Input label="Slug" placeholder="e.g. shiva-cbp" {...form.register('slug', { required: true })} />
        </form>
      </Modal>

      <Modal
        open={!!createdInfo} onClose={() => setCreatedInfo(null)} title="Deployment created" size="sm"
        footer={<Button onClick={() => setCreatedInfo(null)}>Done</Button>}
      >
        {createdInfo && (
          <div className="space-y-3 text-sm">
            <p className="text-muted">
              Set these two values in this client's Dokploy Environment tab (celery-worker service),
              then redeploy. This token is single-use and shown only once.
            </p>
            <div className="space-y-1">
              <div className="text-xs text-muted">HUB_REGISTRATION_TOKEN</div>
              <div className="flex items-center gap-2 font-mono text-xs bg-background rounded p-2 break-all">
                {createdInfo.registration_token}
                <button onClick={() => { navigator.clipboard.writeText(createdInfo.registration_token); toast.success('Copied') }}>
                  <Copy size={14} />
                </button>
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}
