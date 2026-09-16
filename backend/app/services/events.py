"""record_event() — the one function every router/hook calls to append an
OperationalEvent (see app/models.py's docstring on that table for the
overall design). Never commits: it only calls db.add(...), so the event
lands in the exact same transaction as the business mutation it describes
— per docs/architecture/current-state.md's database-first-integrity note,
an event and the change it records must succeed or fail together, not be
written by two separate commits where the second could be lost."""
import uuid
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OperationalEvent, OperationalEventStatus


def record_event(
    db: AsyncSession, *,
    event_type: str,
    source: str,
    actor_type: str,
    actor_id: Optional[uuid.UUID] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[uuid.UUID] = None,
    deployment_id: Optional[uuid.UUID] = None,
    correlation_id: Optional[uuid.UUID] = None,
    causation_id: Optional[uuid.UUID] = None,
    status: OperationalEventStatus = OperationalEventStatus.info,
    metadata: Optional[dict] = None,
) -> OperationalEvent:
    """correlation_id defaults to a fresh id when the caller has no
    existing chain to join — see core/event_types.py's module docstring
    for why cross-request/cross-system correlation chaining is
    HUB-Expansion.md Phase 2 scope, not Phase 1's."""
    event = OperationalEvent(
        event_type=event_type, source=source, actor_type=actor_type, actor_id=actor_id,
        entity_type=entity_type, entity_id=entity_id, deployment_id=deployment_id,
        correlation_id=correlation_id or uuid.uuid4(), causation_id=causation_id,
        status=status, event_metadata=metadata,
    )
    db.add(event)
    return event
