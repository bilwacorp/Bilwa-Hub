export type DeploymentStatus = 'pending' | 'active' | 'suspended'
export type SupportTicketStatus = 'open' | 'in_progress' | 'resolved' | 'closed'
export type MaintenanceWindowStatus =
  | 'draft' | 'approval_required' | 'approved' | 'planned' | 'notification' | 'in_progress' | 'completed'
  | 'failed' | 'cancelled'
export type DeploymentEnvironment = 'production' | 'staging' | 'development' | 'uat'
export type DeploymentReleaseSource = 'manual' | 'heartbeat_inferred' | 'github_actions' | 'ci_cd'

// HUB-Expansion.md Phase 4 — deployment lineage.
export interface Customer {
  id: string
  name: string
  slug: string
  notes: string | null
  created_at: string
}

export interface Application {
  id: string
  name: string
  slug: string
  description: string | null
  created_at: string
}

export interface DeploymentRelease {
  id: string
  deployment_id: string
  version: string | null
  repository_id: string | null
  release_id: string | null
  commit_sha: string | null
  source: DeploymentReleaseSource
  deployed_by: string | null
  deployed_at: string
  notes: string | null
  created_at: string
  repository_full_name: string | null
  release_tag_name: string | null
}

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

// Minimal staff shape for the deployment-assignment picker — not the full
// StaffUser (role/is_active/created_at), since GET /deployments/staff-options
// is DEPLOYMENTS_MANAGE, not STAFF_MANAGE (see StaffUser).
export interface StaffOption {
  id: string
  username: string
  full_name: string | null
}

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
  // Staff assigned to this deployment — when non-empty, narrows who gets
  // notified about its tickets/subscription requests (see backend's
  // services/notifications/recipients.py).
  assigned_staff: StaffOption[]
  // HUB-Expansion.md Phase 4 lineage.
  environment: DeploymentEnvironment
  customer_id: string | null
  application_id: string | null
  customer: Customer | null
  application: Application | null
  current_release: DeploymentRelease | null
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

// HUB-Expansion.md Phase 6 — ticket <-> engineering links.
export type TicketLinkType = 'github_issue' | 'github_pull_request' | 'github_release' | 'maintenance_window'

export interface SupportTicketLink {
  id: string
  ticket_id: string
  link_type: TicketLinkType
  target_id: string
  created_by: string | null
  created_at: string
  label: string
  url: string | null
  target_status: string | null
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
  expected_impact: string | null
  actual_impact: string | null
  approved_by: string | null
  created_by: string | null
  created_at: string
}

export interface MaintenanceWindowListResponse {
  total: number
  items: MaintenanceWindow[]
}

// A role name — roles are dynamic now (see Role below), not a fixed set of
// two. The two seeded roles are still 'admin' and 'engineer', but a custom
// role can be any string matching ^[a-z0-9_]+$.
export type StaffRole = string

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

// ── rbac (roles & permission catalog) ───────────────────────────────────

export interface Permission {
  id: string
  resource: string
  action: string
  description: string | null
}

export interface Role {
  id: string
  name: string
  description: string | null
  is_system: boolean
  created_at: string
}

// ── workflow / approval engine ──────────────────────────────────────────

export type WorkflowVersionStatus = 'draft' | 'published' | 'archived'
export type WorkflowInstanceStatus = 'running' | 'completed' | 'rejected' | 'cancelled' | 'error'
export type WorkflowTaskStatus = 'pending' | 'approved' | 'rejected' | 'cancelled' | 'skipped'

export interface WorkflowDefinition {
  id: string
  key: string
  name: string
  description: string | null
  is_active: boolean
  is_system: boolean
  created_by: string | null
  created_at: string
}

export interface WorkflowVersionListItem {
  id: string
  definition_id: string
  version: number
  process_id: string
  status: WorkflowVersionStatus
  notes: string | null
  created_by: string | null
  created_at: string
  published_at: string | null
}

export interface WorkflowVersion extends WorkflowVersionListItem {
  bpmn_xml: string
}

export interface ValidationStep {
  task_id: string
  name: string | null
  step_key: string | null
  rule_key: string | null
}

