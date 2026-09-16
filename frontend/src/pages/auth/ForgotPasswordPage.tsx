import { useForm } from 'react-hook-form'
import { useMutation } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import toast from 'react-hot-toast'
import api from '../../lib/api'
import { Button } from '../../components/ui/Button'
import { Input } from '../../components/ui/Input'

type ForgotPasswordForm = { username: string }

export default function ForgotPasswordPage() {
  const { register, handleSubmit } = useForm<ForgotPasswordForm>()

  const mutation = useMutation({
    // Always 204 from the backend's point of view, whether or not the
    // account/email exists — that's deliberate (avoids letting this form
    // enumerate staff usernames). A genuine request failure (network/500)
    // still surfaces as an error here so the form doesn't falsely claim
    // to have sent something.
    mutationFn: (v: ForgotPasswordForm) => api.post('/auth/forgot-password', v),
    onError: () => toast.error('Something went wrong — please try again'),
  })

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="flex justify-center mb-6">
          <img src="/logo-dark.png" alt="BilwaCorp" className="h-10 w-auto object-contain" />
        </div>
        <div className="bg-surface border border-border rounded-lg shadow-card p-6">
          <h1 className="text-lg font-semibold text-text mb-1">Forgot password</h1>
          <p className="text-sm text-muted mb-6">Enter your username and we'll email you a reset link, if there's an address on file.</p>

          {mutation.isSuccess ? (
            <div className="space-y-4">
              <p className="text-sm text-text">
                If that account exists and has an email on file, a reset link is on its way — it expires in 30 minutes.
              </p>
              <Link to="/login" className="text-sm text-accent hover:underline">Back to sign in</Link>
            </div>
          ) : (
            <form className="space-y-4" onSubmit={handleSubmit((v) => mutation.mutate(v))}>
              <Input label="Username" {...register('username', { required: true })} />
              <Button type="submit" className="w-full" loading={mutation.isPending}>Send reset link</Button>
              <Link to="/login" className="block text-center text-sm text-accent hover:underline">Back to sign in</Link>
            </form>
          )}
        </div>
      </div>
    </div>
  )
}
