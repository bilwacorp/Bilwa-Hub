import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import { ExternalLink, Trash2 } from 'lucide-react'
import api from '../../lib/api'
import { formatDate, errorMessage as errMsg } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Button } from '../../components/ui/Button'
import { Select } from '../../components/ui/Select'
import { Modal } from '../../components/ui/Modal'
import { Badge } from '../../components/ui/Badge'
import type {
  Deployment, DeploymentGitHubInfo, GitHubIssue, GitHubPullRequest, GitHubRelease, MaintenanceWindow,
  MaintenanceWindowListResponse, OperationalEvent, SupportTicket, SupportTicketLink, SupportTicketStatus, TicketLinkType,
} from '../../types'

const STATUS_VARIANT: Record<SupportTicketStatus, 'amber' | 'blue' | 'green' | 'gray'> = {
  open: 'amber',
  in_progress: 'blue',
  resolved: 'green',
  closed: 'gray',
}

const LINK_TYPE_LABEL: Record<TicketLinkType, string> = {
  github_issue: 'Engineering issue',
  github_pull_request: 'Pull request',
  github_release: 'Release',
  maintenance_window: 'Maintenance window',
}

type AddLinkForm = { link_type: TicketLinkType; target_id: string }

export default function SupportTicketDetailPage() {
  const { ticketId } = useParams<{ ticketId: string }>()
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const canManageLinks = can('tickets.manage_links')

  const { data: t } = useQuery({
    queryKey: ['ticket', ticketId],
    queryFn: () => api.get<SupportTicket>(`/tickets/${ticketId}`).then((r) => r.data),
  })
  const { data: links } = useQuery({
    queryKey: ['ticket-links', ticketId],
    queryFn: () => api.get<SupportTicketLink[]>(`/tickets/${ticketId}/links`).then((r) => r.data),
  })
  const { data: timeline } = useQuery({
    queryKey: ['ticket-timeline', ticketId],
    queryFn: () => api.get<OperationalEvent[]>(`/tickets/${ticketId}/timeline`).then((r) => r.data),
  })
  const { data: deployment } = useQuery({
    queryKey: ['deployment', t?.deployment_id],
    queryFn: () => api.get<Deployment>(`/deployments/${t!.deployment_id}`).then((r) => r.data),
    enabled: !!t,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['ticket-links', ticketId] })
    qc.invalidateQueries({ queryKey: ['ticket-timeline', ticketId] })
  }

  const [nextStatus, setNextStatus] = useState<SupportTicketStatus | null>(null)
  const statusMutation = useMutation({
    mutationFn: (status: SupportTicketStatus) => api.patch(`/tickets/${ticketId}`, { status }),
    onSuccess: () => { toast.success('Ticket updated'); setNextStatus(null); qc.invalidateQueries({ queryKey: ['ticket', ticketId] }); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to update ticket')),
  })

  const [showAddLink, setShowAddLink] = useState(false)
  const addLinkForm = useForm<AddLinkForm>({ defaultValues: { link_type: 'github_issue', target_id: '' } })
  const linkType = addLinkForm.watch('link_type')

  // HUB-Expansion.md Phase 6 — the Add Link picker is scoped to this
  // ticket's deployment's own linked GitHub repo(s) (Phase 3), same
  // row-level lens api/routers/deployments.py's GitHub panel already
  // uses. A staff member who can manage_links but lacks github.view/
  // maintenance.view just sees an empty picker for that type — a known,
  // accepted gap for a custom role that pairs those permissions oddly
  // (the two seeded roles never do).
  const { data: githubInfo } = useQuery({
    queryKey: ['deployment-github', t?.deployment_id],
    queryFn: () => api.get<DeploymentGitHubInfo>(`/deployments/${t!.deployment_id}/github`).then((r) => r.data),
    enabled: !!t && showAddLink,
  })
  const repositoryId = githubInfo?.primary_repository?.id ?? githubInfo?.repositories[0]?.id
  const { data: issues } = useQuery({
    queryKey: ['github-issues', repositoryId],
    queryFn: () => api.get<GitHubIssue[]>('/github/issues', { params: { repository_id: repositoryId } }).then((r) => r.data),
    enabled: showAddLink && linkType === 'github_issue' && !!repositoryId,
  })
  const { data: pullRequests } = useQuery({
    queryKey: ['github-prs', repositoryId],
    queryFn: () => api.get<GitHubPullRequest[]>('/github/pull-requests', { params: { repository_id: repositoryId } }).then((r) => r.data),
    enabled: showAddLink && linkType === 'github_pull_request' && !!repositoryId,
  })
  const { data: releases } = useQuery({
    queryKey: ['github-releases', repositoryId],
    queryFn: () => api.get<GitHubRelease[]>('/github/releases', { params: { repository_id: repositoryId } }).then((r) => r.data),
    enabled: showAddLink && linkType === 'github_release' && !!repositoryId,
  })
  const { data: maintenanceWindows } = useQuery({
    queryKey: ['maintenance-windows'],
    queryFn: () => api.get<MaintenanceWindowListResponse>('/maintenance-windows').then((r) => r.data),
    enabled: showAddLink && linkType === 'maintenance_window',
  })
  const maintenanceOptions = (maintenanceWindows?.items ?? []).filter(
    (w: MaintenanceWindow) => w.deployment_id === null || w.deployment_id === t?.deployment_id,
  )

  const targetOptions = {
    github_issue: (issues ?? []).map((i) => ({ value: i.id, label: `#${i.number}: ${i.title}` })),
    github_pull_request: (pullRequests ?? []).map((p) => ({ value: p.id, label: `#${p.number}: ${p.title}` })),
    github_release: (releases ?? []).map((r) => ({ value: r.id, label: r.tag_name })),
    maintenance_window: maintenanceOptions.map((w) => ({ value: w.id, label: `${w.description} (${formatDate(w.scheduled_start)})` })),
  }[linkType]

  const addLinkMutation = useMutation({
    mutationFn: (v: AddLinkForm) => api.post(`/tickets/${ticketId}/links`, v),
    onSuccess: () => { toast.success('Link added'); setShowAddLink(false); addLinkForm.reset({ link_type: 'github_issue', target_id: '' }); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to add link')),
  })
  const removeLinkMutation = useMutation({
    mutationFn: (linkId: string) => api.delete(`/tickets/${ticketId}/links/${linkId}`),
    onSuccess: () => { toast.success('Link removed'); invalidate() },
    onError: (e) => toast.error(errMsg(e, 'Failed to remove link')),
  })

  if (!t) return <div className="p-6 text-muted">Loading…</div>

  return (
    <div className="p-6 space-y-6">
      <div>
        <div className="flex items-center gap-3">
          <h1 className="text-xl font-semibold text-text">{t.subject}</h1>
          <Badge variant={STATUS_VARIANT[t.status]}>{t.status.replace('_', ' ')}</Badge>
        </div>
        <p className="text-sm text-muted">
          {deployment?.client_name ?? '…'} · Submitted {formatDate(t.created_at)}
          {t.submitted_by_name && ` by ${t.submitted_by_name}`}
        </p>
      </div>

      <div className="bg-surface border border-border rounded-lg p-4 space-y-3">
        <h3 className="text-sm font-semibold text-text">Problem</h3>
        <p className="text-sm text-text whitespace-pre-wrap">{t.description}</p>
        {can('tickets.update_status') && (
          <div className="flex items-end gap-3 pt-2">
            <div className="w-48">
              <Select
                label="Status"
                options={[
                  { value: 'open', label: 'Open' },
                  { value: 'in_progress', label: 'In Progress' },
                  { value: 'resolved', label: 'Resolved' },
                  { value: 'closed', label: 'Closed' },
                ]}
                value={nextStatus ?? t.status}
                onChange={(e) => setNextStatus(e.target.value as SupportTicketStatus)}
              />
            </div>
            {nextStatus && nextStatus !== t.status && (
              <Button size="sm" loading={statusMutation.isPending} onClick={() => statusMutation.mutate(nextStatus)}>Save</Button>
            )}
          </div>
        )}
      </div>

      <div className="bg-surface border border-border rounded-lg p-4">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-text">Links</h3>
          {canManageLinks && <Button size="sm" variant="secondary" onClick={() => setShowAddLink(true)}>Add Link</Button>}
        </div>
        <div className="space-y-2">
          {(links ?? []).map((l) => (
            <div key={l.id} className="flex items-center justify-between text-sm border border-border rounded px-3 py-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-xs text-muted shrink-0">{LINK_TYPE_LABEL[l.link_type]}:</span>
                {l.url ? (
                  <a href={l.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-text hover:text-accent truncate">
                    {l.label} <ExternalLink size={12} className="shrink-0" />
                  </a>
                ) : (
                  <span className="text-text truncate">{l.label}</span>
                )}
                {l.target_status && <Badge variant={l.target_status === 'open' ? 'green' : 'gray'} className="shrink-0">{l.target_status}</Badge>}
              </div>
              {canManageLinks && (
                <button onClick={() => removeLinkMutation.mutate(l.id)} className="text-muted hover:text-danger shrink-0 ml-2" title="Remove link">
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          ))}
          {(links ?? []).length === 0 && <p className="text-sm text-muted">No links yet.</p>}
        </div>
      </div>

      <div className="bg-surface border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-text mb-3">Timeline</h3>
        <div className="space-y-0">
          {(timeline ?? []).map((e, i) => (
            <div key={e.id} className="flex gap-3">
              <div className="flex flex-col items-center">
                <span className="h-2 w-2 rounded-full bg-accent shrink-0 mt-1.5" />
                {i < (timeline?.length ?? 0) - 1 && <span className="w-px flex-1 bg-border" />}
              </div>
              <div className="pb-4 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-xs text-text">{e.event_type}</span>
                  {e.status !== 'info' && <Badge variant={e.status === 'success' ? 'green' : e.status === 'failure' ? 'red' : 'amber'}>{e.status}</Badge>}
                </div>
                <div className="text-xs text-muted mt-0.5">{formatDate(e.created_at)}</div>
              </div>
            </div>
          ))}
          {(timeline ?? []).length === 0 && <p className="text-sm text-muted">No timeline events yet.</p>}
        </div>
      </div>

      <Modal
        open={showAddLink} onClose={() => setShowAddLink(false)} title="Add Link" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowAddLink(false)}>Cancel</Button><Button loading={addLinkMutation.isPending} onClick={addLinkForm.handleSubmit((v) => addLinkMutation.mutate(v))}>Add</Button></>}
      >
        <form className="space-y-4">
          <Select
            label="Link type"
            options={[
              { value: 'github_issue', label: 'Engineering issue' },
              { value: 'github_pull_request', label: 'Pull request' },
              { value: 'github_release', label: 'Release' },
              { value: 'maintenance_window', label: 'Maintenance window' },
            ]}
            {...addLinkForm.register('link_type')}
          />
          <Select
            label="Target"
            placeholder={targetOptions.length === 0 ? 'No options available' : 'Select…'}
            options={targetOptions}
            {...addLinkForm.register('target_id', { required: true })}
          />
        </form>
      </Modal>
    </div>
  )
}
