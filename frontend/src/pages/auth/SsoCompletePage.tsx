import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import api from '../../lib/api'
import { landingPathFor } from '../../lib/landing'
import { useAuthStore } from '../../stores/auth'

// Backend's GET /auth/callback (see docs/adr/ADR-012-authentik-sso.md) sets
// the session cookie and redirects the browser here — a plain 302, not
// something axios drove — so this page's only job is the one thing the
// old LoginPage's login-mutation onSuccess used to do: fetch /auth/me,
// populate the auth store, and land on the first permitted page.
export default function SsoCompletePage() {
  const navigate = useNavigate()
  const setUser = useAuthStore((s) => s.setUser)

  useEffect(() => {
    api.get('/auth/me')
      .then((res) => {
        setUser(res.data)
        navigate(landingPathFor(res.data.permissions ?? []), { replace: true })
      })
      .catch(() => navigate('/login?error=sso_failed', { replace: true }))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <p className="text-sm text-muted">Signing you in…</p>
    </div>
  )
}
