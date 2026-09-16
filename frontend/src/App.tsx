import { Navigate, Route, Routes, Link, useLocation } from 'react-router-dom'
import { LayoutGrid, Ticket, CalendarClock, LogOut, Users, Bell } from 'lucide-react'
import { useAuthStore } from './stores/auth'
import api from './lib/api'
import LoginPage from './pages/auth/LoginPage'
import ForgotPasswordPage from './pages/auth/ForgotPasswordPage'
import ResetPasswordPage from './pages/auth/ResetPasswordPage'
import DeploymentsListPage from './pages/deployments/DeploymentsListPage'
import DeploymentDetailPage from './pages/deployments/DeploymentDetailPage'
import SupportTicketsPage from './pages/tickets/SupportTicketsPage'
import MaintenanceWindowsPage from './pages/maintenance/MaintenanceWindowsPage'
import StaffUsersPage from './pages/users/StaffUsersPage'
import NotificationsPage from './pages/notifications/NotificationsPage'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return <>{children}</>
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const role = useAuthStore((s) => s.user?.role)
  if (role !== 'admin') return <Navigate to="/deployments" replace />
  return <>{children}</>
}

function Shell({ children }: { children: React.ReactNode }) {
  const location = useLocation()
  const clearAuth = useAuthStore((s) => s.clearAuth)
  const role = useAuthStore((s) => s.user?.role)
  const navItems = [
    { to: '/deployments', label: 'Deployments', icon: LayoutGrid },
    { to: '/tickets', label: 'Support Tickets', icon: Ticket },
    { to: '/maintenance-windows', label: 'Maintenance', icon: CalendarClock },
    { to: '/notifications', label: 'Notifications', icon: Bell },
    ...(role === 'admin' ? [{ to: '/users', label: 'Staff', icon: Users }] : []),
  ]
  return (
    <div className="min-h-screen flex">
      <aside className="w-56 bg-primary text-white flex flex-col shrink-0">
        <div className="px-4 py-5 font-semibold">BilwaCorp Fleet Hub</div>
        <nav className="flex-1 space-y-1 px-2">
          {navItems.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={`flex items-center gap-2 px-3 py-2 rounded text-sm ${location.pathname.startsWith(item.to) ? 'bg-accent' : 'hover:bg-white/10'}`}
            >
              <item.icon size={16} />{item.label}
            </Link>
          ))}
        </nav>
        <button
          onClick={async () => { await api.post('/auth/logout'); clearAuth(); window.location.href = '/login' }}
          className="flex items-center gap-2 px-3 py-3 mx-2 mb-2 rounded text-sm hover:bg-white/10"
        >
          <LogOut size={16} /> Log out
        </button>
      </aside>
      <main className="flex-1 bg-background min-h-screen overflow-y-auto">{children}</main>
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
      <Route path="/users" element={<RequireAuth><RequireAdmin><Shell><StaffUsersPage /></Shell></RequireAdmin></RequireAuth>} />
      <Route path="*" element={<Navigate to="/deployments" replace />} />
    </Routes>
  )
}
