"""GitHub integration domain models (HUB-Expansion.md Phase 3) — see
docs/integrations/github.md for the full design and the reasoning behind
every choice below. Table/column conventions mirror app/models.py and
app/workflow/models.py (UUID PKs, Mapped[...]/mapped_column(...), str-enum
+ SQLAlchemy Enum for HUB-owned closed sets only — see the module
docstring below on enums vs strings)."""
import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


# ── Enums — HUB-owned, small, closed sets only. A field sourced from
# GitHub's own vocabulary (GitHubPullRequest.state, GitHubIssue.state) is
# a plain string instead — see docs/integrations/github.md's "Enums vs
# strings" section for the full reasoning (extends ADR-001's rule for
# OperationalEvent.event_type to this domain). ────────────────────────────

class GitHubAuthMode(str, enum.Enum):
    pat = "pat"            # personal/fine-grained access token — the only mode implemented
    github_app = "github_app"  # reserved; not implemented (see docs/integrations/github.md)


class GitHubIntegrationStatus(str, enum.Enum):
    connected = "connected"
    disconnected = "disconnected"
    error = "error"


class GitHubWebhookEventStatus(str, enum.Enum):
    received = "received"
    processing = "processing"
    processed = "processed"
    failed = "failed"


# ── Models ─────────────────────────────────────────────────────────────

class GitHubIntegration(Base):
    """One configured GitHub connection — in practice, one per GitHub org.
    access_token_encrypted/webhook_secret_encrypted are Fernet-encrypted
    with services/crypto.py (the same module/key as Deployment
    .action_key_encrypted) — never returned by any API schema, never
    logged. status/last_synced_at/last_webhook_at/last_error(_at) are the
    "Integration Health" fields Phase 11's Integration Center will read
    later; kept current by sync.py and the webhook path."""
    __tablename__ = "github_integrations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    github_org: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    auth_mode: Mapped[GitHubAuthMode] = mapped_column(Enum(GitHubAuthMode), default=GitHubAuthMode.pat, nullable=False)
    access_token_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_secret_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[GitHubIntegrationStatus] = mapped_column(
        Enum(GitHubIntegrationStatus), default=GitHubIntegrationStatus.disconnected, nullable=False,
    )
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_webhook_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_error_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    repositories: Mapped[list["GitHubRepository"]] = relationship("GitHubRepository", back_populates="integration")


class GitHubRepository(Base):
    """One row per repository HUB knows about — added via manual sync
    (POST /github/integrations/{id}/repositories) or discovered from a
    webhook payload the first time it's seen. Never a mirror of GitHub's
    full repo object — just the fields HUB's own pages/lineage need."""
    __tablename__ = "github_repositories"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("github_integrations.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(300), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner: Mapped[str] = mapped_column(String(200), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(200), default="main", nullable=False)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    integration: Mapped["GitHubIntegration"] = relationship("GitHubIntegration", back_populates="repositories")


class DeploymentGitHubRepository(Base):
    """Many-to-many Deployment<->GitHubRepository — deliberately not
    one-to-one (HUB-Expansion.md Phase 3: "do not assume one repository =
    one deployment"). A deployment can pull from more than one repo
    (e.g. separate frontend/backend); a repo can serve more than one
    deployment (a shared single-tenant-per-deployment codebase).
    is_primary picks which mapped repo a deployment's detail page leads
    with when there's more than one. Composite PK, no own id — same shape
    as app/models.py's DeploymentStaffAssignment."""
    __tablename__ = "deployment_github_repositories"

    deployment_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("deployments.id", ondelete="CASCADE"), primary_key=True)
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("github_repositories.id", ondelete="CASCADE"), primary_key=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class GitHubPullRequest(Base):
    __tablename__ = "github_pull_requests"
    __table_args__ = (UniqueConstraint("repository_id", "number", name="uq_github_pr_repo_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("github_repositories.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    # GitHub's own vocabulary ("open"/"closed") — plain string, see this
    # module's docstring. Merged-vs-just-closed is derived from merged_at
    # being set, matching GitHub's own API shape (state=closed + merged_at
    # set = merged; state=closed + merged_at NULL = closed without merging).
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    author_login: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    merge_commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    merged_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    github_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GitHubIssue(Base):
    __tablename__ = "github_issues"
    __table_args__ = (UniqueConstraint("repository_id", "number", name="uq_github_issue_repo_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("github_repositories.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[int] = mapped_column(Integer, nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    author_login: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    github_updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GitHubRelease(Base):
    __tablename__ = "github_releases"
    __table_args__ = (UniqueConstraint("repository_id", "tag_name", name="uq_github_release_repo_tag"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("github_repositories.id", ondelete="CASCADE"), nullable=False)
    external_id: Mapped[int] = mapped_column(Integer, nullable=False)
    tag_name: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    target_commit_sha: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    is_prerelease: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class GitHubCommit(Base):
    """Only commits actually referenced by a push/PR-merge/release — never
    a full commit-log mirror (HUB-Expansion.md Phase 3: "do not mirror
    the entire GitHub database")."""
    __tablename__ = "github_commits"
    __table_args__ = (UniqueConstraint("repository_id", "sha", name="uq_github_commit_repo_sha"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    repository_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("github_repositories.id", ondelete="CASCADE"), nullable=False)
    sha: Mapped[str] = mapped_column(String(40), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    author_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    author_email: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    author_login: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    html_url: Mapped[str] = mapped_column(String(500), nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GitHubWebhookEvent(Base):
    """The raw persisted webhook — the actual idempotency mechanism
    (unique delivery_id) and the audit trail for every delivery, whether
    or not HUB knows how to interpret its event_type yet. correlation_id
    is generated once at receipt and reused by every OperationalEvent
    emitted while processing this one delivery (see
    docs/integrations/github.md's "Correlation / causation")."""
    __tablename__ = "github_webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    integration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("github_integrations.id", ondelete="CASCADE"), nullable=False)
    delivery_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    signature_valid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[GitHubWebhookEventStatus] = mapped_column(
        Enum(GitHubWebhookEventStatus), default=GitHubWebhookEventStatus.received, nullable=False,
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, default=uuid.uuid4)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
