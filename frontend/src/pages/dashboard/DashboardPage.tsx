import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  AlertOctagon,
  Server,
  LayoutGrid,
  Ticket,
  CalendarClock,
  CheckSquare,
  Plug,
  type LucideIcon,
} from 'lucide-react'
import api from '../../lib/api'
import { cn, formatDate } from '../../lib/utils'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import type { DashboardOut } from '../../types'

function CardHeader({ icon: Icon, title }: { icon: LucideIcon; title: string }) {
  return (
    <div className="flex items-center gap-2">
      <Icon size={16} className="text-muted" />
      <span>{title}</span>
    </div>
  )
}

function FleetTile({ label, value, dotClass }: { label: string; value: number; dotClass: string }) {
  return (
    <div>
      <div className="flex items-center gap-1.5 text-xs text-muted mb-1">
        <span className={cn('inline-block w-1.5 h-1.5 rounded-full', dotClass)} />
        {label}
      </div>
      <div className="text-2xl font-semibold text-text tabular-nums">{value}</div>
    </div>
  )
}

function MetricRow({ label, value, problem }: { label: string; value: number; problem?: boolean }) {
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-border/60 last:border-b-0">
      <span className="text-sm text-muted">{label}</span>
      {problem && value > 0 ? (
        <Badge variant="red">{value}</Badge>
      ) : (
        <span className="text-sm font-semibold text-text tabular-nums">{value}</span>
      )}
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

  const { fleet } = data
  const healthTotal = fleet.healthy + fleet.warning + fleet.offline + fleet.unknown
  const healthSegments = healthTotal > 0
    ? [
        { value: fleet.healthy, className: 'bg-success' },
        { value: fleet.warning, className: 'bg-warning' },
        { value: fleet.offline, className: 'bg-danger' },
        { value: fleet.unknown, className: 'bg-hint' },
      ]
    : []

  return (
    <div className="p-6 space-y-5">
      <h1 className="text-xl font-semibold text-text">Operations Dashboard</h1>

      {/* HUB-Expansion.md Phase 10: "This is more important than adding
          dozens of charts" — attention items lead the page. */}
      {data.attention.length > 0 && (
        <Card padding="sm" header={<CardHeader icon={AlertTriangle} title="Attention Required" />}>
          <div className="space-y-2">
            {data.attention.map((a, i) => (
              <div
                key={i}
                className={cn(
                  'flex items-start gap-2.5 text-sm px-3 py-2.5 rounded border-l-4 bg-surface',
                  a.severity === 'critical' ? 'border-danger' : 'border-warning',
                  a.deployment_id && 'cursor-pointer hover:bg-background',
                )}
                onClick={() => a.deployment_id && navigate(`/deployments/${a.deployment_id}`)}
              >
                {a.severity === 'critical' ? (
                  <AlertOctagon size={16} className="shrink-0 mt-0.5 text-danger" />
                ) : (
                  <AlertTriangle size={16} className="shrink-0 mt-0.5 text-warning" />
                )}
                <span className="flex-1 text-text">{a.message}</span>
                <Badge variant={a.severity === 'critical' ? 'red' : 'amber'}>
                  {a.severity === 'critical' ? 'Critical' : 'Warning'}
                </Badge>
              </div>
            ))}
          </div>
        </Card>
      )}

      <Card padding="sm" header={<CardHeader icon={Server} title="Fleet" />}>
        <div className="grid grid-cols-2 md:grid-cols-6 gap-4">
          <FleetTile label="Total" value={fleet.total} dotClass="bg-hint" />
          <FleetTile label="Healthy" value={fleet.healthy} dotClass="bg-success" />
          <FleetTile label="Warning" value={fleet.warning} dotClass="bg-warning" />
          <FleetTile label="Offline" value={fleet.offline} dotClass="bg-danger" />
          <FleetTile label="Unknown" value={fleet.unknown} dotClass="bg-hint" />
          <FleetTile label="Under maintenance" value={fleet.under_maintenance} dotClass="bg-info" />
        </div>
        {healthSegments.length > 0 && (
          <div className="flex w-full h-1.5 rounded-full overflow-hidden mt-4 bg-background">
            {healthSegments.map((s, i) =>
              s.value > 0 ? (
                <div key={i} className={s.className} style={{ width: `${(s.value / healthTotal) * 100}%` }} />
              ) : null,
            )}
          </div>
        )}
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
        <Card
          padding="sm"
          className="md:col-span-2"
          header={<CardHeader icon={LayoutGrid} title="Deployments" />}
        >
          <div className="grid grid-cols-3 gap-3 mb-3 pb-3 border-b border-border">
            <MetricRow label="Outdated versions" value={data.deployments.outdated_versions} problem />
            <MetricRow label="Missing heartbeat" value={data.deployments.missing_heartbeat} problem />
            <MetricRow label="High risk" value={data.deployments.high_risk} problem />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="text-xs font-medium text-muted mb-1.5">Recently deployed</p>
              <div className="space-y-1">
                {data.deployments.recently_deployed.map((r, i) => (
                  <div
                    key={i}
                    className="text-sm flex justify-between gap-2 cursor-pointer hover:text-accent"
                    onClick={() => navigate(`/deployments/${r.deployment_id}`)}
                  >
                    <span className="truncate">
                      {r.client_name}
                      {r.version ? ` — ${r.version}` : ''}
                    </span>
                    <span className="text-xs text-muted shrink-0">{formatDate(r.deployed_at)}</span>
                  </div>
                ))}
                {data.deployments.recently_deployed.length === 0 && <p className="text-xs text-muted">None yet.</p>}
              </div>
            </div>
            <div>
              <p className="text-xs font-medium text-muted mb-1.5">Recently failed actions</p>
              <div className="space-y-1">
                {data.deployments.recently_failed.map((r, i) => (
                  <div
                    key={i}
                    className="text-sm cursor-pointer hover:text-accent"
                    onClick={() => navigate(`/deployments/${r.deployment_id}`)}
                  >
                    {r.client_name} — {r.action_key.replace('deployment_', '')}
                    {r.last_error && <span className="text-xs text-danger block truncate">{r.last_error}</span>}
                  </div>
                ))}
                {data.deployments.recently_failed.length === 0 && <p className="text-xs text-muted">None.</p>}
              </div>
            </div>
          </div>
        </Card>

        <Card padding="sm" header={<CardHeader icon={Ticket} title="Support" />}>
          <MetricRow label="Open" value={data.support.open} />
          <MetricRow label="Unassigned" value={data.support.unassigned} problem />
          <MetricRow label="Escalated" value={data.support.escalated} problem />
          <MetricRow label="Awaiting engineering" value={data.support.awaiting_engineering} />
        </Card>

        <Card padding="sm" header={<CardHeader icon={CalendarClock} title="Maintenance" />}>
          <MetricRow label="Upcoming" value={data.maintenance.upcoming} />
          <MetricRow label="Active" value={data.maintenance.active} />
          <MetricRow label="Failed" value={data.maintenance.failed} problem />
        </Card>

        <Card padding="sm" header={<CardHeader icon={CheckSquare} title="Approvals" />}>
          <MetricRow label="Pending" value={data.approvals.pending} />
          <MetricRow label="Overdue" value={data.approvals.overdue} problem />
          <MetricRow label="Approved (24h)" value={data.approvals.recently_approved} />
          <MetricRow label="Rejected (24h)" value={data.approvals.recently_rejected} />
        </Card>

        <Card padding="sm" header={<CardHeader icon={Plug} title="Integrations" />}>
          <MetricRow label="GitHub failures (24h)" value={data.integrations.github_webhook_failures} problem />
          <MetricRow label="Callback failures" value={data.integrations.deployment_callback_failures} problem />
          <MetricRow label="Notification failures (24h)" value={data.integrations.notification_failures} problem />
        </Card>
      </div>
    </div>
  )
}
