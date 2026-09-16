import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import axios from 'axios'
import { Plus, Trash2 } from 'lucide-react'
import api from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Select } from '../../components/ui/Select'
import { Button } from '../../components/ui/Button'
import type {
  ApprovalRule, ApproverStrategy, RuleActionInput, RuleActionType, RuleConditionInput, WorkflowDefinition,
} from '../../types'

function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}

const ACTION_TYPE_OPTIONS: { value: RuleActionType; label: string }[] = [
  { value: 'assign_approver', label: 'Assign approver' },
  { value: 'auto_approve', label: 'Auto-approve' },
  { value: 'skip_step', label: 'Skip step' },
  { value: 'set_variable', label: 'Set variable' },
]

const STRATEGY_OPTIONS: { value: ApproverStrategy; label: string }[] = [
  { value: 'casbin_role', label: 'Casbin role' },
  { value: 'explicit_users', label: 'Explicit user ids' },
]

type FormState = {
  key: string
  name: string
  definition_id: string
  step_key: string
  priority: number
  is_active: boolean
  stop_on_match: boolean
  conditions: RuleConditionInput[]
  actionType: RuleActionType
  strategy: ApproverStrategy
  role_name: string
  user_ids: string
  variable_name: string
  variable_value: string
}

const EMPTY_FORM: FormState = {
  key: '', name: '', definition_id: '', step_key: '', priority: 100, is_active: true, stop_on_match: true,
  conditions: [], actionType: 'assign_approver', strategy: 'casbin_role', role_name: '', user_ids: '',
  variable_name: '', variable_value: '',
}

function ruleToForm(r: ApprovalRule): FormState {
  const action = r.actions[0]
  return {
    key: r.key, name: r.name, definition_id: r.definition_id ?? '', step_key: r.step_key ?? '',
    priority: r.priority, is_active: r.is_active, stop_on_match: r.stop_on_match,
    conditions: r.conditions.map((c) => ({ expression: c.expression, description: c.description })),
    actionType: action?.action_type ?? 'assign_approver',
    strategy: (action?.strategy as ApproverStrategy) ?? 'casbin_role',
    role_name: action?.role_name ?? '',
    user_ids: (action?.user_ids ?? []).join(', '),
    variable_name: action?.variable_name ?? '',
    variable_value: action?.variable_value != null ? String(action.variable_value) : '',
  }
}

function formToAction(f: FormState): RuleActionInput {
  if (f.actionType === 'assign_approver') {
    return {
      action_type: 'assign_approver', strategy: f.strategy,
      role_name: f.strategy === 'casbin_role' ? f.role_name : undefined,
      user_ids: f.strategy === 'explicit_users' ? f.user_ids.split(',').map((s) => s.trim()).filter(Boolean) : undefined,
    }
  }
  if (f.actionType === 'set_variable') {
    return { action_type: 'set_variable', variable_name: f.variable_name, variable_value: f.variable_value }
  }
  return { action_type: f.actionType }
}

