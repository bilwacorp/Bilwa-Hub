import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { CheckCircle2, XCircle, HelpCircle } from 'lucide-react'
import api from '../../lib/api'
import { formatDate, errorMessage as errMsg } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import type { IntegrationSummary } from '../../types'

const STATUS_BADGE: Record<IntegrationSummary['status'], { variant: 'green' | 'red' | 'gray'; icon: typeof CheckCircle2 }> = {
  connected: { variant: 'green', icon: CheckCircle2 },
  error: { variant: 'red', icon: XCircle },
  disconnected: { variant: 'gray', icon: HelpCircle },
  not_configured: { variant: 'gray', icon: HelpCircle },
}

export default function IntegrationsPage() {
  const qc = useQueryClient()
  const can = useAuthStore((s) => s.can)
  const { data, isLoading } = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api.get<IntegrationSummary[]>('/integrations').then((r) => r.data),
    refetchInterval: 60_000,
  })

  const testGithubMutation = useMutation({
    mutationFn: (integrationId: string) => api.post(`/github/integrations/${integrationId}/test-connection`),
    onSuccess: (r) => {
      const ok = (r.data as { ok: boolean }).ok
      toast[ok ? 'success' : 'error']((r.data as { detail: string }).detail)
      qc.invalidateQueries({ queryKey: ['integrations'] })
    },
    onError: (e) => toast.error(errMsg(e, 'Test connection failed')),
  })

  return (
    <div className="p-6">
      <h1 className="text-xl font-semibold text-text mb-1">Integrations</h1>
      <p className="text-sm text-muted mb-4">
        HUB-Expansion.md Phase 11 — at-a-glance health for every external system this hub talks to. Credentials are
        never shown here; manage them from each integration's own page.
      </p>
      {isLoading && <p className="text-sm text-muted">Loading…</p>}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {(data ?? []).map((card) => {
          const badge = STATUS_BADGE[card.status]
          const Icon = badge.icon
          return (
            <div key={card.key} className="bg-surface border border-border rounded-lg p-4">
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-sm font-semibold text-text">{card.name}</h3>
                <Badge variant={badge.variant} className="inline-flex items-center gap-1">
                  <Icon size={12} /> {card.status.replace('_', ' ')}
                </Badge>
              </div>
              <div className="space-y-1 text-xs text-muted">
                <div>Last sync: {card.last_sync_at ? formatDate(card.last_sync_at) : '—'}</div>
                {card.last_webhook_at && <div>Last webhook: {formatDate(card.last_webhook_at)}</div>}
                {card.last_error && (
                  <div className="text-danger">
                    Last error ({card.last_error_at ? formatDate(card.last_error_at) : '—'}): {card.last_error}
                  </div>
                )}
                {card.detail && <div className="pt-1">{card.detail}</div>}
              </div>
              {card.integration_id && can('github.test_connection') && (
                <Button
                  size="sm" variant="secondary" className="mt-3" loading={testGithubMutation.isPending}
                  onClick={() => testGithubMutation.mutate(card.integration_id!)}
                >
                  Test Connection
                </Button>
              )}
              {card.key === 'email' && can('notifications.test_send') && (
                <a href="/notifications" className="inline-block mt-3 text-xs text-accent hover:underline">Send a test email →</a>
              )}
              {card.key === 'whatsapp' && can('notifications.test_send') && (
                <a href="/notifications" className="inline-block mt-3 text-xs text-accent hover:underline">Send a test WhatsApp message →</a>
              )}
              {card.key === 'github' && card.status === 'not_configured' && can('github.manage') && (
                <a href="/github" className="inline-block mt-3 text-xs text-accent hover:underline">Configure GitHub →</a>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
