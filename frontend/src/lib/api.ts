import axios, { type AxiosError } from 'axios'
import toast from 'react-hot-toast'
import { useAuthStore } from '../stores/auth'

export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api/v1'

export const api = axios.create({
  baseURL: API_BASE,
  headers: { 'Content-Type': 'application/json' },
  // Token lives in an HttpOnly cookie (see backend auth.py) — withCredentials
  // is what makes the browser actually send it.
  withCredentials: true,
})

api.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    const status = error.response?.status
    if (status === 401) {
      const isLoginRequest = error.config?.url?.includes('/auth/login')
      if (!isLoginRequest) {
        useAuthStore.getState().clearAuth()
        window.location.href = '/login'
      }
    } else if (status === 403) {
      toast.error('You do not have permission to perform this action.')
    } else if (status === 502) {
      toast.error((error.response?.data as { detail?: string })?.detail || 'That deployment could not be reached.')
    } else if (status && status >= 500) {
      toast.error('Server error. Please try again later.')
    }
    return Promise.reject(error)
  },
)

export default api