export default function ApprovalRulesPage() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const [params] = useSearchParams()
  const definitionFilter = params.get('definition_id') ?? ''

  const { data: definitions } = useQuery({
    queryKey: ['workflows'],
    queryFn: () => api.get<WorkflowDefinition[]>('/workflows').then((r) => r.data),
  })
  const definitionMap = useMemo(() => new Map((definitions ?? []).map((d) => [d.id, d])), [definitions])
  const definitionOptions = (definitions ?? []).map((d) => ({ value: d.id, label: `${d.name} (${d.key})` }))

  const { data: rules, isLoading } = useQuery({
    queryKey: ['workflow-rules-all', definitionFilter],
    queryFn: () => api.get<ApprovalRule[]>('/workflow-rules', {
      params: definitionFilter ? { definition_id: definitionFilter } : undefined,
    }).then((r) => r.data),
  })

  const [editing, setEditing] = useState<ApprovalRule | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<FormState>(EMPTY_FORM)

  const openCreate = () => { setEditing(null); setForm({ ...EMPTY_FORM, definition_id: definitionFilter }); setShowForm(true) }
  const openEdit = (r: ApprovalRule) => { setEditing(r); setForm(ruleToForm(r)); setShowForm(true) }

  const saveMutation = useMutation({
    mutationFn: () => {
      const body = {
        key: form.key, name: form.name, definition_id: form.definition_id || null,
        step_key: form.step_key || null, priority: Number(form.priority), is_active: form.is_active,
        stop_on_match: form.stop_on_match, conditions: form.conditions, actions: [formToAction(form)],
      }
      return editing ? api.patch(`/workflow-rules/${editing.id}`, body) : api.post('/workflow-rules', body)
    },
    onSuccess: () => {
      toast.success(editing ? 'Rule updated' : 'Rule created')
      setShowForm(false)
      qc.invalidateQueries({ queryKey: ['workflow-rules-all'] })
      qc.invalidateQueries({ queryKey: ['workflow-rules'] })
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to save rule'),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => api.patch(`/workflow-rules/${id}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['workflow-rules-all'] }),
    onError: (e) => toast.error(errorDetail(e) || 'Failed to update rule'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/workflow-rules/${id}`),
    onSuccess: () => { toast.success('Rule deleted'); qc.invalidateQueries({ queryKey: ['workflow-rules-all'] }) },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to delete rule (deactivate it instead if it has already routed a task)'),
  })

  const columns: Column<ApprovalRule>[] = [
    { key: 'name', header: 'Name', render: (r) => <span className="font-medium text-text">{r.name}</span> },
    { key: 'key', header: 'Key', render: (r) => <span className="font-mono text-xs text-muted">{r.key}</span> },
    { key: 'definition', header: 'Workflow', render: (r) => <span className="text-muted">{r.definition_id ? (definitionMap.get(r.definition_id)?.name ?? '—') : 'Any'}</span> },
    { key: 'step_key', header: 'Step', render: (r) => <span className="font-mono text-xs text-muted">{r.step_key ?? 'any'}</span> },
    { key: 'priority', header: 'Priority' },
    { key: 'action', header: 'Action', render: (r) => <span className="text-muted">{r.actions[0]?.action_type ?? '—'}{r.actions[0]?.role_name ? ` (${r.actions[0].role_name})` : ''}</span> },
    { key: 'is_system', header: 'Type', render: (r) => <Badge variant={r.is_system ? 'blue' : 'gray'}>{r.is_system ? 'Built-in' : 'Custom'}</Badge> },
    {
      key: 'is_active', header: 'Status',
      render: (r) => (
        <button disabled={!can('workflow_rules.update') || toggleMutation.isPending} onClick={() => toggleMutation.mutate({ id: r.id, is_active: !r.is_active })} className="disabled:cursor-not-allowed">
          <Badge variant={r.is_active ? 'green' : 'gray'}>{r.is_active ? 'Active' : 'Inactive'}</Badge>
        </button>
      ),
    },
    {
      key: 'actions_col', header: '', className: 'text-right',
      render: (r) => (
        <div className="flex justify-end gap-1">
          {can('workflow_rules.update') && <Button size="sm" variant="ghost" onClick={() => openEdit(r)}>Edit</Button>}
          {can('workflow_rules.delete') && !r.is_system && (
            <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => deleteMutation.mutate(r.id)} />
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-1">
        <h1 className="text-xl font-semibold text-text">Approval Rules</h1>
        {can('workflow_rules.create') && <Button icon={<Plus size={15} />} onClick={openCreate}>New Rule</Button>}
      </div>
      <p className="text-sm text-muted mb-4">
        Rules resolve who approves each step. Definition-specific and step-specific rules are tried before generic
        ones, in priority order (lower number first).
      </p>

      <DataTable columns={columns} data={rules ?? []} loading={isLoading} keyExtractor={(r) => r.id} emptyMessage="No rules yet." />

      <Modal
        open={showForm} onClose={() => setShowForm(false)} title={editing ? 'Edit rule' : 'New rule'} size="lg"
        footer={<><Button variant="secondary" onClick={() => setShowForm(false)}>Cancel</Button><Button loading={saveMutation.isPending} onClick={() => saveMutation.mutate()}>Save</Button></>}
      >
        <div className="space-y-4">
          {editing?.is_system && (
            <p className="text-xs text-warning bg-warning-bg border border-warning/20 rounded px-3 py-2">
              Built-in rule — only "Active" can be changed here; other fields are locked.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3">
            <Input label="Key" value={form.key} disabled={!!editing} onChange={(e) => setForm({ ...form, key: e.target.value })} />
            <Input label="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Select label="Workflow (blank = any)" placeholder="Any workflow" options={definitionOptions} value={form.definition_id} onChange={(e) => setForm({ ...form, definition_id: e.target.value })} />
            <Input label="Step key (blank = any)" value={form.step_key} onChange={(e) => setForm({ ...form, step_key: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Input label="Priority (lower runs first)" type="number" value={form.priority} onChange={(e) => setForm({ ...form, priority: Number(e.target.value) })} />
            <label className="flex items-center gap-2 text-sm text-text mt-6">
              <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} /> Active
            </label>
          </div>

          <div>
            <div className="flex items-center justify-between mb-1.5">
              <span className="text-sm font-medium text-text">Conditions (all must match; none = always matches)</span>
              <Button size="sm" variant="ghost" onClick={() => setForm({ ...form, conditions: [...form.conditions, { expression: '' }] })}>+ Add</Button>
            </div>
            {form.conditions.map((c, i) => (
              <div key={i} className="flex items-center gap-2 mb-2">
                <Input
                  className="font-mono text-xs" placeholder="e.g. renewal_amount > 10000" value={c.expression}
                  onChange={(e) => {
                    const next = [...form.conditions]; next[i] = { ...next[i], expression: e.target.value }
                    setForm({ ...form, conditions: next })
                  }}
                />
                <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} onClick={() => setForm({ ...form, conditions: form.conditions.filter((_, j) => j !== i) })} />
              </div>
            ))}
          </div>

          <div className="border-t border-border pt-4">
            <span className="text-sm font-medium text-text">Action</span>
            <div className="grid grid-cols-2 gap-3 mt-1.5">
              <Select label="Type" options={ACTION_TYPE_OPTIONS} value={form.actionType} onChange={(e) => setForm({ ...form, actionType: e.target.value as RuleActionType })} />
              {form.actionType === 'assign_approver' && (
                <Select label="Strategy" options={STRATEGY_OPTIONS} value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value as ApproverStrategy })} />
              )}
            </div>
            {form.actionType === 'assign_approver' && form.strategy === 'casbin_role' && (
              <Input className="mt-3" label="Role name" placeholder="e.g. admin" value={form.role_name} onChange={(e) => setForm({ ...form, role_name: e.target.value })} />
            )}
            {form.actionType === 'assign_approver' && form.strategy === 'explicit_users' && (
              <Input className="mt-3" label="User ids (comma-separated)" value={form.user_ids} onChange={(e) => setForm({ ...form, user_ids: e.target.value })} />
            )}
            {form.actionType === 'set_variable' && (
              <div className="grid grid-cols-2 gap-3 mt-3">
                <Input label="Variable name" value={form.variable_name} onChange={(e) => setForm({ ...form, variable_name: e.target.value })} />
                <Input label="Value" value={form.variable_value} onChange={(e) => setForm({ ...form, variable_value: e.target.value })} />
              </div>
            )}
          </div>
        </div>
      </Modal>
    </div>
  )
}
