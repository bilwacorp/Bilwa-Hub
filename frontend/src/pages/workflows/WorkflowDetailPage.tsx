import { Link, useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { ArrowLeft, Plus, ExternalLink } from 'lucide-react'
import api from '../../lib/api'
import { formatDate, errorMessage as errorDetail } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { buildBlankBpmn } from './designer/blankBpmn'
import type { ApprovalRule, WorkflowDefinition, WorkflowVersionListItem } from '../../types'

export default function WorkflowDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)

  const { data: definition, isLoading: definitionLoading } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => api.get<WorkflowDefinition>(`/workflows/${id}`).then((r) => r.data),
    enabled: !!id,
  })

  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ['workflow-versions', id],
    queryFn: () => api.get<WorkflowVersionListItem[]>(`/workflows/${id}/versions`).then((r) => r.data),
    enabled: !!id,
  })

  const { data: rules } = useQuery({
    queryKey: ['workflow-rules', id],
    queryFn: () => api.get<ApprovalRule[]>('/workflow-rules', { params: { definition_id: id } }).then((r) => r.data),
    enabled: !!id,
  })

  const lockedBuiltIn = !!definition?.is_system

  const createVersionMutation = useMutation({
    mutationFn: () => api.post(`/workflows/${id}/versions`, {
      bpmn_xml: buildBlankBpmn(definition?.key ?? 'process'),
      process_id: definition?.key ?? 'process',
      notes: null,
    }),
    onSuccess: (res) => {
      toast.success('Draft version created')
      qc.invalidateQueries({ queryKey: ['workflow-versions', id] })
      navigate(`/workflows/${id}/versions/${(res.data as { id: string }).id}`)
    },
    onError: (e) => toast.error(errorDetail(e, 'Failed to create a new version')),
  })

  const columns: Column<WorkflowVersionListItem>[] = [
    {
      key: 'version', header: 'Version',
      render: (v) => (
        <Link to={`/workflows/${id}/versions/${v.id}`} className="font-medium text-text hover:text-accent">
          v{v.version}
        </Link>
      ),
    },
    { key: 'status', header: 'Status', render: (v) => <Badge variant={v.status === 'published' ? 'green' : v.status === 'draft' ? 'gray' : 'amber'}>{v.status}</Badge> },
    { key: 'notes', header: 'Notes', render: (v) => <span className="text-muted">{v.notes ?? '—'}</span> },
    { key: 'created_at', header: 'Created', render: (v) => formatDate(v.created_at) },
    { key: 'published_at', header: 'Published', render: (v) => formatDate(v.published_at) },
  ]

  if (definitionLoading || !definition) return <div className="p-6 text-sm text-muted">Loading…</div>

  return (
    <div className="p-6 max-w-4xl">
      <Link to="/workflows" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-text mb-4">
        <ArrowLeft size={14} /> Back to workflows
      </Link>

      <div className="flex items-center gap-2 mb-1">
        <h1 className="text-xl font-semibold text-text">{definition.name}</h1>
        <Badge variant={definition.is_system ? 'blue' : 'gray'}>{definition.is_system ? 'Built-in' : 'Custom'}</Badge>
        <Badge variant={definition.is_active ? 'green' : 'gray'}>{definition.is_active ? 'Active' : 'Inactive'}</Badge>
      </div>
      <p className="text-sm text-muted mb-1 font-mono">{definition.key}</p>
      {definition.description && <p className="text-sm text-muted mb-6">{definition.description}</p>}

      {lockedBuiltIn && (
        <div className="mb-4 px-3 py-2 rounded border border-info/20 bg-info-bg text-info text-sm">
          Built-in workflow — its structure is locked once published. Use "Active" on the Workflows list to turn the
          whole approval gate on or off.
        </div>
      )}

      <div className="flex items-center justify-between mb-2 mt-6">
        <h2 className="text-sm font-semibold text-text uppercase tracking-wide">Versions</h2>
        {can('workflows.update') && !lockedBuiltIn && (
          <Button size="sm" icon={<Plus size={13} />} loading={createVersionMutation.isPending} onClick={() => createVersionMutation.mutate()}>
            New draft version
          </Button>
        )}
      </div>
      <DataTable columns={columns} data={versions ?? []} loading={versionsLoading} keyExtractor={(v) => v.id} emptyMessage="No versions yet." />

      <div className="flex items-center justify-between mb-2 mt-8">
        <h2 className="text-sm font-semibold text-text uppercase tracking-wide">Approval rules</h2>
        <Link to={`/workflow-rules?definition_id=${id}`} className="inline-flex items-center gap-1 text-sm text-accent hover:underline">
          Manage rules <ExternalLink size={13} />
        </Link>
      </div>
      <Card padding="sm">
        {!rules || rules.length === 0 ? (
          <p className="text-sm text-muted">No rules target this workflow yet — every step will error with "no approval rule matched" until one exists.</p>
        ) : (
          <div className="space-y-2">
            {rules.map((r) => (
              <div key={r.id} className="flex items-center justify-between text-sm py-1.5 border-b border-border last:border-0">
                <div>
                  <span className="font-medium text-text">{r.name}</span>
                  <span className="text-muted ml-2 font-mono text-xs">{r.step_key ?? 'any step'}</span>
                </div>
                <Badge variant={r.is_active ? 'green' : 'gray'}>{r.is_active ? 'Active' : 'Inactive'}</Badge>
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  )
}
