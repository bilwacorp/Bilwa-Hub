export type DeploymentStatus = 'pending' | 'active' | 'suspended'
export type SupportTicketStatus = 'open' | 'in_progress' | 'resolved' | 'closed'
export type MaintenanceWindowStatus = 'planned' | 'in_progress' | 'completed' | 'cancelled'

export interface PendingRequest {
  id: string
  request_type: string
  requested_plan_name: string | null
  message: string | null
  requested_at: string
}

export interface UsageItem {
  key: string
  label: string
  current: number
  limit: number | null
}

export interface DeploymentSnapshot {
  plan_name: string | null
  subscription_status: string | null
  expiry_date: string | null
  trial_ends_at: string | null
  auto_renew: boolean | null
  usage: UsageItem[] | null
  pending_requests: PendingRequest[] | null
  app_version: string | null
  received_at: string
}

export type DerivedDeploymentStatus = 'never' | 'online' | 'stale' | 'offline' | 'maintenance'

export interface Deployment {
  id: string
  client_name: string
  slug: string
  base_url: string | null
  status: DeploymentStatus
  created_at: string
  latest_snapshot: DeploymentSnapshot | null
  heartbeat_age_seconds: number | null
  derived_status: DerivedDeploymentStatus
}

export interface DeploymentListResponse {
  total: number
  items: Deployment[]
}

export interface DeploymentCreateOut {
  id: string
  client_name: string
  slug: string
  status: DeploymentStatus
  registration_token: string
}

export interface SupportTicket {
  id: string
  deployment_id: string
  subject: string
  description: string
  priority: string
  status: SupportTicketStatus
  submitted_by_name: string | null
  submitted_by_email: string | null
  created_at: string
  resolved_at: string | null
}

export interface SupportTicketListResponse {
  total: number
  items: SupportTicket[]
}

// How hard an active window bites — escalating:
//   banner    → in-app notice only
//   read_only → deployment 503s all writes
//   lockout   → read_only + new sign-ins refused
export type MaintenanceMode = 'banner' | 'read_only' | 'lockout'

export interface MaintenanceWindow {
  id: string
  deployment_id: string | null
  scheduled_start: string
  scheduled_end: string
  description: string
  status: MaintenanceWindowStatus
  mode: MaintenanceMode
  created_at: string
}

export interface MaintenanceWindowListResponse {
  total: number
  items: MaintenanceWindow[]
}

// admin: full access, incl. managing other staff accounts.
// engineer: fleet access (deployments/tickets/maintenance) but not staff management.
export type StaffRole = 'admin' | 'engineer'

export interface StaffUser {
  id: string
  username: string
  full_name: string | null
  email: string | null
  phone: string | null
  is_active: boolean
  role: StaffRole | null
  created_at: string
}

export interface StaffUserListResponse {
  total: number
  items: StaffUser[]
}

// ── notifications ───────────────────────────────────────────────────────

export type NotificationChannel = 'email' | 'whatsapp'
export type NotificationStatus = 'pending' | 'sending' | 'sent' | 'failed' | 'cancelled'

export interface NotificationLog {
  id: string
  channel: NotificationChannel
  provider: string
  recipient: string
  subject: string | null
  template: string | null
  status: NotificationStatus
  error_message: string | null
  retry_count: number
  sent_at: string | null
  created_at: string
}

export interface NotificationLogListResponse {
  total: number
  items: NotificationLog[]
}
