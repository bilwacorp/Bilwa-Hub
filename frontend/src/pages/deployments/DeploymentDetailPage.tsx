import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import axios from 'axios'
import { RotateCw } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import type { Deployment, StaffOption } from '../../types'

function errMsg(err: unknown, fallback: string) {
  if (axios.isAxiosError(err)) return (err.response?.data as { detail?: string } | undefined)?.detail || fallback
  return fallback
}

interface HealthCheckResult {
  api: boolean
  database: boolean
  api_latency_ms: number | null
  db_latency_ms: number | null
  checked_at: string
}

function HealthRow({ label, ok, latencyMs }: { label: string; ok: boolean; latencyMs: number | null }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted flex items-center gap-2">
        <span className={`h-2 w-2 rounded-full ${ok ? 'bg-success' : 'bg-danger'}`} />
        {label}
      </span>
      <span className={ok ? 'text-text' : 'text-danger font-medium'}>
        {ok ? `${latencyMs ?? '—'}ms` : 'Unreachable'}
      </span>
    </div>
  )
}

export default function DeploymentDetailPage() {
  const { deploymentId } = useParams<{ deploymentId: string }>()
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)

  const { data: d } = useQuery({
    queryKey: ['deployment', deploymentId],
    queryFn: () => api.get<Deployment>(`/deployments/${deploymentId}`).then((r) => r.data),
    refetchInterval: 30_000,
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['deployment', deploymentId] })

  const canAssignStaff = can('deployments.assign_staff')
  const { data: staffOptions } = useQuery({
    queryKey: ['staff-options'],
    queryFn: () => api.get<StaffOption[]>('/deployments/staff-options').then((r) => r.data),
    enabled: canAssignStaff,
  })

  // Local, editable copy of the assignment set — initialized once from the
  // server (not on every 30s refetch, which would clobber an in-progress
  // edit). null = not yet loaded.
  const [selectedStaffIds, setSelectedStaffIds] = useState<string[] | null>(null)
  useEffect(() => {
    if (d && selectedStaffIds === null) setSelectedStaffIds(d.assigned_staff.map((s) => s.id))
  }, [d, selectedStaffIds])

  const assignStaffMutation = useMutation({
    mutationFn: (user_ids: string[]) => api.put(`/deployments/${deploymentId}/staff`, { user_ids }),
    onSuccess: () => { toast.success('Assigned staff updated'); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to update assigned staff')),
  })

  const savedStaffIds = d?.assigned_staff.map((s) => s.id).slice().sort() ?? []
  const staffDirty = selectedStaffIds !== null
    && JSON.stringify(selectedStaffIds.slice().sort()) !== JSON.stringify(savedStaffIds)

  const [showRenew, setShowRenew] = useState(false)
  const [showSuspend, setShowSuspend] = useState(false)
  const [showChangePlan, setShowChangePlan] = useState(false)
  const [showExtend, setShowExtend] = useState(false)

  const renewForm = useForm<{ new_expiry_date: string; renewal_amount?: number }>()
  const renewMutation = useMutation({
    mutationFn: (v: { new_expiry_date: string; renewal_amount?: number }) => api.post(`/deployments/${deploymentId}/actions/renew`, v),
    onSuccess: () => { toast.success('Subscription renewed'); setShowRenew(false); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to renew')),
  })

  const suspendForm = useForm<{ reason: string }>()
  const suspendMutation = useMutation({
    mutationFn: (v: { reason: string }) => api.post(`/deployments/${deploymentId}/actions/suspend`, v),
    onSuccess: () => { toast.success('Subscription suspended'); setShowSuspend(false); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to suspend')),
  })

  const changePlanForm = useForm<{ new_plan_id: string }>()
  const changePlanMutation = useMutation({
    mutationFn: (v: { new_plan_id: string }) => api.post(`/deployments/${deploymentId}/actions/change-plan`, v),
    onSuccess: () => { toast.success('Plan changed'); setShowChangePlan(false); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to change plan')),
  })

  const extendForm = useForm<{ new_expiry_date: string }>()
  const extendMutation = useMutation({
    mutationFn: (v: { new_expiry_date: string }) => api.post(`/deployments/${deploymentId}/actions/extend-expiry`, v),
    onSuccess: () => { toast.success('Expiry extended'); setShowExtend(false); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to extend expiry')),
  })

  const reviewMutation = useMutation({
    mutationFn: (v: { requestId: string; status: string }) =>
      api.patch(`/deployments/${deploymentId}/subscription-requests/${v.requestId}`, { status: v.status }),
    onSuccess: () => { toast.success('Request reviewed'); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to review request')),
  })

  const healthCheckMutation = useMutation({
    mutationFn: () => api.post<HealthCheckResult>(`/deployments/${deploymentId}/actions/check-health`).then((r) => r.data),
    onError: (e) => toast.error(errMsg(e, 'Failed to reach deployment')),
  })
  const health = healthCheckMutation.data

  if (!d) return <div className="p-6 text-muted">Loading…</div>

  const snap = d.latest_snapshot

  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-text">{d.client_name}</h1>
        <p className="text-sm text-muted font-mono">{d.slug} · {d.base_url ?? 'not yet registered'}</p>
      </div>

      {canAssignStaff ? (
        <div className="bg-surface border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-text">Assigned Staff</h3>
            {staffDirty && (
              <div className="flex gap-2">
                <Button size="sm" variant="ghost" onClick={() => setSelectedStaffIds(savedStaffIds)}>Cancel</Button>
                <Button
                  size="sm" loading={assignStaffMutation.isPending}
                  onClick={() => selectedStaffIds && assignStaffMutation.mutate(selectedStaffIds)}
                >
                  Save
                </Button>
              </div>
            )}
          </div>
          <p className="text-xs text-muted mb-3">
            Ticket and renewal/upgrade-request alerts for this deployment go only to staff checked here.
            Leave nothing checked to notify every fleet staff member instead (the default).
          </p>
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            {(staffOptions ?? []).map((s) => (
              <label key={s.id} className="flex items-center gap-2 text-sm cursor-pointer">
                <input
                  type="checkbox"
                  checked={selectedStaffIds?.includes(s.id) ?? false}
                  onChange={(e) => {
                    setSelectedStaffIds((prev) => {
                      const base = prev ?? []
                      return e.target.checked ? [...base, s.id] : base.filter((id) => id !== s.id)
                    })
                  }}
                />
                {s.full_name || s.username}
              </label>
            ))}
            {staffOptions?.length === 0 && <p className="text-sm text-muted">No staff accounts yet.</p>}
          </div>
        </div>
      ) : d.assigned_staff.length > 0 && (
        <div className="bg-surface border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text mb-2">Assigned Staff</h3>
          <p className="text-sm text-text">{d.assigned_staff.map((s) => s.full_name || s.username).join(', ')}</p>
        </div>
      )}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="bg-surface border border-border rounded-lg p-4">
          <div className="text-xs text-muted mb-1">Status</div>
          <Badge variant={d.status === 'active' ? 'green' : d.status === 'suspended' ? 'red' : 'gray'}>{d.status}</Badge>
        </div>
        <div className="bg-surface border border-border rounded-lg p-4">
          <div className="text-xs text-muted mb-1">Plan</div>
          <div className="font-semibold">{snap?.plan_name ?? '—'}</div>
        </div>
        <div className="bg-surface border border-border rounded-lg p-4">
          <div className="text-xs text-muted mb-1">Expiry</div>
          <div className="font-semibold">{formatDate(snap?.expiry_date)}</div>
        </div>
        <div className="bg-surface border border-border rounded-lg p-4">
          <div className="text-xs text-muted mb-1">Last heartbeat</div>
          <div className="font-semibold">{snap ? formatDate(snap.received_at) : '—'}</div>
        </div>
      </div>

      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text">Live Health Check</h3>
          {can('deployments.check_health') && (
            <Button
              size="sm" variant="secondary"
              icon={<RotateCw size={13} />}
              loading={healthCheckMutation.isPending}
              onClick={() => healthCheckMutation.mutate()}
            >
              Check now
            </Button>
          )}
        </div>
        {health ? (
          <div className="space-y-2">
            <HealthRow label="API Server" ok={health.api} latencyMs={health.api_latency_ms} />
            <HealthRow label="Database" ok={health.database} latencyMs={health.db_latency_ms} />
            <p className="text-xs text-muted pt-1">Checked {formatDate(health.checked_at)}</p>
          </div>
        ) : (
          <p className="text-sm text-muted">
            Probes this deployment's own /api/health right now — independent of the Health dot above,
            which only reflects the last heartbeat (up to ~2h old).
          </p>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        {can('deployments.renew') && <Button variant="secondary" onClick={() => setShowRenew(true)}>Renew</Button>}
        {can('deployments.change_plan') && <Button variant="secondary" onClick={() => setShowChangePlan(true)}>Change Plan</Button>}
        {can('deployments.extend_expiry') && <Button variant="secondary" onClick={() => setShowExtend(true)}>Extend Expiry</Button>}
        {can('deployments.suspend') && <Button variant="danger" onClick={() => setShowSuspend(true)}>Suspend</Button>}
      </div>

      {snap && (
        <div className="bg-surface border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text mb-3">Usage</h3>
          <div className="space-y-2">
            {(snap.usage ?? []).map((u) => (
              <div key={u.key} className="flex items-center justify-between text-sm">
                <span className="text-muted">{u.label}</span>
                <span>{u.current}{u.limit != null ? ` / ${u.limit}` : ' (unlimited)'}</span>
              </div>
            ))}
            {(snap.usage ?? []).length === 0 && <p className="text-sm text-muted">No plan limits configured.</p>}
          </div>
        </div>
      )}

      <div className="bg-surface border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-text mb-3">Pending Renewal / Upgrade Requests</h3>
        <div className="space-y-3">
          {(snap?.pending_requests ?? []).map((r) => (
            <div key={r.id} className="border border-border rounded p-3 flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-medium capitalize">{r.request_type}{r.requested_plan_name ? ` → ${r.requested_plan_name}` : ''}</div>
                {r.message && <div className="text-sm text-muted italic mt-1">"{r.message}"</div>}
                <div className="text-xs text-muted mt-1">{formatDate(r.requested_at)}</div>
              </div>
              {can('deployments.review_request') && (
                <div className="flex gap-2 shrink-0">
                  <Button size="sm" variant="secondary" loading={reviewMutation.isPending} onClick={() => reviewMutation.mutate({ requestId: r.id, status: 'actioned' })}>Mark Actioned</Button>
                  <Button size="sm" variant="ghost" onClick={() => reviewMutation.mutate({ requestId: r.id, status: 'rejected' })}>Reject</Button>
                </div>
              )}
            </div>
          ))}
          {(snap?.pending_requests ?? []).length === 0 && <p className="text-sm text-muted">No pending requests.</p>}
        </div>
        <p className="text-xs text-muted mt-3">
          This list reflects the last heartbeat (up to 2h old) — the review action itself is sent live to the deployment.
        </p>
      </div>

      <Modal open={showRenew} onClose={() => setShowRenew(false)} title="Renew Subscription" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowRenew(false)}>Cancel</Button><Button loading={renewMutation.isPending} onClick={renewForm.handleSubmit((v) => renewMutation.mutate(v))}>Renew</Button></>}>
        <form className="space-y-4">
          <Input type="date" label="New expiry date" {...renewForm.register('new_expiry_date', { required: true })} />
          <Input type="number" step="0.01" label="Renewal amount (optional)" {...renewForm.register('renewal_amount', { valueAsNumber: true })} />
        </form>
      </Modal>

      <Modal open={showSuspend} onClose={() => setShowSuspend(false)} title="Suspend Subscription" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowSuspend(false)}>Cancel</Button><Button variant="danger" loading={suspendMutation.isPending} onClick={suspendForm.handleSubmit((v) => suspendMutation.mutate(v))}>Suspend</Button></>}>
        <Input label="Reason" placeholder="e.g. payment overdue" {...suspendForm.register('reason', { required: true })} />
      </Modal>

      <Modal open={showChangePlan} onClose={() => setShowChangePlan(false)} title="Change Plan" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowChangePlan(false)}>Cancel</Button><Button loading={changePlanMutation.isPending} onClick={changePlanForm.handleSubmit((v) => changePlanMutation.mutate(v))}>Change Plan</Button></>}>
        <Input label="New plan ID" placeholder="UUID of the plan on this deployment" {...changePlanForm.register('new_plan_id', { required: true })} />
      </Modal>

      <Modal open={showExtend} onClose={() => setShowExtend(false)} title="Extend Expiry" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowExtend(false)}>Cancel</Button><Button loading={extendMutation.isPending} onClick={extendForm.handleSubmit((v) => extendMutation.mutate(v))}>Extend</Button></>}>
        <Input type="date" label="New expiry date" {...extendForm.register('new_expiry_date', { required: true })} />
      </Modal>
    </div>
  )
}
