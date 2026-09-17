import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import { RotateCw, ExternalLink } from 'lucide-react'
import api from '../../lib/api'
import { formatDate, errorMessage as errMsg } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { Select } from '../../components/ui/Select'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import { EventTimeline } from '../../components/ui/EventTimeline'
import type {
  Application, Customer, Deployment, DeploymentActionExecution, DeploymentEnvironment, DeploymentGitHubInfo,
  MaintenanceMode, MaintenanceWindow, MaintenanceWindowListResponse, OperationalEvent, StaffOption, SupportTicket,
  SupportTicketListResponse,
} from '../../types'

// Money actions (renew/suspend/change-plan) run immediately, unless a
// published approval workflow is configured for that action (see backend's
// app/approvals/deployment_hooks.py) — then the action route returns this
// shape instead of executing straight away. successMessage is shown when
// the action really did run now; approvalMessage covers the gated case.
interface ActionResult {
  approval_required?: boolean
  message?: string
}

function actionToast(data: ActionResult | undefined, successMessage: string) {
  if (data?.approval_required) {
    toast.success(data.message || 'Approval requested — this will run once approved.')
  } else {
    toast.success(successMessage)
  }
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

const EXECUTION_STATUS_VARIANT: Record<DeploymentActionExecution['status'], 'amber' | 'blue' | 'green' | 'red'> = {
  pending: 'amber',
  executing: 'blue',
  executed: 'green',
  failed: 'red',
}

// HUB-Expansion.md Phase 16/18 — the header's at-a-glance health badge.
// Mirrors DeploymentsListPage.tsx's healthDot() (not shared/extracted — a
// small, page-local helper each list/detail view already keeps its own
// copy of, same as e.g. this file's own MODE_BADGE-style constants).
function healthBadge(d: Deployment): { variant: 'green' | 'amber' | 'red' | 'gray'; label: string } {
  switch (d.derived_status) {
    case 'maintenance': return { variant: 'amber', label: 'Under maintenance' }
    case 'offline': return { variant: 'red', label: 'Offline' }
    case 'stale': return { variant: 'amber', label: 'Stale' }
    case 'online': return { variant: 'green', label: 'Healthy' }
    default: return { variant: 'gray', label: 'No heartbeat yet' }
  }
}

// action_key is the workflow definition key ("deployment_renew") — strip
// the "deployment_" prefix and underscore-case for a human label.
function actionLabel(actionKey: string): string {
  return actionKey.replace(/^deployment_/, '').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function DeploymentDetailPage() {
  const { deploymentId } = useParams<{ deploymentId: string }>()
  const navigate = useNavigate()
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
    mutationFn: (v: { new_expiry_date: string; renewal_amount?: number }) =>
      api.post<ActionResult>(`/deployments/${deploymentId}/actions/renew`, v).then((r) => r.data),
    onSuccess: (data) => { actionToast(data, 'Subscription renewed'); setShowRenew(false); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to renew')),
  })

  const suspendForm = useForm<{ reason: string }>()
  const suspendMutation = useMutation({
    mutationFn: (v: { reason: string }) =>
      api.post<ActionResult>(`/deployments/${deploymentId}/actions/suspend`, v).then((r) => r.data),
    onSuccess: (data) => { actionToast(data, 'Subscription suspended'); setShowSuspend(false); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to suspend')),
  })

  const changePlanForm = useForm<{ new_plan_id: string }>()
  const changePlanMutation = useMutation({
    mutationFn: (v: { new_plan_id: string }) =>
      api.post<ActionResult>(`/deployments/${deploymentId}/actions/change-plan`, v).then((r) => r.data),
    onSuccess: (data) => { actionToast(data, 'Plan changed'); setShowChangePlan(false); invalidate() },
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

  // HUB-Expansion.md Phase 12/13: a completed approval no longer implies
  // the deferred call to the deployment actually succeeded — this list is
  // where "approved but execution failed" becomes visible, with a retry
  // action for the ones that are.
  const { data: executions } = useQuery({
    queryKey: ['action-executions', deploymentId],
    queryFn: () => api.get<DeploymentActionExecution[]>(`/deployments/${deploymentId}/action-executions`).then((r) => r.data),
    refetchInterval: 30_000,
  })
  const retryMutation = useMutation({
    mutationFn: (executionId: string) =>
      api.post<DeploymentActionExecution>(`/deployments/${deploymentId}/action-executions/${executionId}/retry`).then((r) => r.data),
    onSuccess: (data) => {
      toast[data.status === 'executed' ? 'success' : 'error'](
        data.status === 'executed' ? 'Retry succeeded' : `Retry failed: ${data.last_error ?? 'unknown error'}`,
      )
      qc.invalidateQueries({ queryKey: ['action-executions', deploymentId] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to retry')),
  })

  // HUB-Expansion.md Phase 3 — GitHub reference data surfaced through
  // this one deployment's lens (row-scoped server-side by
  // DEPLOYMENTS_VIEW, same as every other route here — not a github.*
  // permission; see api/routers/deployments.py's comment).
  const { data: githubInfo } = useQuery({
    queryKey: ['deployment-github', deploymentId],
    queryFn: () => api.get<DeploymentGitHubInfo>(`/deployments/${deploymentId}/github`).then((r) => r.data),
  })

  // HUB-Expansion.md Phase 9 — unified timeline: every OperationalEvent
  // already tagged with this deployment_id, newest first.
  const { data: timeline } = useQuery({
    queryKey: ['deployment-timeline', deploymentId],
    queryFn: () => api.get<OperationalEvent[]>(`/deployments/${deploymentId}/timeline`).then((r) => r.data),
  })

  // HUB-Expansion.md Phase 18 — Support section: this deployment's own
  // tickets. /tickets already supports a deployment_id filter (used by
  // notification "View ticket" links) — no new backend endpoint needed.
  const canViewTickets = can('tickets.view')
  const { data: ticketsResp } = useQuery({
    queryKey: ['deployment-tickets', deploymentId],
    queryFn: () => api.get<SupportTicketListResponse>('/tickets', { params: { deployment_id: deploymentId } }).then((r) => r.data),
    enabled: canViewTickets,
  })
  const openTickets = (ticketsResp?.items ?? []).filter((t: SupportTicket) => t.status === 'open' || t.status === 'in_progress')

  // HUB-Expansion.md Phase 18 — Maintenance section: windows affecting
  // this deployment specifically, or fleet-wide. GET /maintenance-windows
  // has no deployment_id filter (the standalone page doesn't need one),
  // so this filters client-side — the fleet-wide window count is small
  // enough that this isn't a real cost.
  const canViewMaintenance = can('maintenance.view')
  const { data: windowsResp } = useQuery({
    queryKey: ['maintenance-windows'],
    queryFn: () => api.get<MaintenanceWindowListResponse>('/maintenance-windows').then((r) => r.data),
    enabled: canViewMaintenance,
  })
  const relevantWindows = (windowsResp?.items ?? []).filter((w: MaintenanceWindow) => w.deployment_id === null || w.deployment_id === deploymentId)
  const activeWindows = relevantWindows.filter((w) => w.status === 'in_progress')
  const upcomingWindows = relevantWindows.filter((w) => ['draft', 'approval_required', 'approved', 'planned', 'notification'].includes(w.status))
  const pastWindows = relevantWindows.filter((w) => ['completed', 'failed', 'cancelled'].includes(w.status))
    .sort((a, b) => new Date(b.scheduled_start).getTime() - new Date(a.scheduled_start).getTime())

  const [showScheduleMaintenance, setShowScheduleMaintenance] = useState(false)
  const scheduleMaintenanceForm = useForm<{ scheduled_start: string; scheduled_end: string; description: string; mode: MaintenanceMode }>({
    defaultValues: { mode: 'banner' },
  })
  const scheduleMaintenanceMutation = useMutation({
    mutationFn: (v: { scheduled_start: string; scheduled_end: string; description: string; mode: MaintenanceMode }) =>
      api.post('/maintenance-windows', {
        deployment_id: deploymentId, description: v.description, mode: v.mode,
        scheduled_start: new Date(v.scheduled_start).toISOString(), scheduled_end: new Date(v.scheduled_end).toISOString(),
      }),
    onSuccess: () => {
      toast.success('Maintenance window created')
      setShowScheduleMaintenance(false)
      scheduleMaintenanceForm.reset({ mode: 'banner' })
      qc.invalidateQueries({ queryKey: ['maintenance-windows'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to schedule maintenance')),
  })

  // HUB-Expansion.md Phase 4 — lineage (Customer/Application/Environment/
  // current release). Edit affordances are gated by deployments.manage_lineage,
  // which is a separate permission from deployments.view.
  const canManageLineage = can('deployments.manage_lineage')
  const { data: customers } = useQuery({
    queryKey: ['customers'], queryFn: () => api.get<Customer[]>('/customers').then((r) => r.data),
    enabled: canManageLineage,
  })
  const { data: applications } = useQuery({
    queryKey: ['applications'], queryFn: () => api.get<Application[]>('/applications').then((r) => r.data),
    enabled: canManageLineage,
  })
  const [showLineageEdit, setShowLineageEdit] = useState(false)
  const lineageForm = useForm<{ customer_id: string; application_id: string; environment: DeploymentEnvironment }>()
  const lineageMutation = useMutation({
    mutationFn: (v: { customer_id: string; application_id: string; environment: DeploymentEnvironment }) =>
      api.patch(`/deployments/${deploymentId}/lineage`, {
        customer_id: v.customer_id || null, application_id: v.application_id || null, environment: v.environment,
      }),
    onSuccess: () => { toast.success('Lineage updated'); setShowLineageEdit(false); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to update lineage')),
  })
  const openLineageEdit = () => {
    lineageForm.reset({
      customer_id: d?.customer_id ?? '', application_id: d?.application_id ?? '', environment: d?.environment ?? 'production',
    })
    setShowLineageEdit(true)
  }

  const [showRecordRelease, setShowRecordRelease] = useState(false)
  const recordReleaseForm = useForm<{ version: string; commit_sha: string; deployed_by: string; notes: string }>()
  const recordReleaseMutation = useMutation({
    mutationFn: (v: { version: string; commit_sha: string; deployed_by: string; notes: string }) =>
      api.post(`/deployments/${deploymentId}/releases`, {
        version: v.version || null, commit_sha: v.commit_sha || null, deployed_by: v.deployed_by || null, notes: v.notes || null,
      }),
    onSuccess: () => { toast.success('Release recorded'); setShowRecordRelease(false); recordReleaseForm.reset(); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to record release')),
  })

  if (!d) return <div className="p-6 text-muted">Loading…</div>

  const snap = d.latest_snapshot

  return (
    <div className="p-6 space-y-6">
      <div>
        {/* HUB-Expansion.md Phase 16/18 — the header now answers "is this
            deployment healthy right now" at a glance, without scrolling
            to the Technical section's on-demand check. */}
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-xl font-semibold text-text">{d.client_name}</h1>
          <Badge variant={d.environment === 'production' ? 'blue' : 'gray'} className="capitalize">{d.environment}</Badge>
          <Badge variant={healthBadge(d).variant}>{healthBadge(d).label}</Badge>
          {(snap?.app_version ?? d.current_release?.version) && (
            <span className="text-sm text-muted">v{snap?.app_version ?? d.current_release?.version}</span>
          )}
        </div>
        <p className="text-sm text-muted font-mono">{d.slug} · {d.base_url ?? 'not yet registered'}</p>
      </div>

      {/* HUB-Expansion.md Phase 18 — HEADER's suggested action row:
          Renew/Suspend/Change Plan/Health Check/Schedule Maintenance,
          all in one place instead of scattered across sections. */}
      <div className="flex flex-wrap gap-2">
        {can('deployments.renew') && <Button variant="secondary" onClick={() => setShowRenew(true)}>Renew</Button>}
        {can('deployments.change_plan') && <Button variant="secondary" onClick={() => setShowChangePlan(true)}>Change Plan</Button>}
        {can('deployments.extend_expiry') && <Button variant="secondary" onClick={() => setShowExtend(true)}>Extend Expiry</Button>}
        {can('deployments.check_health') && (
          <Button variant="secondary" icon={<RotateCw size={13} />} loading={healthCheckMutation.isPending} onClick={() => healthCheckMutation.mutate()}>
            Health Check
          </Button>
        )}
        {canViewMaintenance && can('maintenance.create') && (
          <Button variant="secondary" onClick={() => setShowScheduleMaintenance(true)}>Schedule Maintenance</Button>
        )}
        {can('deployments.suspend') && <Button variant="danger" onClick={() => setShowSuspend(true)}>Suspend</Button>}
      </div>

      {/* HUB-Expansion.md Phase 4 — lineage: Customer/Application/
          Environment/current version+release+commit+repository. */}
      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text">Software</h3>
          <div className="flex gap-2">
            {canManageLineage && <Button size="sm" variant="ghost" onClick={() => setShowRecordRelease(true)}>Record Release</Button>}
            {canManageLineage && <Button size="sm" variant="secondary" onClick={openLineageEdit}>Edit</Button>}
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-3 text-sm">
          <div>
            <div className="text-xs text-muted mb-0.5">Customer</div>
            <div className="font-medium">{d.customer?.name ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Application</div>
            <div className="font-medium">{d.application?.name ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Environment</div>
            <Badge variant={d.environment === 'production' ? 'blue' : 'gray'} className="capitalize">{d.environment}</Badge>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Current version</div>
            <div className="font-medium">{snap?.app_version ?? d.current_release?.version ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Release</div>
            <div className="font-mono text-xs font-medium">{d.current_release?.release_tag_name ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Commit</div>
            <div className="font-mono text-xs font-medium">{d.current_release?.commit_sha ? d.current_release.commit_sha.slice(0, 7) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Repository</div>
            <div className="font-mono text-xs font-medium">{d.current_release?.repository_full_name ?? '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Deployed</div>
            <div className="font-medium">{d.current_release ? formatDate(d.current_release.deployed_at) : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-muted mb-0.5">Deployed by</div>
            <div className="font-medium">{d.current_release?.deployed_by ?? '—'}</div>
          </div>
        </div>
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

      {/* HUB-Expansion.md Phase 18 — "Overview": subscription/plan/expiry/
          heartbeat at a glance (Usage sits right below this, same
          section conceptually). */}
      <h2 className="text-xs font-semibold uppercase tracking-wide text-muted -mb-2">Overview</h2>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="bg-surface border border-border rounded-lg p-4">
          <div className="text-xs text-muted mb-1">Subscription</div>
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

      {/* HUB-Expansion.md Phase 16/18 — "Technical": deployment URL, live
          API status, last heartbeat, integration status all in one place
          (the last two already exist elsewhere on this page — Overview's
          stat grid and the GitHub panel below — but a summary line here
          means a staff member doesn't have to hunt for them). */}
      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text">Technical</h3>
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
        <div className="space-y-2 text-sm mb-3">
          <div className="flex items-center justify-between">
            <span className="text-muted">Deployment URL</span>
            <span className="font-mono text-xs">{d.base_url ?? 'not yet registered'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">Last heartbeat</span>
            <span>{snap ? formatDate(snap.received_at) : '—'}</span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-muted">GitHub integration</span>
            <span>{githubInfo && githubInfo.repositories.length > 0 ? `Linked (${githubInfo.repositories.length} repo${githubInfo.repositories.length === 1 ? '' : 's'})` : 'Not linked'}</span>
          </div>
        </div>
        {health ? (
          <div className="space-y-2">
            <HealthRow label="API Server" ok={health.api} latencyMs={health.api_latency_ms} />
            <HealthRow label="Database" ok={health.database} latencyMs={health.db_latency_ms} />
            <p className="text-xs text-muted pt-1">Checked {formatDate(health.checked_at)}</p>
          </div>
        ) : (
          <p className="text-sm text-muted">
            "Check now" probes this deployment's own /api/health right now — independent of the Health badge in
            the header above, which only reflects the last heartbeat (up to ~2h old).
          </p>
        )}
      </div>

      {/* HUB-Expansion.md Phase 18's "Approvals: Pending / Recent" — this
          IS that section for deployment actions (renew/suspend/change
          plan). A separate lookup of maintenance-window approval
          instances would duplicate what the Maintenance section above
          already shows via each window's own status badge
          (approval_required/approved), so it isn't repeated here too. */}
      {executions && executions.length > 0 && (
        <div className="bg-surface border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text mb-1">Approvals — Action Executions</h3>
          <p className="text-xs text-muted mb-3">
            An approved renew/suspend/change-plan action runs against the deployment separately from the approval
            itself — a row here can show "failed" even though its approval shows "approved."
          </p>
          <div className="space-y-2">
            {executions.map((e) => (
              <div key={e.id} className="border border-border rounded p-3 flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-text">{actionLabel(e.action_key)}</span>
                    <Badge variant={EXECUTION_STATUS_VARIANT[e.status]}>{e.status}</Badge>
                  </div>
                  <div className="text-xs text-muted mt-1">
                    {e.attempt_count} attempt{e.attempt_count === 1 ? '' : 's'}
                    {e.last_attempted_at ? ` · last attempted ${formatDate(e.last_attempted_at)}` : ''}
                  </div>
                  {e.status === 'failed' && e.last_error && (
                    <div className="text-xs text-danger mt-1">{e.last_error}</div>
                  )}
                </div>
                {e.status === 'failed' && can('actions.retry') && (
                  <Button
                    size="sm" variant="secondary" icon={<RotateCw size={13} />}
                    loading={retryMutation.isPending} onClick={() => retryMutation.mutate(e.id)}
                  >
                    Retry
                  </Button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

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

      {githubInfo && githubInfo.repositories.length > 0 && (
        <div className="bg-surface border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text mb-3">GitHub</h3>
          <div className="space-y-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted">Repository</span>
              {githubInfo.primary_repository && (
                <a href={githubInfo.primary_repository.html_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-text hover:text-accent">
                  {githubInfo.primary_repository.full_name} <ExternalLink size={12} />
                </a>
              )}
            </div>
            {githubInfo.latest_commit && (
              <div className="flex items-center justify-between">
                <span className="text-muted">Current commit</span>
                <a href={githubInfo.latest_commit.html_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs text-text hover:text-accent">
                  {githubInfo.latest_commit.sha.slice(0, 7)} <ExternalLink size={11} />
                </a>
              </div>
            )}
            {githubInfo.latest_pull_request && (
              <div className="flex items-center justify-between">
                <span className="text-muted">Related PR</span>
                <a href={githubInfo.latest_pull_request.html_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-text hover:text-accent">
                  #{githubInfo.latest_pull_request.number} {githubInfo.latest_pull_request.title} <ExternalLink size={12} />
                </a>
              </div>
            )}
            {githubInfo.latest_release && (
              <div className="flex items-center justify-between">
                <span className="text-muted">Related release</span>
                <a href={githubInfo.latest_release.html_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-xs text-text hover:text-accent">
                  {githubInfo.latest_release.tag_name} <ExternalLink size={11} />
                </a>
              </div>
            )}
            {githubInfo.repositories.length > 1 && (
              <p className="text-xs text-muted pt-1">+{githubInfo.repositories.length - 1} more mapped repository(ies).</p>
            )}
          </div>
        </div>
      )}

      {/* HUB-Expansion.md Phase 18 — Support: this deployment's own
          tickets (CLAUDE.md previously called this out explicitly: "the
          deployment detail page doesn't show tickets" — no longer true). */}
      {canViewTickets && (
        <div className="bg-surface border border-border rounded-lg p-4">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-text">Support</h3>
            <span className="text-xs text-muted">{openTickets.length} open</span>
          </div>
          <div className="space-y-2">
            {(ticketsResp?.items ?? []).slice(0, 5).map((t: SupportTicket) => (
              <div
                key={t.id} className="flex items-center justify-between text-sm cursor-pointer hover:text-accent"
                onClick={() => navigate(`/tickets/${t.id}`)}
              >
                <span className="truncate">{t.subject}</span>
                <Badge variant={t.status === 'open' ? 'amber' : t.status === 'in_progress' ? 'blue' : 'gray'} className="shrink-0 ml-2">
                  {t.status.replace('_', ' ')}
                </Badge>
              </div>
            ))}
            {(ticketsResp?.items ?? []).length === 0 && <p className="text-sm text-muted">No tickets from this deployment yet.</p>}
          </div>
        </div>
      )}

      {/* HUB-Expansion.md Phase 18 — Maintenance: windows affecting this
          deployment, or fleet-wide, bucketed by upcoming/active/history. */}
      {canViewMaintenance && (
        <div className="bg-surface border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text mb-3">Maintenance</h3>
          {activeWindows.length > 0 && (
            <div className="mb-3">
              <p className="text-xs text-muted mb-1">Active</p>
              {activeWindows.map((w) => (
                <div key={w.id} className="text-sm flex items-center justify-between">
                  <span>{w.description}</span>
                  <Badge variant="amber">in progress</Badge>
                </div>
              ))}
            </div>
          )}
          <div className="mb-3">
            <p className="text-xs text-muted mb-1">Upcoming</p>
            {upcomingWindows.map((w) => (
              <div key={w.id} className="text-sm flex items-center justify-between">
                <span>{w.description} — {formatDate(w.scheduled_start)}</span>
                <Badge variant={w.status === 'approval_required' ? 'amber' : 'blue'}>{w.status.replace('_', ' ')}</Badge>
              </div>
            ))}
            {upcomingWindows.length === 0 && <p className="text-sm text-muted">None scheduled.</p>}
          </div>
          <div>
            <p className="text-xs text-muted mb-1">History</p>
            {pastWindows.slice(0, 5).map((w) => (
              <div key={w.id} className="text-sm flex items-center justify-between text-muted">
                <span>{w.description} — {formatDate(w.scheduled_start)}</span>
                <Badge variant={w.status === 'completed' ? 'green' : w.status === 'failed' ? 'red' : 'gray'}>{w.status}</Badge>
              </div>
            ))}
            {pastWindows.length === 0 && <p className="text-sm text-muted">No past windows.</p>}
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

      <div className="bg-surface border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-text mb-3">Timeline</h3>
        <EventTimeline events={timeline ?? []} />
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

      <Modal
        open={showScheduleMaintenance} onClose={() => setShowScheduleMaintenance(false)} title="Schedule Maintenance" size="sm"
        footer={<>
          <Button variant="secondary" onClick={() => setShowScheduleMaintenance(false)}>Cancel</Button>
          <Button loading={scheduleMaintenanceMutation.isPending} onClick={scheduleMaintenanceForm.handleSubmit((v) => scheduleMaintenanceMutation.mutate(v))}>
            Create
          </Button>
        </>}
      >
        <form className="space-y-4">
          <Input type="datetime-local" label="Start" {...scheduleMaintenanceForm.register('scheduled_start', { required: true })} />
          <Input type="datetime-local" label="End" {...scheduleMaintenanceForm.register('scheduled_end', { required: true })} />
          <Input label="Description" placeholder="e.g. Database upgrade" {...scheduleMaintenanceForm.register('description', { required: true })} />
          <Select
            label="Enforcement"
            options={[
              { value: 'banner', label: 'Banner only' },
              { value: 'read_only', label: 'Read-only' },
              { value: 'lockout', label: 'Lockout (needs approval)' },
            ]}
            {...scheduleMaintenanceForm.register('mode')}
          />
          <p className="text-xs text-muted">Scoped to {d.client_name}. Lockout-mode windows require admin approval before they're scheduled.</p>
        </form>
      </Modal>

      <Modal open={showLineageEdit} onClose={() => setShowLineageEdit(false)} title="Edit Lineage" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowLineageEdit(false)}>Cancel</Button><Button loading={lineageMutation.isPending} onClick={lineageForm.handleSubmit((v) => lineageMutation.mutate(v))}>Save</Button></>}>
        <form className="space-y-4">
          <Select
            label="Customer" placeholder="Unlinked"
            options={(customers ?? []).map((c: Customer) => ({ value: c.id, label: c.name }))}
            {...lineageForm.register('customer_id')}
          />
          <Select
            label="Application" placeholder="Unlinked"
            options={(applications ?? []).map((a: Application) => ({ value: a.id, label: a.name }))}
            {...lineageForm.register('application_id')}
          />
          <Select
            label="Environment"
            options={(['production', 'staging', 'development', 'uat'] as DeploymentEnvironment[]).map((e) => ({ value: e, label: e }))}
            {...lineageForm.register('environment')}
          />
        </form>
      </Modal>

      <Modal open={showRecordRelease} onClose={() => setShowRecordRelease(false)} title="Record Release" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowRecordRelease(false)}>Cancel</Button><Button loading={recordReleaseMutation.isPending} onClick={recordReleaseForm.handleSubmit((v) => recordReleaseMutation.mutate(v))}>Record</Button></>}>
        <form className="space-y-4">
          <Input label="Version" placeholder="e.g. 2.8.15" {...recordReleaseForm.register('version')} />
          <Input label="Commit SHA (optional)" placeholder="e.g. 83ad92f..." {...recordReleaseForm.register('commit_sha')} />
          <Input label="Deployed by (optional)" placeholder="e.g. GitHub Actions" {...recordReleaseForm.register('deployed_by')} />
          <Input label="Notes (optional)" {...recordReleaseForm.register('notes')} />
          <p className="text-xs text-muted">
            Manual entry — a heartbeat reporting a new version already records this automatically when it matches a
            linked GitHub release; use this to backfill or correct history.
          </p>
        </form>
      </Modal>
    </div>
  )
}
