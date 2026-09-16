import { useForm } from 'react-hook-form'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import api from '../../lib/api'
import { errorMessage } from '../../lib/utils'
import { useAuthStore } from '../../stores/auth'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { PasswordInput } from '../../components/ui/PasswordInput'

type LoginForm = { username: string; password: string }

// Same order as App.tsx's sidebar nav — land on the first page this user
// actually has permission for, not always /deployments (a role scoped to
// e.g. just notifications would otherwise land on a "no access" page).
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

export default function LoginPage() {
  const navigate = useNavigate()
  const setUser = useAuthStore((s) => s.setUser)
  const { register, handleSubmit } = useForm<LoginForm>()

  const loginMutation = useMutation({
    mutationFn: (v: LoginForm) => api.post('/auth/login', v),
    onSuccess: async () => {
      const me = await api.get('/auth/me')
      setUser(me.data)
      const permissions: string[] = me.data.permissions ?? []
      const landing = LANDING_PAGES.find((p) => permissions.includes(p.permission))
      navigate(landing?.path ?? '/deployments')
    },
    onError: (e) => toast.error(errorMessage(e, 'Login failed')),
  })

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="flex justify-center mb-6">
          <img src="/logo-dark.png" alt="BilwaCorp" className="h-10 w-auto object-contain" />
        </div>
        <div className="bg-surface border border-border rounded-lg shadow-card p-6">
          <h1 className="text-lg font-semibold text-text mb-1">Fleet Hub</h1>
          <p className="text-sm text-muted mb-6">Staff sign-in</p>
          <form className="space-y-4" onSubmit={handleSubmit((v) => loginMutation.mutate(v))}>
            <Input label="Username" {...register('username', { required: true })} />
            <PasswordInput label="Password" {...register('password', { required: true })} />
            <div className="text-right -mt-2">
              <Link to="/forgot-password" className="text-sm text-accent hover:underline">Forgot password?</Link>
            </div>
            <Button type="submit" className="w-full" loading={loginMutation.isPending}>Sign in</Button>
          </form>
        </div>
      </div>
    </div>
  )
}
