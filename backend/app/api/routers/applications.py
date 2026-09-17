"""HUB-Expansion.md Phase 4 — the Application catalog (app/models.py's
Application). Not row-scoped, same tier as customers.py."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.permissions import APPLICATIONS_MANAGE, APPLICATIONS_VIEW, require_permission
from app.db.session import get_db
from app.models import Application, User
from app.schemas import ApplicationCreate, ApplicationOut, ApplicationUpdate
from app.services.events import record_event

router = APIRouter(prefix="/applications", tags=["applications"])


@router.get("", response_model=list[ApplicationOut])
async def list_applications(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_permission(*APPLICATIONS_VIEW)),
):
    rows = (await db.execute(select(Application).order_by(Application.name))).scalars().all()
    return [ApplicationOut.model_validate(a) for a in rows]


@router.post("", response_model=ApplicationOut, status_code=201)
async def create_application(
    body: ApplicationCreate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPLICATIONS_MANAGE)),
):
    application = Application(name=body.name, slug=body.slug, description=body.description)
    db.add(application)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "An application with that slug already exists")
    await db.refresh(application)
    record_event(
        db, event_type=et.APPLICATION_CREATED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_APPLICATION, entity_id=application.id,
        metadata={"name": application.name, "slug": application.slug},
    )
    return ApplicationOut.model_validate(application)


@router.patch("/{application_id}", response_model=ApplicationOut)
async def update_application(
    application_id: str, body: ApplicationUpdate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*APPLICATIONS_MANAGE)),
):
    application = (await db.execute(select(Application).where(Application.id == application_id))).scalar_one_or_none()
    if application is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Application not found")
    if body.name is not None:
        application.name = body.name
    if body.description is not None:
        application.description = body.description
    await db.flush()
    record_event(
        db, event_type=et.APPLICATION_UPDATED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_APPLICATION, entity_id=application.id,
        metadata={"name": application.name},
    )
    return ApplicationOut.model_validate(application)
