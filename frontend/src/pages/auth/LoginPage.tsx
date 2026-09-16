import { useForm } from 'react-hook-form'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import axios from 'axios'
import api from '../../lib/api'
import { useAuthStore } from '../../stores/auth'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'
import { PasswordInput } from '../../components/ui/PasswordInput'

type LoginForm = { username: string; password: string }

export default function LoginPage() {
  const navigate = useNavigate()
  const setUser = useAuthStore((s) => s.setUser)
  const { register, handleSubmit } = useForm<LoginForm>()

  const loginMutation = useMutation({
    mutationFn: (v: LoginForm) => api.post('/auth/login', v),
    onSuccess: async () => {
      const me = await api.get('/auth/me')
      setUser(me.data)
      navigate('/deployments')
    },
    onError: (e) => {
      const detail = axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
      toast.error(detail || 'Login failed')
    },
  })

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-full max-w-sm bg-surface border border-border rounded-lg shadow-card p-6">
        <h1 className="text-xl font-semibold text-text mb-1">BilwaCorp Fleet Hub</h1>
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
  )
}
