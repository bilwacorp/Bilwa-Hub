import { useForm } from 'react-hook-form'
import { useMutation } from '@tanstack/react-query'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import toast from 'react-hot-toast'
import axios from 'axios'
import api from '../../lib/api'
import { Button } from '../../components/ui/Button'
import { PasswordInput } from '../../components/ui/PasswordInput'

type ResetPasswordForm = { new_password: string; confirm_password: string }

export default function ResetPasswordPage() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token')
  const { register, handleSubmit, watch, formState: { errors } } = useForm<ResetPasswordForm>()

  const mutation = useMutation({
    mutationFn: (v: ResetPasswordForm) => api.post('/auth/reset-password', { token, new_password: v.new_password }),
    onSuccess: () => {
      toast.success('Password reset — sign in with your new password')
      navigate('/login')
    },
    onError: (e) => {
      const detail = axios.isAxiosError(e) ? (e.response?.data as { detail?: string })?.detail : undefined
      toast.error(detail || 'That reset link is invalid or has expired')
    },
  })

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="flex justify-center mb-6">
          <img src="/logo-dark.png" alt="BilwaCorp" className="h-10 w-auto object-contain" />
        </div>
        <div className="bg-surface border border-border rounded-lg shadow-card p-6">
          <h1 className="text-lg font-semibold text-text mb-1">Reset password</h1>
          <p className="text-sm text-muted mb-6">Choose a new password for your account.</p>

          {!token ? (
            <div className="space-y-4">
              <p className="text-sm text-danger">This reset link is missing its token — request a new one.</p>
              <Link to="/forgot-password" className="text-sm text-accent hover:underline">Request a new link</Link>
            </div>
          ) : (
            <form className="space-y-4" onSubmit={handleSubmit((v) => mutation.mutate(v))}>
              <PasswordInput
                label="New password"
                error={errors.new_password?.message}
                {...register('new_password', { required: true, minLength: { value: 8, message: 'At least 8 characters' } })}
              />
              <PasswordInput
                label="Confirm new password"
                error={errors.confirm_password?.message}
                {...register('confirm_password', {
                  required: true,
                  validate: (v) => v === watch('new_password') || "Passwords don't match",
                })}
              />
              <Button type="submit" className="w-full" loading={mutation.isPending}>Reset password</Button>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
