import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, NavLink, useNavigate } from 'react-router-dom'
import { LayoutGrid, Ticket, CalendarClock, LogOut, Users, Bell, Menu, X, ShieldCheck } from 'lucide-react'
import { useAuthStore } from './stores/auth'
import api from './lib/api'
import { cn } from './lib/utils'
import { Badge } from './components/ui/Badge'
import LoginPage from './pages/auth/LoginPage'
import ForgotPasswordPage from './pages/auth/ForgotPasswordPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import DeploymentsListPage from './pages/deployments/DeploymentsListPage'
import DeploymentDetailPage from './pages/deployments/DeploymentDetailPage'
import SupportTicketsPage from './pages/tickets/SupportTicketsPage'
import MaintenanceWindowsPage from './pages/maintenance/MaintenanceWindowsPage'
import StaffUsersPage from './pages/users/StaffUsersPage'
import NotificationsPage from './pages/notifications/NotificationsPage'
import RolesPage from './pages/rbac/RolesPage'
import RolePermissionsPage from './pages/rbac/RolePermissionsPage'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

// Gates a route by permission ("resource.action") rather than a hardcoded
// role name — a custom role granted e.g. staff.manage should reach the
// Staff page too, not just the literal 'admin' role.
function RequirePermission({ permission, children }: { permission: string; children: React.ReactNode }) {
  const can = useAuthStore((s) => s.can)
  if (!can(permission)) return <Navigate to="/deployments" replace />
  return <>{children}</>
}

const NAV_ITEMS = [
  { to: '/deployments', label: 'Deployments', icon: LayoutGrid },
  { to: '/tickets', label: 'Support Tickets', icon: Ticket },
  { to: '/maintenance-windows', label: 'Maintenance', icon: CalendarClock },
  { to: '/notifications', label: 'Notifications', icon: Bell },
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
    await api.post('/auth/logout')
    clearAuth()
    navigate('/login')
  }

  const initial = user?.full_name?.[0] || user?.username?.[0] || '?'

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
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} onClick={onNavigate} className={({ isActive }) => navItemClass(isActive)}>
            <item.icon size={16} className="shrink-0" />
            {item.label}
          </NavLink>
        ))}
        {can('staff.manage') && (
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
      <Route path="/forgot-password" element={<ForgotPasswordPage />} />
      <Route path="/reset-password" element={<ResetPasswordPage />} />
      <Route path="/deployments" element={<RequireAuth><Shell><DeploymentsListPage /></Shell></RequireAuth>} />
      <Route path="/deployments/:deploymentId" element={<RequireAuth><Shell><DeploymentDetailPage /></Shell></RequireAuth>} />
      <Route path="/tickets" element={<RequireAuth><Shell><SupportTicketsPage /></Shell></RequireAuth>} />
      <Route path="/maintenance-windows" element={<RequireAuth><Shell><MaintenanceWindowsPage /></Shell></RequireAuth>} />
      <Route path="/notifications" element={<RequireAuth><Shell><NotificationsPage /></Shell></RequireAuth>} />
      <Route path="/users" element={<RequireAuth><RequirePermission permission="staff.manage"><Shell><StaffUsersPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/roles" element={<RequireAuth><RequirePermission permission="rbac.manage"><Shell><RolesPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="/roles/:roleId/permissions" element={<RequireAuth><RequirePermission permission="rbac.manage"><Shell><RolePermissionsPage /></Shell></RequirePermission></RequireAuth>} />
      <Route path="*" element={<Navigate to="/deployments" replace />} />
    </Routes>
  )
}
