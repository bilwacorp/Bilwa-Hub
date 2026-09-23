// Same order as App.tsx's sidebar nav — land on the first page this user
// actually has permission for, not always /deployments (a role scoped to
// e.g. just notifications would otherwise land on a "no access" page).
// Shared by LoginPage (the "no matching account" case still needs a plain
// login screen) and SsoCompletePage (the post-Authentik landing step).
const LANDING_PAGES: { permission: string; path: string }[] = [
  { permission: 'deployments.view', path: '/deployments' },
  { permission: 'tickets.view', path: '/tickets' },
  { permission: 'maintenance.view', path: '/maintenance-windows' },
  { permission: 'notifications.view', path: '/notifications' },
  { permission: 'approvals.view', path: '/approvals' },
  { permission: 'workflows.view', path: '/workflows' },
  { permission: 'staff.view', path: '/users' },
  { permission: 'rbac.manage', path: '/roles' },
]

export function landingPathFor(permissions: string[]): string {
  const landing = LANDING_PAGES.find((p) => permissions.includes(p.permission))
  return landing?.path ?? '/deployments'
}
