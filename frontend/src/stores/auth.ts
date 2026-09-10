import { create } from 'zustand'
import { persist } from 'zustand/middleware'

interface StaffUser {
  id: string
  username: string
  full_name: string | null
}

interface AuthState {
  user: StaffUser | null
  isAuthenticated: boolean
  setUser: (user: StaffUser) => void
  clearAuth: () => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,
      setUser: (user) => set({ user, isAuthenticated: true }),
      clearAuth: () => set({ user: null, isAuthenticated: false }),
    }),
    { name: 'bilwacorp-hub-auth' },
  ),
)
