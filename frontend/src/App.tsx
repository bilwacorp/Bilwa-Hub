import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, NavLink, useNavigate } from 'react-router-dom'
import { LayoutGrid, Ticket, CalendarClock, LogOut, Users, Bell, Menu, X, ShieldCheck, CheckSquare, Workflow, History, Github, Building2, Package, LayoutDashboard, Plug, ListChecks } from 'lucide-react'
import { useAuthStore } from './stores/auth'
import api from './lib/api'
import { cn } from './lib/utils'
import { Badge } from './components/ui/Badge'
import LoginPage from './pages/auth/LoginPage'
import SsoCompletePage from './pages/auth/SsoCompletePage'
import DeploymentsListPage from './pages/deployments/DeploymentsListPage'
import DeploymentDetailPage from './pages/deployments/DeploymentDetailPage'
import SupportTicketsPage from './pages/tickets/SupportTicketsPage'
import SupportTicketDetailPage from './pages/tickets/SupportTicketDetailPage'
import MaintenanceWindowsPage from './pages/maintenance/MaintenanceWindowsPage'
import StaffUsersPage from './pages/users/StaffUsersPage'
import NotificationsPage from './pages/notifications/NotificationsPage'
import RolesPage from './pages/rbac/RolesPage'
import RolePermissionsPage from './pages/rbac/RolePermissionsPage'
import MyApprovalsPage from './pages/workflows/MyApprovalsPage'
import WorkflowListPage from './pages/workflows/WorkflowListPage'
import WorkflowDetailPage from './pages/workflows/WorkflowDetailPage'
import WorkflowDesignerPage from './pages/workflows/WorkflowDesignerPage'
import ApprovalRulesPage from './pages/workflows/ApprovalRulesPage'
import EventsPage from './pages/events/EventsPage'
import GitHubPage from './pages/github/GitHubPage'
import CustomersPage from './pages/customers/CustomersPage'
import ApplicationsPage from './pages/applications/ApplicationsPage'
import DashboardPage from './pages/dashboard/DashboardPage'
import IntegrationsPage from './pages/integrations/IntegrationsPage'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

// Gates a route by permission ("resource.action") rather than a hardcoded
// role name — a custom role granted e.g. staff.view should reach the
// Staff page too, not just the literal 'admin' role. Renders inline
// (doesn't redirect to another gated route like /deployments) — a role
// with no view permissions anywhere would otherwise bounce between two
// failing redirects forever.
function RequirePermission({ permission, children }: { permission: string; children: React.ReactNode }) {
  const can = useAuthStore((s) => s.can)
  if (!can(permission)) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background p-6">
        <p className="text-sm text-muted">You don't have access to this page.</p>
      </div>
    )
  }
  return <>{children}</>
}

// HUB-Expansion.md Phase 17 — grouped around operational workflows rather
// than a flat table-per-page list. Deliberately shallow: the doc's own
// suggested nav nests things like Maintenance > Calendar/Active/History
// or Support > Tickets/Escalations, but none of those sub-pages exist as
// distinct features (Maintenance is one page with a status column,
// there's no Escalations concept) — inventing empty pages just to match
// the doc's tree literally would violate its OWN "don't add pages merely
// because a table exists" rule. Only "Fleet" and "Workflows" actually
// group 2+ real pages; groups here render a header only when a role's
// permissions leave 2+ of that group's items visible (see SidebarContent
// below) — see docs/adr/ADR-011-observability-ux-deployment-page.md
// decision 2 for the reasoning per group.
const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard', icon: LayoutDashboard, permission: 'dashboard.view' },
  { to: '/deployments', label: 'Deployments', icon: LayoutGrid, permission: 'deployments.view', group: 'Fleet' },
  { to: '/customers', label: 'Customers', icon: Building2, permission: 'customers.view', group: 'Fleet' },
  { to: '/applications', label: 'Applications', icon: Package, permission: 'applications.view', group: 'Fleet' },
  { to: '/tickets', label: 'Support Tickets', icon: Ticket, permission: 'tickets.view' },
  { to: '/maintenance-windows', label: 'Maintenance', icon: CalendarClock, permission: 'maintenance.view' },
  { to: '/approvals', label: 'Approvals', icon: CheckSquare, permission: 'approvals.view' },
  { to: '/github', label: 'GitHub', icon: Github, permission: 'github.view' },
  { to: '/integrations', label: 'Integrations', icon: Plug, permission: 'integrations.view' },
  // Approval Rules previously had a route (/workflow-rules) but NO nav
  // link at all — a real pre-existing gap this grouping fixes.
  { to: '/workflows', label: 'Workflows', icon: Workflow, permission: 'workflows.view', group: 'Workflows' },
  { to: '/workflow-rules', label: 'Approval Rules', icon: ListChecks, permission: 'workflow_rules.view', group: 'Workflows' },
  { to: '/notifications', label: 'Notifications', icon: Bell, permission: 'notifications.view' },
  { to: '/events', label: 'Audit / Events', icon: History, permission: 'events.view' },
] as const

