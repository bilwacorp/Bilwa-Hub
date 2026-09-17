"""HUB-Expansion.md Phase 5's "integration abstraction rather than
coupling HUB to one CI provider" — `DeploymentProvider` is the interface
a CI/CD webhook handler codes against; `app/integrations/github/webhooks.py`
never inspects a raw GitHub Actions payload shape directly, only this
normalized `DeploymentEventData`. A future second provider (e.g. a
different CI system) implements this same interface and is wired in next
to `GitHubActionsProvider` in `github_actions.py` — no changes needed to
the webhook dispatch or `services/lineage.py`'s recording logic.

Deliberately no live-polling implementation of get_deployment_status/
get_release/get_commit here (HUB-Expansion.md rule 5: "do not introduce
unnecessary infrastructure") — every field these need is already present
either in the webhook payload itself or in data Phase 3's sync/webhooks
already persisted (GitHubRelease/GitHubCommit), so there's no case yet
where HUB needs to make a fresh outbound API call to answer these."""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class DeploymentEventData:
    """Provider-agnostic shape a `receive_deployment_event()` call
    normalizes a webhook payload into. `version` is left for the caller
    to resolve (see `services/lineage.py`'s release-matching helpers) —
    a provider only knows the commit/ref it saw, not what HUB's own
    GitHubRelease catalog has synced."""
    repository_external_id: int
    commit_sha: Optional[str]
    ref: Optional[str]
    status: str
    deployed_by: Optional[str]
    deployed_at: datetime
    description: Optional[str]


class DeploymentProvider(ABC):
    @abstractmethod
    def receive_deployment_event(self, payload: dict) -> Optional[DeploymentEventData]:
        """Parses a provider-specific webhook payload. Returns None when
        this payload isn't a deployment this provider recognizes as one
        (e.g. a workflow run that isn't a deploy, or one that didn't
        succeed) — the caller records nothing for a None result, per
        HUB-Expansion.md's "never blindly trust webhook payloads"."""

    def get_commit(self, event: DeploymentEventData) -> Optional[str]:
        return event.commit_sha

    def get_deployment_status(self, event: DeploymentEventData) -> str:
        return event.status
