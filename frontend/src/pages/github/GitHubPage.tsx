import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useForm } from 'react-hook-form'
import toast from 'react-hot-toast'
import { Github, Plus, RotateCw, ExternalLink, GitBranch } from 'lucide-react'
import api from '../../lib/api'
import { cn, formatDate, errorMessage as errMsg } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Tabs } from '../../components/ui/Tabs'
import { DataTable, type Column } from '../../components/ui/DataTable'
import { Badge } from '../../components/ui/Badge'
import { Modal } from '../../components/ui/Modal'
import { Input } from '../../components/ui/Input'
import { Select } from '../../components/ui/Select'
import { Button } from '../../components/ui/Button'
import type {
  Deployment, DeploymentListResponse, GitHubIntegration, GitHubIssue, GitHubPullRequest, GitHubRelease,
  GitHubRepository,
} from '../../types'

const STATUS_VARIANT: Record<GitHubIntegration['status'], 'green' | 'gray' | 'red'> = {
  connected: 'green', disconnected: 'gray', error: 'red',
}

type NewIntegrationForm = { name: string; github_org: string; access_token: string; webhook_secret: string }
type AddRepoForm = { full_name: string }
type ManifestForm = { name: string; github_org: string }

/** Submits GitHub's App Manifest flow — a real top-level navigation
 * (GitHub renders its own confirmation page before creating the App),
 * not a fetch, so this builds and submits a hidden form rather than
 * following a link. See docs/adr/ADR-004-github-app-auth.md decision #9. */
function submitAppManifest(targetUrl: string, manifest: object) {
  const form = document.createElement('form')
  form.method = 'POST'
  form.action = targetUrl
  form.style.display = 'none'
  const input = document.createElement('input')
  input.type = 'hidden'
  input.name = 'manifest'
  input.value = JSON.stringify(manifest)
  form.appendChild(input)
  document.body.appendChild(form)
  form.submit()
}