export interface ValidationResult {
  is_valid: boolean
  errors: string[]
  steps: ValidationStep[]
}

export interface WorkflowInstance {
  id: string
  instance_code: string
  definition_id: string
  version_id: string
  business_object_type: string
  business_object_id: string
  status: WorkflowInstanceStatus
  result: string | null
  started_by: string
  started_at: string
  completed_at: string | null
  error_detail: string | null
}

export interface WorkflowHistoryEntry {
  id: string
  instance_id: string
  task_id: string | null
  event_type: string
  actor_user_id: string | null
  actor_username: string | null
  from_status: string | null
  to_status: string | null
  comment: string | null
  detail: Record<string, unknown> | null
  created_at: string
}

export interface WorkflowVariable {
  id: string
  name: string
  value: unknown
  value_type: string
  is_input: boolean
  updated_at: string
  updated_by: string | null
}

export interface WorkflowTask {
  id: string
  instance_id: string
  task_spec_name: string
  task_name: string | null
  step_key: string | null
  status: WorkflowTaskStatus
  assigned_user_id: string | null
  candidate_user_ids: string[]
  rule_id: string | null
  due_at: string | null
  acted_by: string | null
  acted_at: string | null
  comment: string | null
  created_at: string
}

export type RuleActionType = 'assign_approver' | 'auto_approve' | 'skip_step' | 'set_variable'
export type ApproverStrategy = 'casbin_role' | 'explicit_users'

export interface RuleCondition {
  id: string
  expression: string
  description: string | null
  sequence: number
}

export interface RuleConditionInput {
  expression: string
  description?: string | null
}

export interface RuleActionOut {
  id: string
  action_type: RuleActionType
  strategy: ApproverStrategy | null
  role_name: string | null
  user_ids: string[] | null
  variable_name: string | null
  variable_value: unknown
  config: Record<string, unknown> | null
  sequence: number
}

export interface RuleActionInput {
  action_type: RuleActionType
  strategy?: ApproverStrategy | null
  role_name?: string | null
  user_ids?: string[] | null
  variable_name?: string | null
  variable_value?: unknown
  config?: Record<string, unknown> | null
}

export interface ApprovalRule {
  id: string
  key: string
  name: string
  definition_id: string | null
  step_key: string | null
  priority: number
  is_active: boolean
  stop_on_match: boolean
  is_system: boolean
  created_by: string | null
  created_at: string
  updated_at: string
  conditions: RuleCondition[]
  actions: RuleActionOut[]
}

// ── operational events (HUB-Expansion.md Phase 1) ─────────────────────────

export type OperationalEventStatus = 'info' | 'pending' | 'success' | 'failure'

export interface OperationalEvent {
  id: string
  event_type: string
  source: string
  actor_type: string
  actor_id: string | null
  entity_type: string | null
  entity_id: string | null
  deployment_id: string | null
  customer_id: string | null
  correlation_id: string
  causation_id: string | null
  status: OperationalEventStatus
  event_metadata: Record<string, unknown> | null
  created_at: string
}

export interface OperationalEventListResponse {
  total: number
  items: OperationalEvent[]
}

// ── deployment action executions (HUB-Expansion.md Phase 12/13) ───────────

export type DeploymentActionExecutionStatus = 'pending' | 'executing' | 'executed' | 'failed'
export type DeploymentActionAttemptStatus = 'success' | 'failure'

export interface DeploymentActionAttempt {
  id: string
  attempt_number: number
  status: DeploymentActionAttemptStatus
  error: string | null
  response: Record<string, unknown> | null
  triggered_by: string | null
  started_at: string
  finished_at: string
}

export interface DeploymentActionExecution {
  id: string
  workflow_instance_id: string
  deployment_id: string
  action_key: string
  idempotency_key: string
  correlation_id: string | null
  status: DeploymentActionExecutionStatus
  attempt_count: number
  last_attempted_at: string | null
  last_error: string | null
  last_response: Record<string, unknown> | null
  created_at: string
  updated_at: string
  attempts: DeploymentActionAttempt[]
}

