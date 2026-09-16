import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import axios from 'axios'
import { Check, X } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Button } from '../../components/ui/Button'
import { Tabs } from '../../components/ui/Tabs'
import type { WorkflowTask } from '../../types'

function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}

function statusBadge(status: WorkflowTask['status']) {
  const variant = status === 'approved' ? 'green' : status === 'rejected' ? 'red' : status === 'pending' ? 'amber' : 'gray'
  return <Badge variant={variant}>{status}</Badge>
}

// The task's step_key (e.g. "deployment_renew_approval") doubles as a
// human-readable label — strip the "_approval" suffix and underscore-case.
function describeTask(t: WorkflowTask): string {
  const base = (t.step_key ?? t.task_name ?? t.task_spec_name).replace(/_approval$/, '')
  return base.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export default function MyApprovalsPage() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const canActAny = can('approvals.act_any')
  const [scope, setScope] = useState<'mine' | 'all'>('mine')

  const { data: myTasks, isLoading: myLoading } = useQuery({
    queryKey: ['my-tasks'],
    queryFn: () => api.get<WorkflowTask[]>('/approvals/my-tasks').then((r) => r.data),
  })

  const { data: allTasks, isLoading: allLoading } = useQuery({
    queryKey: ['all-tasks'],
    queryFn: () => api.get<WorkflowTask[]>('/approvals/tasks').then((r) => r.data),
    enabled: scope === 'all' && canActAny,
  })

  const tasks = scope === 'mine' ? myTasks : allTasks
  const loading = scope === 'mine' ? myLoading : allLoading
  const pending = (tasks ?? []).filter((t) => t.status === 'pending')
  const done = (tasks ?? []).filter((t) => t.status !== 'pending')

  const [target, setTarget] = useState<{ task: WorkflowTask; action: 'approve' | 'reject' } | null>(null)
  const [comment, setComment] = useState('')

  const actMutation = useMutation({
    mutationFn: () => api.post(`/approvals/tasks/${target!.task.id}/${target!.action}`, { comment: comment || null, data: {} }),
    onSuccess: () => {
      toast.success(target!.action === 'approve' ? 'Approved' : 'Rejected')
      setTarget(null)
      setComment('')
      qc.invalidateQueries({ queryKey: ['my-tasks'] })
      qc.invalidateQueries({ queryKey: ['all-tasks'] })
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to act on this task'),
  })

  return (
    <div className="p-6 max-w-3xl">
      <h1 className="text-xl font-semibold text-text mb-1">Approvals</h1>
      <p className="text-sm text-muted mb-4">Requests waiting on your decision, and their history.</p>

      {canActAny && (
        <Tabs
          className="mb-4 w-fit"
          tabs={[{ key: 'mine', label: 'Assigned to me' }, { key: 'all', label: 'All approvals' }]}
          active={scope}
          onChange={(k) => setScope(k as 'mine' | 'all')}
        />
      )}

      <h2 className="text-sm font-semibold text-text uppercase tracking-wide mb-2">Pending ({pending.length})</h2>
      {loading ? (
        <p className="text-sm text-muted">Loading…</p>
      ) : pending.length === 0 ? (
        <p className="text-sm text-muted mb-6">Nothing waiting on you right now.</p>
      ) : (
        <div className="space-y-2 mb-6">
          {pending.map((t) => (
            <Card key={t.id} padding="sm">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-text">{describeTask(t)}</p>
                  <p className="text-xs text-muted">Requested {formatDate(t.created_at)}</p>
                </div>
                {can('approvals.act') && (
                  <div className="flex gap-2 shrink-0">
                    <Button size="sm" variant="secondary" icon={<X size={14} />} onClick={() => setTarget({ task: t, action: 'reject' })}>Reject</Button>
                    <Button size="sm" icon={<Check size={14} />} onClick={() => setTarget({ task: t, action: 'approve' })}>Approve</Button>
                  </div>
                )}
              </div>
            </Card>
          ))}
        </div>
      )}

      <h2 className="text-sm font-semibold text-text uppercase tracking-wide mb-2">History</h2>
      {done.length === 0 ? (
        <p className="text-sm text-muted">No decided tasks yet.</p>
      ) : (
        <div className="space-y-2">
          {done.map((t) => (
            <Card key={t.id} padding="sm">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-text">{describeTask(t)}</p>
                  <p className="text-xs text-muted">{t.acted_at ? `Actioned ${formatDate(t.acted_at)}` : ''}{t.comment ? ` — "${t.comment}"` : ''}</p>
                </div>
                {statusBadge(t.status)}
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        open={!!target} onClose={() => setTarget(null)} title={target?.action === 'approve' ? 'Approve request' : 'Reject request'} size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={() => setTarget(null)}>Cancel</Button>
            <Button variant={target?.action === 'reject' ? 'danger' : 'primary'} loading={actMutation.isPending} onClick={() => actMutation.mutate()}>
              {target?.action === 'approve' ? 'Approve' : 'Reject'}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          {target && <p className="text-sm text-text">{describeTask(target.task)}</p>}
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-text">Comment (optional)</span>
            <textarea
              className="w-full rounded border border-border bg-surface text-sm text-text px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent/30 focus:border-accent"
              rows={3} value={comment} onChange={(e) => setComment(e.target.value)}
            />
          </label>
        </div>
      </Modal>
    </div>
  )
}
