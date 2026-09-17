import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.integrations.github.models import GitHubAuthMode, GitHubIntegrationStatus


class GitHubIntegrationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    github_org: str = Field(min_length=1, max_length=200)
    access_token: str = Field(min_length=1)
    webhook_secret: str = Field(min_length=1)


class GitHubAppManifestRequest(BaseModel):
    """Both optional — "Set up GitHub App" (decision #9,
    docs/adr/ADR-004-github-app-auth.md) needs no required input at all,
    since the manifest's URLs are derived from the request itself
    (api.py's create_app_manifest)."""
    name: Optional[str] = Field(default=None, max_length=200)
    github_org: Optional[str] = Field(default=None, max_length=200, pattern=r"^[A-Za-z0-9_.-]*$")


class GitHubIntegrationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    # Omitted (not just empty-string) means "leave the stored credential
    # alone" — this update endpoint never requires re-entering a secret
    # just to rename an integration.
    access_token: Optional[str] = Field(default=None, min_length=1)
    webhook_secret: Optional[str] = Field(default=None, min_length=1)


class GitHubIntegrationOut(BaseModel):
    id: uuid.UUID
    name: str
    github_org: str
    auth_mode: GitHubAuthMode
    installation_id: Optional[int] = None
    status: GitHubIntegrationStatus
    # Never the token/secret themselves — only whether one is on file.
    has_access_token: bool
    has_webhook_secret: bool
    last_synced_at: Optional[datetime]
    last_webhook_at: Optional[datetime]
    last_error: Optional[str]
    last_error_at: Optional[datetime]
    created_at: datetime
    webhook_url_path: str

    model_config = {"from_attributes": True}


class GitHubTestConnectionResult(BaseModel):
    ok: bool
    detail: str
    github_login: Optional[str] = None


class GitHubRepositoryOut(BaseModel):
    id: uuid.UUID
    integration_id: uuid.UUID
    external_id: int
    full_name: str
    name: str
    owner: str
    default_branch: str
    html_url: str
    is_active: bool
    last_synced_at: Optional[datetime]
    created_at: datetime

    model_config = {"from_attributes": True}


class GitHubRepositoryAddRequest(BaseModel):
    # Restricted to GitHub's own allowed owner/repo character set — this
    # value is interpolated directly into a URL path in client.py's
    # get_repository ("/repos/{full_name}"); a strict pattern here (not
    # just min/max length) is the server-side guard against a crafted
    # value doing anything other than naming a repository.
    full_name: str = Field(min_length=1, max_length=300, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", description='"owner/repo"')


class GitHubRepositoryDeploymentMapping(BaseModel):
    deployment_id: uuid.UUID
    is_primary: bool = False


class GitHubRepositoryDeploymentMappingsUpdate(BaseModel):
    mappings: List[GitHubRepositoryDeploymentMapping]


class GitHubPullRequestOut(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    number: int
    title: str
    state: str
    is_draft: bool
    author_login: Optional[str]
    html_url: str
    merge_commit_sha: Optional[str]
    opened_at: datetime
    merged_at: Optional[datetime]
    closed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class GitHubIssueOut(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    number: int
    title: str
    state: str
    author_login: Optional[str]
    html_url: str
    opened_at: datetime
    closed_at: Optional[datetime]

    model_config = {"from_attributes": True}


class GitHubReleaseOut(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    tag_name: str
    name: Optional[str]
    html_url: str
    target_commit_sha: Optional[str]
    is_prerelease: bool
    is_draft: bool
    published_at: Optional[datetime]

    model_config = {"from_attributes": True}


class GitHubCommitOut(BaseModel):
    id: uuid.UUID
    repository_id: uuid.UUID
    sha: str
    message: Optional[str]
    author_name: Optional[str]
    author_login: Optional[str]
    html_url: str
    committed_at: datetime

    model_config = {"from_attributes": True}


class DeploymentGitHubInfo(BaseModel):
    """GET /deployments/{id}/github's shape — deliberately its own
    schema (not just a list of GitHubRepositoryOut) since a deployment
    detail page wants "the" primary repo plus its most-recent commit/PR/
    release at a glance, not a raw table."""
    repositories: List[GitHubRepositoryOut]
    primary_repository: Optional[GitHubRepositoryOut] = None
    latest_commit: Optional[GitHubCommitOut] = None
    latest_pull_request: Optional[GitHubPullRequestOut] = None
    latest_release: Optional[GitHubReleaseOut] = None
