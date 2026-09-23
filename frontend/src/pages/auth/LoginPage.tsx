import { useSearchParams } from 'react-router-dom'
import { API_BASE } from '../../lib/api'
import { Button } from '../../components/ui/Button'

// Staff login is Authentik OIDC only (see docs/adr/ADR-012-authentik-sso.md)
// — this button does a real browser navigation to the backend's /auth/login
// redirect, never an axios/fetch call, since the browser has to follow the
// redirect chain out to Authentik and back itself.
const ERROR_MESSAGES: Record<string, string> = {
  no_account: 'No BilwaCorp staff account matches that sign-in — ask an admin to add you.',
  sso_failed: 'Sign-in failed. Please try again.',
}

export default function LoginPage() {
  const [searchParams] = useSearchParams()
  const error = searchParams.get('error')

  return (
    <div className="min-h-screen flex items-center justify-center bg-background px-4">
      <div className="w-full max-w-sm">
        <div className="flex justify-center mb-6">
          <img src="/logo-dark.png" alt="BilwaCorp" className="h-10 w-auto object-contain" />
        </div>
        <div className="bg-surface border border-border rounded-lg shadow-card p-6">
          <h1 className="text-lg font-semibold text-text mb-1">Fleet Hub</h1>
          <p className="text-sm text-muted mb-6">Staff sign-in</p>
          {error && (
            <p className="text-sm text-red-600 mb-4">
              {ERROR_MESSAGES[error] ?? ERROR_MESSAGES.sso_failed}
            </p>
          )}
          <Button className="w-full" onClick={() => { window.location.href = `${API_BASE}/auth/login` }}>
            Sign in with Authentik
          </Button>
        </div>
      </div>
    </div>
  )
}
