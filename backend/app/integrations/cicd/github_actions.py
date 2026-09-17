"""GitHub Actions' `workflow_run` webhook event as a DeploymentProvider —
HUB-Expansion.md Phase 5's "initial implementation can support GitHub
Actions." `workflow_run` (not GitHub's separate Deployments API /
`deployment_status` event) is the signal this repo's own deploy pipeline
actually produces — see docs/adr/ADR-005-cicd-integration.md for why.

`_looks_like_deploy` is a heuristic, not a certainty: GitHub's API gives
no first-class "this workflow run was a deployment" flag, so this infers
it from the workflow's own name/file path containing "deploy" (matching
common convention — "Deploy", "deploy.yml", "deploy-production.yml").
A workflow that deploys under a different name is silently not picked
up; Phase 4's manual `POST /deployments/{id}/releases` entry point still
exists to backfill/correct history for exactly that case."""
import re
from datetime import datetime
from typing import Optional

from app.integrations.cicd.provider import DeploymentEventData, DeploymentProvider


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)


def _looks_like_deploy(workflow_run: dict) -> bool:
    name = (workflow_run.get("name") or "").lower()
    path = (workflow_run.get("path") or "").lower()
    return "deploy" in name or "deploy" in path


# A ref that looks like a semantic version or a 'v'-prefixed tag —
# distinguishes a real release ref ('2.8.15', 'v2.8.15') from a branch
# name ('main', 'production') that `head_branch` might otherwise hold.
_VERSION_RE = re.compile(r"^v?\d+(\.\d+){1,3}")


def looks_like_version(ref: Optional[str]) -> bool:
    return bool(ref and _VERSION_RE.match(ref))


class GitHubActionsProvider(DeploymentProvider):
    def receive_deployment_event(self, payload: dict) -> Optional[DeploymentEventData]:
        workflow_run = payload.get("workflow_run") or {}
        if payload.get("action") != "completed":
            return None
        if workflow_run.get("status") != "completed" or workflow_run.get("conclusion") != "success":
            return None
        if not _looks_like_deploy(workflow_run):
            return None

        actor = workflow_run.get("triggering_actor") or workflow_run.get("actor") or {}
        deployed_at = _parse_dt(workflow_run.get("updated_at")) or _parse_dt(workflow_run.get("created_at")) or datetime.utcnow()
        repository = payload.get("repository") or {}
        return DeploymentEventData(
            repository_external_id=repository.get("id"),
            commit_sha=workflow_run.get("head_sha"),
            ref=workflow_run.get("head_branch"),
            status="success",
            deployed_by=actor.get("login"),
            deployed_at=deployed_at,
            description=f"{workflow_run.get('name') or 'workflow'} run #{workflow_run.get('run_number')}",
        )