function IntegrationsTab() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: integrations, isLoading } = useQuery({
    queryKey: ['github-integrations'],
    queryFn: () => api.get<GitHubIntegration[]>('/github/integrations').then((r) => r.data),
    refetchInterval: 30_000,
  })
  const { data: appStatus } = useQuery({
    queryKey: ['github-app-status'],
    queryFn: () => api.get<{ configured: boolean }>('/github/app/status').then((r) => r.data),
  })

  useEffect(() => {
    if (searchParams.get('installed') === '1') {
      toast.success('GitHub App connected')
      qc.invalidateQueries({ queryKey: ['github-integrations'] })
      setSearchParams((params) => { params.delete('installed'); return params }, { replace: true })
    } else if (searchParams.get('app_setup') === '1') {
      toast.success('GitHub App created — you can now connect an org')
      qc.invalidateQueries({ queryKey: ['github-app-status'] })
      setSearchParams((params) => { params.delete('app_setup'); return params }, { replace: true })
    } else if (searchParams.get('app_setup_error') === '1') {
      toast.error('Setting up the GitHub App failed — please try again')
      setSearchParams((params) => { params.delete('app_setup_error'); return params }, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams])

  const connectMutation = useMutation({
    mutationFn: () => api.get<{ url: string }>('/github/app/install-url').then((r) => r.data.url),
    onSuccess: (url) => { window.location.href = url },
    onError: (e) => toast.error(errMsg(e, 'GitHub App is not configured on this hub')),
  })

  const [showSetupApp, setShowSetupApp] = useState(false)
  const setupAppForm = useForm<ManifestForm>()
  const setupAppMutation = useMutation({
    mutationFn: (v: ManifestForm) => api.post<{ manifest: object; target_url: string }>('/github/app/manifest', {
      name: v.name || undefined, github_org: v.github_org || undefined,
    }).then((r) => r.data),
    onSuccess: ({ manifest, target_url }) => submitAppManifest(target_url, manifest),
    onError: (e) => toast.error(errMsg(e, 'Failed to start GitHub App setup')),
  })

  const [showCreate, setShowCreate] = useState(false)
  const createForm = useForm<NewIntegrationForm>()
  const createMutation = useMutation({
    mutationFn: (v: NewIntegrationForm) => api.post('/github/integrations', v),
    onSuccess: () => {
      toast.success('Integration created')
      setShowCreate(false)
      createForm.reset()
      qc.invalidateQueries({ queryKey: ['github-integrations'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to create integration')),
  })

  const testMutation = useMutation({
    mutationFn: (id: string) => api.post(`/github/integrations/${id}/test-connection`).then((r) => r.data as { ok: boolean; detail: string }),
    onSuccess: (data) => {
      toast[data.ok ? 'success' : 'error'](data.detail)
      qc.invalidateQueries({ queryKey: ['github-integrations'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to test connection')),
  })

  const [addRepoFor, setAddRepoFor] = useState<GitHubIntegration | null>(null)
  const addRepoForm = useForm<AddRepoForm>()
  const addRepoMutation = useMutation({
    mutationFn: (v: AddRepoForm) => api.post(`/github/integrations/${addRepoFor!.id}/repositories`, v),
    onSuccess: () => {
      toast.success('Repository added')
      setAddRepoFor(null)
      addRepoForm.reset()
      qc.invalidateQueries({ queryKey: ['github-repositories'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to add repository')),
  })

  const columns: Column<GitHubIntegration>[] = [
    { key: 'name', header: 'Name', render: (i) => <span className="font-medium text-text">{i.name}</span> },
    { key: 'github_org', header: 'Org', render: (i) => <span className="font-mono text-xs text-muted">{i.github_org}</span> },
    {
      key: 'auth_mode', header: 'Auth',
      render: (i) => <Badge variant={i.auth_mode === 'github_app' ? 'blue' : 'gray'}>{i.auth_mode === 'github_app' ? 'GitHub App' : 'Token'}</Badge>,
    },
    { key: 'status', header: 'Status', render: (i) => <Badge variant={STATUS_VARIANT[i.status]}>{i.status}</Badge> },
    { key: 'last_synced_at', header: 'Last synced', render: (i) => formatDate(i.last_synced_at) },
    { key: 'last_webhook_at', header: 'Last webhook', render: (i) => formatDate(i.last_webhook_at) },
    {
      key: 'last_error', header: 'Last error',
      render: (i) => i.last_error ? <span className="text-danger text-xs">{i.last_error}</span> : <span className="text-muted">—</span>,
    },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (i) => (
        <div className="flex justify-end gap-1">
          {can('github.test_connection') && (
            <Button size="sm" variant="secondary" loading={testMutation.isPending} onClick={() => testMutation.mutate(i.id)}>Test</Button>
          )}
          {can('github.manage') && (
            <Button size="sm" variant="ghost" icon={<Plus size={13} />} onClick={() => setAddRepoFor(i)}>Repo</Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <p className="text-sm text-muted max-w-2xl">
          "Connect with GitHub" installs a GitHub App on the org you pick — no token or webhook secret to manage.
          "Use a token instead" connects with a personal/fine-grained access token plus a webhook secret you paste
          into both HUB and GitHub yourself. HUB never returns a token/secret once saved, either way.
        </p>
        {can('github.manage') && (
          <div className="flex gap-2">
            {appStatus?.configured ? (
              <Button icon={<Github size={15} />} loading={connectMutation.isPending} onClick={() => connectMutation.mutate()}>
                Connect with GitHub
              </Button>
            ) : (
              <Button icon={<Github size={15} />} onClick={() => setShowSetupApp(true)}>Set up GitHub App</Button>
            )}
            <Button variant="secondary" icon={<Plus size={15} />} onClick={() => setShowCreate(true)}>Use a token instead</Button>
          </div>
        )}
      </div>
      <DataTable columns={columns} data={integrations ?? []} loading={isLoading} keyExtractor={(i) => i.id} emptyMessage="No GitHub integrations configured yet." />

      <Modal
        open={showSetupApp} onClose={() => setShowSetupApp(false)} title="Set up GitHub App" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowSetupApp(false)}>Cancel</Button><Button loading={setupAppMutation.isPending} onClick={setupAppForm.handleSubmit((v) => setupAppMutation.mutate(v))}>Continue on GitHub</Button></>}
      >
        <p className="text-xs text-muted mb-3">
          Creates a new GitHub App via GitHub's manifest flow — no manual Developer Settings form-filling. You'll be
          taken to GitHub to review and confirm, then back here automatically. Do this once per hub; every future org
          uses "Connect with GitHub" against the same App.
        </p>
        <form className="space-y-4">
          <Input label="App name (optional)" placeholder="BilwaCorp Fleet Hub" {...setupAppForm.register('name')} />
          <Input label="GitHub org (optional)" placeholder="Leave blank to create under your personal account" {...setupAppForm.register('github_org')} />
        </form>
      </Modal>

      <Modal
        open={showCreate} onClose={() => setShowCreate(false)} title="New GitHub Integration" size="sm"
        footer={<><Button variant="secondary" onClick={() => setShowCreate(false)}>Cancel</Button><Button loading={createMutation.isPending} onClick={createForm.handleSubmit((v) => createMutation.mutate(v))}>Create</Button></>}
      >
        <form className="space-y-4">
          <Input label="Name" placeholder="e.g. BilwaCorp org" {...createForm.register('name', { required: true })} />
          <Input label="GitHub org" placeholder="e.g. bilwacorp" {...createForm.register('github_org', { required: true })} />
          <Input label="Access token" type="password" placeholder="ghp_… or fine-grained token" {...createForm.register('access_token', { required: true })} />
          <Input label="Webhook secret" type="password" placeholder="a random secret you'll also enter on GitHub" {...createForm.register('webhook_secret', { required: true })} />
        </form>
      </Modal>

      <Modal
        open={!!addRepoFor} onClose={() => setAddRepoFor(null)} title={`Add repository — ${addRepoFor?.name}`} size="sm"
        footer={<><Button variant="secondary" onClick={() => setAddRepoFor(null)}>Cancel</Button><Button loading={addRepoMutation.isPending} onClick={addRepoForm.handleSubmit((v) => addRepoMutation.mutate(v))}>Add</Button></>}
      >
        <Input label="Repository" placeholder="owner/repo" {...addRepoForm.register('full_name', { required: true })} />
        {addRepoFor && (
          <p className="text-xs text-muted mt-3">
            Webhook URL for this integration: <span className="font-mono">{addRepoFor.webhook_url_path}</span>
          </p>
        )}
      </Modal>
    </div>
  )
}

function RepositoriesTab() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const { data: repositories, isLoading } = useQuery({
    queryKey: ['github-repositories'],
    queryFn: () => api.get<GitHubRepository[]>('/github/repositories').then((r) => r.data),
  })
  const { data: deployments } = useQuery({
    queryKey: ['deployments-for-mapping'],
    queryFn: () => api.get<DeploymentListResponse>('/deployments', { params: { page_size: 100 } }).then((r) => r.data),
    enabled: can('deployments.view'),
  })

  const syncMutation = useMutation({
    mutationFn: (id: string) => api.post(`/github/repositories/${id}/sync`),
    onSuccess: () => toast.success('Sync queued'),
    onError: (e) => toast.error(errMsg(e, 'Failed to queue sync')),
  })

  const [mappingFor, setMappingFor] = useState<GitHubRepository | null>(null)
  const [selectedDeploymentIds, setSelectedDeploymentIds] = useState<string[]>([])
  const [primaryId, setPrimaryId] = useState<string>('')
  const mapMutation = useMutation({
    mutationFn: () => api.put(`/github/repositories/${mappingFor!.id}/deployments`, {
      mappings: selectedDeploymentIds.map((id) => ({ deployment_id: id, is_primary: id === primaryId })),
    }),
    onSuccess: () => {
      toast.success('Deployment mapping updated')
      setMappingFor(null)
      qc.invalidateQueries({ queryKey: ['github-repositories'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Failed to update mapping')),
  })

  const columns: Column<GitHubRepository>[] = [
    {
      key: 'full_name', header: 'Repository',
      render: (r) => (
        <a href={r.html_url} target="_blank" rel="noreferrer" className={cn('inline-flex items-center gap-1 font-medium hover:text-accent', r.is_active ? 'text-text' : 'text-muted line-through')}>
          {r.full_name} <ExternalLink size={12} />
        </a>
      ),
    },
    {
      key: 'is_active', header: 'Access',
      render: (r) => r.is_active
        ? <Badge variant="green">Active</Badge>
        : <Badge variant="gray">Removed from installation</Badge>,
    },
    { key: 'default_branch', header: 'Default branch', render: (r) => <span className="font-mono text-xs text-muted">{r.default_branch}</span> },
    { key: 'last_synced_at', header: 'Last synced', render: (r) => formatDate(r.last_synced_at) },
    {
      key: 'actions', header: '', className: 'text-right',
      render: (r) => (
        <div className="flex justify-end gap-1">
          {can('github.manage') && (
            <Button size="sm" variant="ghost" icon={<GitBranch size={13} />} onClick={() => { setMappingFor(r); setSelectedDeploymentIds([]); setPrimaryId('') }}>
              Map deployments
            </Button>
          )}
          {can('github.sync') && (
            <Button size="sm" variant="secondary" icon={<RotateCw size={13} />} loading={syncMutation.isPending} onClick={() => syncMutation.mutate(r.id)}>
              Sync
            </Button>
          )}
        </div>
      ),
    },
  ]

  return (
    <div>
      <DataTable columns={columns} data={repositories ?? []} loading={isLoading} keyExtractor={(r) => r.id} emptyMessage="No repositories added yet — add one from the Integrations tab." />

      <Modal
        open={!!mappingFor} onClose={() => setMappingFor(null)} title={`Map deployments — ${mappingFor?.full_name}`} size="sm"
        footer={<><Button variant="secondary" onClick={() => setMappingFor(null)}>Cancel</Button><Button loading={mapMutation.isPending} onClick={() => mapMutation.mutate()}>Save</Button></>}
      >
        <p className="text-xs text-muted mb-3">
          A repository can serve more than one deployment; a deployment can pull from more than one repository.
          Pick which mapped repo is "primary" for a deployment's detail page.
        </p>
        <div className="space-y-2 max-h-64 overflow-y-auto">
          {(deployments?.items ?? []).map((d: Deployment) => (
            <div key={d.id} className="flex items-center gap-3 text-sm">
              <input
                type="checkbox"
                checked={selectedDeploymentIds.includes(d.id)}
                onChange={(e) => {
                  setSelectedDeploymentIds((prev) => e.target.checked ? [...prev, d.id] : prev.filter((id) => id !== d.id))
                  if (!e.target.checked && primaryId === d.id) setPrimaryId('')
                }}
              />
              <span className="flex-1">{d.client_name}</span>
              {selectedDeploymentIds.includes(d.id) && (
                <label className="flex items-center gap-1 text-xs text-muted">
                  <input type="radio" name="primary" checked={primaryId === d.id} onChange={() => setPrimaryId(d.id)} /> primary
                </label>
              )}
            </div>
          ))}
          {(deployments?.items ?? []).length === 0 && <p className="text-sm text-muted">No deployments yet.</p>}
        </div>
      </Modal>
    </div>
  )
}

function RepositoryFilterSelect({ repositories, value, onChange }: { repositories: GitHubRepository[]; value: string; onChange: (v: string) => void }) {
  return (
    <Select
      className="w-auto mb-4" placeholder="All repositories" value={value} onChange={(e) => onChange(e.target.value)}
      options={repositories.map((r) => ({ value: r.id, label: r.full_name }))}
    />
  )
}

function PullRequestsTab() {
  const [repositoryId, setRepositoryId] = useState('')
  const { data: repositories } = useQuery({
    queryKey: ['github-repositories'],
    queryFn: () => api.get<GitHubRepository[]>('/github/repositories').then((r) => r.data),
  })
  const { data: prs, isLoading } = useQuery({
    queryKey: ['github-pull-requests', repositoryId],
    queryFn: () => api.get<GitHubPullRequest[]>('/github/pull-requests', { params: { repository_id: repositoryId || undefined } }).then((r) => r.data),
  })

  const columns: Column<GitHubPullRequest>[] = [
    { key: 'number', header: '#', render: (p) => <span className="font-mono text-xs">{p.number}</span> },
    {
      key: 'title', header: 'Title',
      render: (p) => <a href={p.html_url} target="_blank" rel="noreferrer" className="text-text hover:text-accent inline-flex items-center gap-1">{p.title} <ExternalLink size={12} /></a>,
    },
    {
      key: 'state', header: 'State',
      render: (p) => <Badge variant={p.merged_at ? 'blue' : p.state === 'open' ? 'green' : 'gray'}>{p.merged_at ? 'merged' : p.state}{p.is_draft ? ' (draft)' : ''}</Badge>,
    },
    { key: 'author_login', header: 'Author', render: (p) => p.author_login ?? '—' },
    { key: 'opened_at', header: 'Opened', render: (p) => formatDate(p.opened_at) },
  ]

  return (
    <div>
      <RepositoryFilterSelect repositories={repositories ?? []} value={repositoryId} onChange={setRepositoryId} />
      <DataTable columns={columns} data={prs ?? []} loading={isLoading} keyExtractor={(p) => p.id} emptyMessage="No pull requests synced yet." />
    </div>
  )
}

function IssuesTab() {
  const [repositoryId, setRepositoryId] = useState('')
  const { data: repositories } = useQuery({
    queryKey: ['github-repositories'],
    queryFn: () => api.get<GitHubRepository[]>('/github/repositories').then((r) => r.data),
  })
  const { data: issues, isLoading } = useQuery({
    queryKey: ['github-issues', repositoryId],
    queryFn: () => api.get<GitHubIssue[]>('/github/issues', { params: { repository_id: repositoryId || undefined } }).then((r) => r.data),
  })

  const columns: Column<GitHubIssue>[] = [
    { key: 'number', header: '#', render: (i) => <span className="font-mono text-xs">{i.number}</span> },
    {
      key: 'title', header: 'Title',
      render: (i) => <a href={i.html_url} target="_blank" rel="noreferrer" className="text-text hover:text-accent inline-flex items-center gap-1">{i.title} <ExternalLink size={12} /></a>,
    },
    { key: 'state', header: 'State', render: (i) => <Badge variant={i.state === 'open' ? 'green' : 'gray'}>{i.state}</Badge> },
    { key: 'author_login', header: 'Author', render: (i) => i.author_login ?? '—' },
    { key: 'opened_at', header: 'Opened', render: (i) => formatDate(i.opened_at) },
  ]

  return (
    <div>
      <RepositoryFilterSelect repositories={repositories ?? []} value={repositoryId} onChange={setRepositoryId} />
      <DataTable columns={columns} data={issues ?? []} loading={isLoading} keyExtractor={(i) => i.id} emptyMessage="No issues synced yet." />
    </div>
  )
}

function ReleasesTab() {
  const [repositoryId, setRepositoryId] = useState('')
  const { data: repositories } = useQuery({
    queryKey: ['github-repositories'],
    queryFn: () => api.get<GitHubRepository[]>('/github/repositories').then((r) => r.data),
  })
  const { data: releases, isLoading } = useQuery({
    queryKey: ['github-releases', repositoryId],
    queryFn: () => api.get<GitHubRelease[]>('/github/releases', { params: { repository_id: repositoryId || undefined } }).then((r) => r.data),
  })

  const columns: Column<GitHubRelease>[] = [
    {
      key: 'tag_name', header: 'Tag',
      render: (r) => <a href={r.html_url} target="_blank" rel="noreferrer" className="font-mono text-xs text-text hover:text-accent inline-flex items-center gap-1">{r.tag_name} <ExternalLink size={11} /></a>,
    },
    { key: 'name', header: 'Name', render: (r) => r.name ?? '—' },
    {
      key: 'status', header: 'Status',
      render: (r) => r.is_draft ? <Badge variant="gray">draft</Badge> : r.is_prerelease ? <Badge variant="amber">pre-release</Badge> : <Badge variant="green">published</Badge>,
    },
    { key: 'published_at', header: 'Published', render: (r) => formatDate(r.published_at) },
  ]

  return (
    <div>
      <RepositoryFilterSelect repositories={repositories ?? []} value={repositoryId} onChange={setRepositoryId} />
      <DataTable columns={columns} data={releases ?? []} loading={isLoading} keyExtractor={(r) => r.id} emptyMessage="No releases synced yet." />
    </div>
  )
}

export default function GitHubPage() {
  const [tab, setTab] = useState('integrations')

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-text mb-1">GitHub</h1>
      <p className="text-sm text-muted mb-4">
        HUB stores just the operational metadata it needs — repository/PR/issue/release references and their
        deployment mapping — GitHub itself stays the system of record for source and engineering activity.
      </p>
      <Tabs
        className="mb-4 w-fit"
        tabs={[
          { key: 'integrations', label: 'Integrations' },
          { key: 'repositories', label: 'Repositories' },
          { key: 'pull_requests', label: 'Pull Requests' },
          { key: 'issues', label: 'Issues' },
          { key: 'releases', label: 'Releases' },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === 'integrations' && <IntegrationsTab />}
      {tab === 'repositories' && <RepositoriesTab />}
      {tab === 'pull_requests' && <PullRequestsTab />}
      {tab === 'issues' && <IssuesTab />}
      {tab === 'releases' && <ReleasesTab />}
    </div>
  )
}
