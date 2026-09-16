import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { StaffRole } from '../types'

interface LoggedInUser {
  id: string
  username: string
  full_name: string | null
  role: StaffRole | null
  // "resource.action" strings from /auth/me — gate UI elements with
  // useAuthStore.getState().can('resource.action') instead of a hardcoded
  // role-name check, which breaks once custom roles exist (see backend's
  // services/rbac.get_permissions_for_user).
  permissions: string[]
}

interface AuthState {
  user: LoggedInUser | null
  isAuthenticated: boolean
  setUser: (user: LoggedInUser) => void
  clearAuth: () => void
  can: (permission: string) => boolean
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set, get) => ({
      user: null,
      isAuthenticated: false,
      setUser: (user) => set({ user, isAuthenticated: true }),
      clearAuth: () => set({ user: null, isAuthenticated: false }),
      can: (permission) => get().user?.permissions.includes(permission) ?? false,
    }),
    { name: 'bilwacorp-hub-auth' },
  ),
)
