import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import axios from 'axios'
import { ArrowLeft } from 'lucide-react'
import api from '../../lib/api'
import { Card } from '../../components/ui/Card'
import { Badge } from '../../components/ui/Badge'
import { Button } from '../../components/ui/Button'
import type { Permission, Role } from '../../types'

function errorDetail(e: unknown): string | undefined {
  return axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
}

export default function RolePermissionsPage() {
  const { roleId } = useParams<{ roleId: string }>()
  const qc = useQueryClient()

  const { data: roles } = useQuery({
    queryKey: ['roles'],
    queryFn: () => api.get<Role[]>('/rbac/roles').then((r) => r.data),
  })
  const role = roles?.find((r) => r.id === roleId)

  const { data: catalog, isLoading: catalogLoading } = useQuery({
    queryKey: ['permissions-catalog'],
    queryFn: () => api.get<Permission[]>('/rbac/permissions').then((r) => r.data),
  })

  const { data: granted, isLoading: grantedLoading } = useQuery({
    queryKey: ['role-permissions', roleId],
    queryFn: () => api.get<string[]>(`/rbac/roles/${roleId}/permissions`).then((r) => r.data),
    enabled: !!roleId,
  })

  const [selected, setSelected] = useState<Set<string> | null>(null)
  useEffect(() => {
    if (granted && selected === null) setSelected(new Set(granted))
  }, [granted, selected])

  const saveMutation = useMutation({
    mutationFn: (permissions: string[]) => api.put(`/rbac/roles/${roleId}/permissions`, { permissions }),
    onSuccess: () => {
      toast.success('Permissions updated')
      qc.invalidateQueries({ queryKey: ['role-permissions', roleId] })
    },
    onError: (e) => toast.error(errorDetail(e) || 'Failed to update permissions'),
  })

  const grantedSorted = (granted ?? []).slice().sort()
  const selectedSorted = selected ? Array.from(selected).sort() : []
  const dirty = selected !== null && JSON.stringify(selectedSorted) !== JSON.stringify(grantedSorted)

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev ?? [])
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  const loading = catalogLoading || grantedLoading || selected === null

  // Grouped by resource, one Card per resource — a flat list of ~28
  // permissions is unwieldy; catalog order (server-defined) is preserved
  // within each group.
  const groups: { resource: string; permissions: Permission[] }[] = []
  for (const p of catalog ?? []) {
    let group = groups.find((g) => g.resource === p.resource)
    if (!group) { group = { resource: p.resource, permissions: [] }; groups.push(group) }
    group.permissions.push(p)
  }

  return (
    <div className="p-6 max-w-3xl">
      <Link to="/roles" className="inline-flex items-center gap-1.5 text-sm text-muted hover:text-text mb-4">
        <ArrowLeft size={14} /> Back to roles
      </Link>

      <div className="flex items-center justify-between mb-1">
        <h1 className="text-xl font-semibold text-text font-mono">{role?.name ?? '…'}</h1>
        {role && <Badge variant={role.is_system ? 'blue' : 'gray'}>{role.is_system ? 'Built-in' : 'Custom'}</Badge>}
      </div>
      {role?.description && <p className="text-sm text-muted mb-4">{role.description}</p>}

      {loading ? (
        <p className="text-sm text-muted mt-4">Loading…</p>
      ) : (
        <div className="space-y-4 mt-4">
          {groups.map((group) => (
            <Card key={group.resource} header={<span className="capitalize">{group.resource}</span>}>
              <div className="space-y-1">
                {group.permissions.map((p) => {
                  const key = `${p.resource}.${p.action}`
                  return (
                    <label key={key} className="flex items-start gap-3 py-2 border-b border-border last:border-0 cursor-pointer">
                      <input
                        type="checkbox"
                        className="mt-0.5"
                        checked={selected?.has(key) ?? false}
                        onChange={() => toggle(key)}
                      />
                      <div className="min-w-0">
                        <div className="font-mono text-sm text-text">{key}</div>
                        {p.description && <div className="text-xs text-muted mt-0.5">{p.description}</div>}
                      </div>
                    </label>
                  )
                })}
              </div>
            </Card>
          ))}
        </div>
      )}

      <div className="flex justify-end gap-2 mt-4 sticky bottom-6">
        <Button variant="secondary" disabled={!dirty} onClick={() => setSelected(new Set(granted))}>Reset</Button>
        <Button
          loading={saveMutation.isPending} disabled={!dirty}
          onClick={() => selected && saveMutation.mutate(Array.from(selected))}
        >
          Save Changes
        </Button>
      </div>
    </div>
  )
}
