import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { format, parseISO } from 'date-fns'
import axios from 'axios'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** A 422 body's `detail` is FastAPI's list of Pydantic error objects
 * ({type, loc, msg, ...}), not a string — rendering it directly into a
 * toast crashes React ("Objects are not valid as a React child"). Every
 * other error shape this app returns (403/404/409/502) is a plain string,
 * so only this one needs unwrapping. */
export function errorMessage(err: unknown, fallback: string): string {
  if (!axios.isAxiosError(err)) return fallback
  const detail = (err.response?.data as { detail?: unknown } | undefined)?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string } | undefined
    return first?.msg || fallback
  }
  return fallback
}

export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '—'
  try {
    const d = dateStr.includes('T') ? parseISO(dateStr) : new Date(dateStr + 'T00:00:00')
    return format(d, 'dd-MMM-yyyy HH:mm')
  } catch {
    return dateStr
  }
}