// Flush left accent bar rather than a filled pill — reads calmer against a
// short, flat nav list. border-l-[3px] stays on inactive rows too (just
// transparent) so the bar appearing/disappearing never shifts layout.
function navItemClass(isActive: boolean): string {
  return cn(
    'flex items-center gap-2.5 pl-[11px] pr-2.5 py-2.5 rounded-r-lg text-sm font-medium transition-colors duration-150 border-l-[3px]',
    isActive
      ? 'border-accent bg-accent/[0.07] text-accent font-semibold'
      : 'border-transparent text-muted hover:text-text hover:bg-background',
  )
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const navigate = useNavigate()
  const user = useAuthStore((s) => s.user)
  const clearAuth = useAuthStore((s) => s.clearAuth)
  const can = useAuthStore((s) => s.can)
  const role = user?.role

  const handleLogout = async () => {
    const { data } = await api.post('/auth/logout')
    clearAuth()
    // Full redirect through Authentik's end-session endpoint when one's
    // available, so the SSO session actually ends too — otherwise a staff
    // member could click "Sign in with Authentik" right back in without a
    // credential prompt (see docs/adr/ADR-012-authentik-sso.md).
    if (data?.sso_logout_url) {
      window.location.href = data.sso_logout_url
    } else {
      navigate('/login')
    }
  }

  const initial = user?.full_name?.[0] || user?.username?.[0] || '?'

  const visibleItems = NAV_ITEMS.filter((item) => can(item.permission))
  const itemGroup = (item: (typeof NAV_ITEMS)[number]): string | undefined => ('group' in item ? item.group : undefined)
  // A group header renders only above the FIRST visible item of a run of
  // 2+ consecutive same-group items — a role whose permissions leave just
  // one item of a group visible sees it as a plain flat item instead.
  const groupCounts = visibleItems.reduce<Record<string, number>>((acc, item) => {
    const g = itemGroup(item)
    if (g) acc[g] = (acc[g] ?? 0) + 1
    return acc
  }, {})

  return (
    <>
      <div className="flex items-center gap-2.5 px-4 h-16 border-b border-border shrink-0">
        <img src="/logo-dark.png" alt="BilwaCorp" className="h-7 w-auto object-contain shrink-0" />
        <div className="min-w-0">
          <span className="block font-semibold text-text text-[15px] leading-tight truncate">Fleet Hub</span>
          <span className="block text-xs text-muted leading-tight mt-0.5">Fleet Management</span>
        </div>
      </div>
      <nav className="flex-1 overflow-y-auto px-2.5 py-3 space-y-0.5">
        {visibleItems.map((item, i) => {
          const group = itemGroup(item)
          const prevGroup = i > 0 ? itemGroup(visibleItems[i - 1]) : undefined
          const showGroupHeader = !!group && groupCounts[group] > 1 && group !== prevGroup
          return (
            <div key={item.to}>
              {showGroupHeader && (
                <div className="pt-3 pb-1 pl-3 text-[11px] font-semibold uppercase tracking-wide text-muted/70">{group}</div>
              )}
              <NavLink to={item.to} onClick={onNavigate} className={({ isActive }) => navItemClass(isActive)}>
                <item.icon size={16} className="shrink-0" />
                {item.label}
              </NavLink>
            </div>
          )
        })}
        {can('staff.view') && (
          <NavLink to="/users" onClick={onNavigate} className={({ isActive }) => navItemClass(isActive)}>
            <Users size={16} className="shrink-0" />
            Staff
          </NavLink>
        )}
        {can('rbac.manage') && (
          <NavLink to="/roles" onClick={onNavigate} className={({ isActive }) => navItemClass(isActive)}>
            <ShieldCheck size={16} className="shrink-0" />
            Roles & Permissions
          </NavLink>
        )}
      </nav>
      <div className="border-t border-border p-2.5 shrink-0">
        <div className="flex items-center gap-2.5 px-1.5 py-1 rounded-xl">
          <div className="w-9 h-9 rounded-full bg-accent/10 ring-1 ring-border flex items-center justify-center shrink-0">
            <span className="text-xs font-semibold text-accent">{initial.toUpperCase()}</span>
          </div>
          <div className="flex-1 min-w-0 flex flex-col gap-1">
            <p className="text-sm font-medium text-text truncate">{user?.full_name || user?.username}</p>
            {role && <Badge variant={role === 'admin' ? 'blue' : 'gray'} className="text-xs w-fit capitalize">{role}</Badge>}
          </div>
          <button onClick={handleLogout} className="text-muted hover:text-danger transition-colors p-1 rounded shrink-0" title="Log out">
            <LogOut size={15} />
          </button>
        </div>
      </div>
    </>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    document.body.style.overflow = mobileOpen ? 'hidden' : ''
    return () => { document.body.style.overflow = '' }
  }, [mobileOpen])

  return (
    <div className="min-h-screen bg-background">
      {/* Desktop sidebar */}
      <aside className="hidden lg:flex fixed top-0 left-0 h-screen w-[240px] bg-surface border-r border-border flex-col z-40">
        <SidebarContent />
      </aside>

      {/* Mobile top bar */}
      <header className="lg:hidden fixed top-0 left-0 right-0 h-14 bg-surface border-b border-border flex items-center px-4 gap-3 z-30">
        <button onClick={() => setMobileOpen(true)} className="p-2 -ml-2 text-text" aria-label="Open menu">
          <Menu size={20} />
        </button>
        <img src="/logo-dark.png" alt="BilwaCorp" className="h-6 w-auto object-contain" />
        <span className="font-semibold text-text text-sm truncate">BilwaCorp Fleet Hub</span>
      </header>

      {/* Mobile drawer */}
      {mobileOpen && (
        <div className="lg:hidden fixed inset-0 z-50">
          <div className="absolute inset-0 bg-black/40" onClick={() => setMobileOpen(false)} />
          <aside className="absolute top-0 left-0 h-screen w-[240px] max-w-[80vw] bg-surface border-r border-border flex flex-col shadow-card">
            <SidebarContent onNavigate={() => setMobileOpen(false)} />
          </aside>
          <button
            onClick={() => setMobileOpen(false)}
            aria-label="Close menu"
            className="absolute top-3 right-3 p-2 rounded-full bg-surface/90 text-text shadow-subtle"
            style={{ left: 'calc(min(240px, 80vw) + 8px)' }}
          >
            <X size={18} />
          </button>
        </div>
      )}

      <main className="lg:ml-[240px] min-h-screen overflow-y-auto pt-14 lg:pt-0">{children}</main>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/sso/complete" element={<SsoCompletePage />} />
      <Route path="/deployments" element={<RequireAuth><RequirePermission permission="deployments.view"><Shell><DeploymentsListPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/deployments/:deploymentId" element={<RequireAuth><RequirePermission permission="deployments.view"><Shell><DeploymentDetailPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/customers" element={<RequireAuth><RequirePermission permission="customers.view"><Shell><CustomersPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/applications" element={<RequireAuth><RequirePermission permission="applications.view"><Shell><ApplicationsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/tickets" element={<RequireAuth><RequirePermission permission="tickets.view"><Shell><SupportTicketsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/tickets/:ticketId" element={<RequireAuth><RequirePermission permission="tickets.view"><Shell><SupportTicketDetailPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/maintenance-windows" element={<RequireAuth><RequirePermission permission="maintenance.view"><Shell><MaintenanceWindowsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/notifications" element={<RequireAuth><RequirePermission permission="notifications.view"><Shell><NotificationsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/users" element={<RequireAuth><RequirePermission permission="staff.view"><Shell><StaffUsersPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/roles" element={<RequireAuth><RequirePermission permission="rbac.manage"><Shell><RolesPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/roles/:roleId/permissions" element={<RequireAuth><RequirePermission permission="rbac.manage"><Shell><RolePermissionsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/approvals" element={<RequireAuth><RequirePermission permission="approvals.view"><Shell><MyApprovalsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/workflows" element={<RequireAuth><RequirePermission permission="workflows.view"><Shell><WorkflowListPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/workflows/:id" element={<RequireAuth><RequirePermission permission="workflows.view"><Shell><WorkflowDetailPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/workflows/:id/versions/:versionId" element={<RequireAuth><RequirePermission permission="workflows.view"><Shell><WorkflowDesignerPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/workflow-rules" element={<RequireAuth><RequirePermission permission="workflow_rules.view"><Shell><ApprovalRulesPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/github" element={<RequireAuth><RequirePermission permission="github.view"><Shell><GitHubPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/events" element={<RequireAuth><RequirePermission permission="events.view"><Shell><EventsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/dashboard" element={<RequireAuth><RequirePermission permission="dashboard.view"><Shell><DashboardPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/integrations" element={<RequireAuth><RequirePermission permission="integrations.view"><Shell><IntegrationsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="*" element={<Navigate to="/deployments" replace />} />
    </Routes>
  )
}
