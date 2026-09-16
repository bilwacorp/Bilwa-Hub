"""Payload -> local-row upserts shared by sync.py (manual/initial sync,
fed from GitHub REST API list responses) and webhooks.py (fed from
webhook payloads). GitHub's webhook payload for `pull_request`/`issues`/
`release` embeds the *same* resource representation the REST API returns
for that resource, so one parser per resource type is correct for both
callers — no duplicated parsing logic between "synced" and "webhooked"
data."""
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.github.models import GitHubCommit, GitHubIssue, GitHubPullRequest, GitHubRelease, GitHubRepository


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


async def upsert_pull_request(db: AsyncSession, repository: GitHubRepository, data: dict) -> GitHubPullRequest:
    pr = (await db.execute(
        select(GitHubPullRequest).where(GitHubPullRequest.repository_id == repository.id, GitHubPullRequest.number == data["number"])
    )).scalar_one_or_none()
    if pr is None:
        pr = GitHubPullRequest(repository_id=repository.id, number=data["number"], opened_at=_parse_dt(data["created_at"]))
        db.add(pr)
    pr.external_id = data["id"]
    pr.title = data["title"]
    pr.state = data["state"]
    pr.is_draft = bool(data.get("draft", False))
    pr.author_login = (data.get("user") or {}).get("login")
    pr.html_url = data["html_url"]
    pr.merge_commit_sha = data.get("merge_commit_sha")
    pr.merged_at = _parse_dt(data.get("merged_at"))
    pr.closed_at = _parse_dt(data.get("closed_at"))
    pr.github_updated_at = _parse_dt(data["updated_at"])
    await db.flush()
    return pr


async def upsert_issue(db: AsyncSession, repository: GitHubRepository, data: dict) -> GitHubIssue:
    issue = (await db.execute(
        select(GitHubIssue).where(GitHubIssue.repository_id == repository.id, GitHubIssue.number == data["number"])
    )).scalar_one_or_none()
    if issue is None:
        issue = GitHubIssue(repository_id=repository.id, number=data["number"], opened_at=_parse_dt(data["created_at"]))
        db.add(issue)
    issue.external_id = data["id"]
    issue.title = data["title"]
    issue.state = data["state"]
    issue.author_login = (data.get("user") or {}).get("login")
    issue.html_url = data["html_url"]
    issue.closed_at = _parse_dt(data.get("closed_at"))
    issue.github_updated_at = _parse_dt(data["updated_at"])
    await db.flush()
    return issue


async def upsert_release(db: AsyncSession, repository: GitHubRepository, data: dict) -> GitHubRelease:
    release = (await db.execute(
        select(GitHubRelease).where(GitHubRelease.repository_id == repository.id, GitHubRelease.tag_name == data["tag_name"])
    )).scalar_one_or_none()
    if release is None:
        release = GitHubRelease(repository_id=repository.id, tag_name=data["tag_name"])
        db.add(release)
    release.external_id = data["id"]
    release.name = data.get("name")
    release.html_url = data["html_url"]
    release.target_commit_sha = data.get("target_commitish")
    release.is_prerelease = bool(data.get("prerelease", False))
    release.is_draft = bool(data.get("draft", False))
    release.published_at = _parse_dt(data.get("published_at"))
    await db.flush()
    return release


async def upsert_commit(db: AsyncSession, repository: GitHubRepository, data: dict) -> GitHubCommit:
    """`data` is one entry of a push webhook's `commits` array — a
    lighter shape than the REST API's commit object (no separate
    author-login field; `author`/`committer` here are just name+email).
    author_login is left NULL for push-sourced commits — GitHub doesn't
    include it in this payload shape, and resolving it would need an
    extra API call per commit, which HUB-Expansion.md's "do not mirror
    the entire GitHub database" argues against for a field only cosmetic
    value would use."""
    commit = (await db.execute(
        select(GitHubCommit).where(GitHubCommit.repository_id == repository.id, GitHubCommit.sha == data["id"])
    )).scalar_one_or_none()
    if commit is None:
        commit = GitHubCommit(repository_id=repository.id, sha=data["id"])
        db.add(commit)
    commit.message = (data.get("message") or "").split("\n", 1)[0][:2000]
    author = data.get("author") or {}
    commit.author_name = author.get("name")
    commit.author_email = author.get("email")
    commit.html_url = data.get("url", "")
    commit.committed_at = _parse_dt(data.get("timestamp")) or datetime.utcnow()
    await db.flush()
    return commit
