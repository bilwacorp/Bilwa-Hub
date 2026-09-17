import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, AlertOctagon } from 'lucide-react'
import api from '../../lib/api'
import { formatDate } from '../../lib/utils'
import type { DashboardOut } from '../../types'

function StatTile({ label, value, tone }: { label: string; value: number; tone?: 'danger' | 'warning' }) {
  return (
    <div className="bg-surface border border-border rounded-lg p-4">
      <div className="text-xs text-muted mb-1">{label}</div>
      <div className={`text-2xl font-semibold ${tone === 'danger' ? 'text-danger' : tone === 'warning' ? 'text-warning' : 'text-text'}`}>
        {value}
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface border border-border rounded-lg p-4">
      <h3 className="text-sm font-semibold text-text mb-3">{title}</h3>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{children}</div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="text-xs text-muted">{label}</div>
      <div className="text-lg font-semibold text-text">{value}</div>
    </div>
  )
}

export default function DashboardPage() {
  const navigate = useNavigate()
  const { data, isLoading } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => api.get<DashboardOut>('/dashboard').then((r) => r.data),
    refetchInterval: 60_000,
  })

  if (isLoading || !data) return <div className="p-6 text-muted">Loading…</div>

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-semibold text-text">Operations Dashboard</h1>

      {/* HUB-Expansion.md Phase 10: "This is more important than adding
          dozens of charts" — attention items lead the page. */}
      {data.attention.length > 0 && (
        <div className="bg-surface border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text mb-3">Attention Required</h3>
          <div className="space-y-2">
            {data.attention.map((a, i) => (
              <div
                key={i}
                className={`flex items-start gap-2 text-sm px-3 py-2 rounded border cursor-pointer hover:opacity-80 ${
                  a.severity === 'critical' ? 'border-danger/30 bg-danger/5 text-danger' : 'border-warning/30 bg-warning/5 text-warning'
                }`}
                onClick={() => a.deployment_id && navigate(`/deployments/${a.deployment_id}`)}
              >
                {a.severity === 'critical' ? <AlertOctagon size={16} className="shrink-0 mt-0.5" /> : <AlertTriangle size={16} className="shrink-0 mt-0.5" />}
                <span>{a.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <Section title="Fleet">
        <StatTile label="Total" value={data.fleet.total} />
        <StatTile label="Healthy" value={data.fleet.healthy} />
        <StatTile label="Warning" value={data.fleet.warning} tone="warning" />
        <StatTile label="Offline" value={data.fleet.offline} tone="danger" />
        <StatTile label="Unknown" value={data.fleet.unknown} />
        <StatTile label="Under maintenance" value={data.fleet.under_maintenance} />
      </Section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div className="bg-surface border border-border rounded-lg p-4">
          <h3 className="text-sm font-semibold text-text mb-3">Deployments</h3>
          <div className="grid grid-cols-3 gap-3 mb-4">
            <Metric label="Outdated versions" value={data.deployments.outdated_versions} />
            <Metric label="Missing heartbeat" value={data.deployments.missing_heartbeat} />
            <Metric label="High risk" value={data.deployments.high_risk} />
          </div>
          <p className="text-xs text-muted mb-1">Recently deployed</p>
          <div className="space-y-1 mb-3">
            {data.deployments.recently_deployed.map((r, i) => (
              <div key={i} className="text-sm flex justify-between cursor-pointer hover:text-accent" onClick={() => navigate(`/deployments/${r.deployment_id}`)}>
                <span>{r.client_name}{r.version ? ` — ${r.version}` : ''}</span>
                <span className="text-xs text-muted">{formatDate(r.deployed_at)}</span>
              </div>
            ))}
            {data.deployments.recently_deployed.length === 0 && <p className="text-xs text-muted">None yet.</p>}
          </div>
          <p className="text-xs text-muted mb-1">Recently failed actions</p>
          <div className="space-y-1">
            {data.deployments.recently_failed.map((r, i) => (
              <div key={i} className="text-sm cursor-pointer hover:text-accent" onClick={() => navigate(`/deployments/${r.deployment_id}`)}>
                {r.client_name} — {r.action_key.replace('deployment_', '')}
                {r.last_error && <span className="text-xs text-danger block truncate">{r.last_error}</span>}
              </div>
            ))}
            {data.deployments.recently_failed.length === 0 && <p className="text-xs text-muted">None.</p>}
          </div>
        </div>

        <div className="space-y-4">
          <Section title="Support">
            <Metric label="Open" value={data.support.open} />
            <Metric label="Unassigned" value={data.support.unassigned} />
            <Metric label="Escalated" value={data.support.escalated} />
            <Metric label="Awaiting engineering" value={data.support.awaiting_engineering} />
          </Section>
          <Section title="Maintenance">
            <Metric label="Upcoming" value={data.maintenance.upcoming} />
            <Metric label="Active" value={data.maintenance.active} />
            <Metric label="Failed" value={data.maintenance.failed} />
          </Section>
          <Section title="Approvals">
            <Metric label="Pending" value={data.approvals.pending} />
            <Metric label="Overdue" value={data.approvals.overdue} />
            <Metric label="Approved (24h)" value={data.approvals.recently_approved} />
            <Metric label="Rejected (24h)" value={data.approvals.recently_rejected} />
          </Section>
          <Section title="Integrations">
            <Metric label="GitHub failures (24h)" value={data.integrations.github_webhook_failures} />
            <Metric label="Callback failures" value={data.integrations.deployment_callback_failures} />
            <Metric label="Notification failures (24h)" value={data.integrations.notification_failures} />
          </Section>
        </div>
      </div>
    </div>
  )
}
