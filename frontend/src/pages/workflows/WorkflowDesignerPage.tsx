import { lazy, Suspense, useRef, useState } from 'react'
import { useParams, useNavigate, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { Save, CheckCircle2, UploadCloud, Download, Upload, ArrowLeft } from 'lucide-react'
import api from '../../lib/api'
import { errorMessage as errorDetail } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Button } from '../../components/ui/Button'
import { Badge } from '../../components/ui/Badge'
import type { ValidationResult, WorkflowDefinition, WorkflowVersion } from '../../types'
import type { BpmnDesignerHandle } from './designer/BpmnDesigner'

// Code-split: bpmn-js's modeler + properties panel (with its CodeMirror
// dependency) run to ~200KB gzipped — only worth loading on this one page.
const BpmnDesigner = lazy(() => import('./designer/BpmnDesigner').then((m) => ({ default: m.BpmnDesigner })))

export default function WorkflowDesignerPage() {
  const { id, versionId } = useParams<{ id: string; versionId: string }>()
  const navigate = useNavigate()
  const can = useAuthStore((s) => s.can)
  const qc = useQueryClient()
  const designerRef = useRef<BpmnDesignerHandle>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [validation, setValidation] = useState<ValidationResult | null>(null)
  const [dirty, setDirty] = useState(false)

  const { data: version, isLoading } = useQuery({
    queryKey: ['workflow-version', id, versionId],
    queryFn: () => api.get<WorkflowVersion>(`/workflows/${id}/versions/${versionId}`).then((r) => r.data),
    enabled: !!id && !!versionId,
  })

  const { data: definition } = useQuery({
    queryKey: ['workflow', id],
    queryFn: () => api.get<WorkflowDefinition>(`/workflows/${id}`).then((r) => r.data),
    enabled: !!id,
  })

  const isDraft = version?.status === 'draft'
  // This hub has no hidden bypass role for a built-in (is_system)
  // workflow, unlike PoultryPro-CBF — once published, it's locked for
  // everyone (see backend's app/workflow/services.py's _LOCKED).
  const lockedBuiltIn = !!definition?.is_system
  const canEdit = isDraft && can('workflows.update') && !lockedBuiltIn

  const saveMutation = useMutation({
    mutationFn: async () => {
      const xml = await designerRef.current!.exportXML()
      return api.put(`/workflows/${id}/versions/${versionId}`, {
        bpmn_xml: xml, process_id: version!.process_id, notes: version!.notes,
      })
    },
    onSuccess: () => {
      toast.success('Saved')
      setDirty(false)
      qc.invalidateQueries({ queryKey: ['workflow-version', id, versionId] })
      qc.invalidateQueries({ queryKey: ['workflow-versions', id] })
    },
    onError: (e) => toast.error(errorDetail(e, 'Failed to save')),
  })

  const validateMutation = useMutation({
    mutationFn: () => api.post<ValidationResult>(`/workflows/${id}/versions/${versionId}/validate`).then((r) => r.data),
    onSuccess: (result) => setValidation(result),
    onError: () => toast.error('Failed to validate'),
  })

  const saveAndValidate = async () => {
    await saveMutation.mutateAsync()
    await validateMutation.mutateAsync()
  }

  const publishMutation = useMutation({
    mutationFn: () => api.post(`/workflows/${id}/versions/${versionId}/publish`),
    onSuccess: () => {
      toast.success('Version published')
      qc.invalidateQueries({ queryKey: ['workflow-version', id, versionId] })
      qc.invalidateQueries({ queryKey: ['workflow-versions', id] })
      navigate(`/workflows/${id}`)
    },
    onError: (e) => toast.error(errorDetail(e, 'Failed to publish')),
  })

  const handleExport = async () => {
    if (!designerRef.current) return
    const xml = await designerRef.current.exportXML()
    const blob = new Blob([xml], { type: 'application/xml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${version?.process_id || 'workflow'}-v${version?.version}.bpmn`
    a.click()
    URL.revokeObjectURL(url)
  }

  const handleImportClick = () => fileInputRef.current?.click()

  const handleImportFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file || !designerRef.current) return
    const text = await file.text()
    await designerRef.current.importXML(text)
    setDirty(true)
  }

  if (isLoading || !version) return <div className="p-6 text-sm text-muted">Loading…</div>

  return (
    <div className="flex flex-col h-[calc(100vh-3.5rem)] lg:h-screen p-4 sm:p-6">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <Link to={`/workflows/${id}`} className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-text mb-1">
            <ArrowLeft size={14} /> Back to workflow
          </Link>
          <h1 className="text-lg font-semibold text-text">Design — v{version.version}</h1>
          <p className="text-xs text-muted font-mono">{version.process_id}</p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={version.status === 'published' ? 'green' : version.status === 'draft' ? 'gray' : 'amber'}>{version.status}</Badge>
          <Button size="sm" variant="secondary" icon={<Upload size={13} />} onClick={handleImportClick} disabled={!canEdit}>Import</Button>
          <input ref={fileInputRef} type="file" accept=".bpmn,.xml" className="hidden" onChange={handleImportFile} />
          <Button size="sm" variant="secondary" icon={<Download size={13} />} onClick={handleExport}>Export</Button>
          <Button
            size="sm" variant="secondary" icon={<CheckCircle2 size={13} />}
            loading={saveMutation.isPending || validateMutation.isPending}
            onClick={saveAndValidate} disabled={!canEdit}
          >
            Validate
          </Button>
          <Button size="sm" icon={<Save size={13} />} loading={saveMutation.isPending} onClick={() => saveMutation.mutate()} disabled={!canEdit}>
            Save
          </Button>
          {can('workflows.publish') && isDraft && !lockedBuiltIn && (
            <Button size="sm" variant="primary" icon={<UploadCloud size={13} />} loading={publishMutation.isPending} onClick={() => publishMutation.mutate()}>
              Publish
            </Button>
          )}
        </div>
      </div>

      {lockedBuiltIn && (
        <div className="mb-3 px-3 py-2 rounded border border-info/20 bg-info-bg text-info text-sm">
          This is a built-in workflow — its structure is locked to protect the approval flow. You can view the diagram
          but not edit or publish it. Turn the whole flow on/off from the Workflows list instead.
        </div>
      )}

      {!definition?.is_system && !isDraft && (
        <div className="mb-3 px-3 py-2 rounded border border-info/20 bg-info-bg text-info text-sm">
          This version is {version.status} and can no longer be edited — start a new version from the workflow's detail page to make changes.
        </div>
      )}

      {validation && (
        <div className={`mb-3 px-3 py-2 rounded border text-sm ${validation.is_valid ? 'border-success/20 bg-success-bg text-success' : 'border-danger/20 bg-danger-bg text-danger'}`}>
          {validation.is_valid ? (
            <span>Valid — {validation.steps.length} human step(s) found: {validation.steps.map((s) => s.step_key).filter(Boolean).join(', ') || '—'}</span>
          ) : (
            <ul className="list-disc list-inside space-y-0.5">
              {validation.errors.map((err, i) => <li key={i}>{err}</li>)}
            </ul>
          )}
        </div>
      )}

      {dirty && canEdit && (
        <div className="mb-3 px-3 py-2 rounded border border-warning/20 bg-warning-bg text-warning text-sm">
          You have unsaved changes — Save before publishing or navigating away.
        </div>
      )}

      <div className="flex-1 min-h-0">
        <Suspense fallback={<div className="p-6 text-sm text-muted">Loading designer…</div>}>
          <BpmnDesigner
            ref={designerRef}
            initialXml={version.bpmn_xml}
            onChanged={() => setDirty(true)}
            onImportError={(msg) => toast.error(`Failed to load diagram: ${msg}`)}
          />
        </Suspense>
      </div>
    </div>
  )
}