// ── GitHub integration (HUB-Expansion.md Phase 3) ──────────────────────────

export type GitHubIntegrationStatus = 'connected' | 'disconnected' | 'error'

export interface GitHubIntegration {
  id: string
  name: string
  github_org: string
  auth_mode: 'pat' | 'github_app'
  status: GitHubIntegrationStatus
  has_access_token: boolean
  has_webhook_secret: boolean
  last_synced_at: string | null
  last_webhook_at: string | null
  last_error: string | null
  last_error_at: string | null
  created_at: string
  webhook_url_path: string
}

export interface GitHubRepository {
  id: string
  integration_id: string
  external_id: number
  full_name: string
  name: string
  owner: string
  default_branch: string
  html_url: string
  is_active: boolean
  last_synced_at: string | null
  created_at: string
}

export interface GitHubPullRequest {
  id: string
  repository_id: string
  number: number
  title: string
  state: string
  is_draft: boolean
  author_login: string | null
  html_url: string
  merge_commit_sha: string | null
  opened_at: string
  merged_at: string | null
  closed_at: string | null
}

export interface GitHubIssue {
  id: string
  repository_id: string
  number: number
  title: string
  state: string
  author_login: string | null
  html_url: string
  opened_at: string
  closed_at: string | null
}

export interface GitHubRelease {
  id: string
  repository_id: string
  tag_name: string
  name: string | null
  html_url: string
  target_commit_sha: string | null
  is_prerelease: boolean
  is_draft: boolean
  published_at: string | null
}

export interface GitHubCommit {
  id: string
  repository_id: string
  sha: string
  message: string | null
  author_name: string | null
  author_login: string | null
  html_url: string
  committed_at: string
}

export interface DeploymentGitHubInfo {
  repositories: GitHubRepository[]
  primary_repository: GitHubRepository | null
  latest_commit: GitHubCommit | null
  latest_pull_request: GitHubPullRequest | null
  latest_release: GitHubRelease | null
}

// ── operations dashboard (HUB-Expansion.md Phase 10) ──────────────────────

export interface DashboardFleetSummary {
  total: number
  healthy: number
  warning: number
  offline: number
  unknown: number
  under_maintenance: number
}

export interface DashboardReleaseItem {
  deployment_id: string
  client_name: string
  version: string | null
  deployed_at: string
}

export interface DashboardFailedActionItem {
  deployment_id: string
  client_name: string
  action_key: string
  last_error: string | null
  last_attempted_at: string | null
}

export interface DashboardDeploymentsSummary {
  recently_deployed: DashboardReleaseItem[]
  recently_failed: DashboardFailedActionItem[]
  outdated_versions: number
  missing_heartbeat: number
  high_risk: number
}

export interface DashboardSupportSummary {
  open: number
  unassigned: number
  escalated: number
  awaiting_engineering: number
}

export interface DashboardMaintenanceSummary {
  upcoming: number
  active: number
  failed: number
}

export interface DashboardApprovalsSummary {
  pending: number
  overdue: number
  recently_approved: number
  recently_rejected: number
}

export interface DashboardIntegrationsSummary {
  github_webhook_failures: number
  deployment_callback_failures: number
  notification_failures: number
}

export interface DashboardAttentionItem {
  kind: string
  severity: 'warning' | 'critical'
  message: string
  deployment_id: string | null
}

export interface DashboardOut {
  fleet: DashboardFleetSummary
  deployments: DashboardDeploymentsSummary
  support: DashboardSupportSummary
  maintenance: DashboardMaintenanceSummary
  approvals: DashboardApprovalsSummary
  integrations: DashboardIntegrationsSummary
  attention: DashboardAttentionItem[]
}

// ── integration center (HUB-Expansion.md Phase 11) ────────────────────────

export interface IntegrationSummary {
  key: string
  name: string
  status: 'connected' | 'disconnected' | 'error' | 'not_configured'
  connected: boolean
  last_sync_at: string | null
  last_webhook_at: string | null
  last_error: string | null
  last_error_at: string | null
  detail: string | null
  integration_id: string | null
}
