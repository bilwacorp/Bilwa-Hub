"""HUB-Expansion.md Phase 4 — the Customer directory (app/models.py's
Customer). Not row-scoped — small, fleet-wide reference data, same tier
as the GitHub catalog (core/permissions.py's customers.view/manage)."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import event_types as et
from app.core.permissions import CUSTOMERS_MANAGE, CUSTOMERS_VIEW, require_permission
from app.db.session import get_db
from app.models import Customer, User
from app.schemas import CustomerCreate, CustomerOut, CustomerUpdate
from app.services.events import record_event

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerOut])
async def list_customers(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(require_permission(*CUSTOMERS_VIEW)),
):
    rows = (await db.execute(select(Customer).order_by(Customer.name))).scalars().all()
    return [CustomerOut.model_validate(c) for c in rows]


@router.post("", response_model=CustomerOut, status_code=201)
async def create_customer(
    body: CustomerCreate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*CUSTOMERS_MANAGE)),
):
    customer = Customer(name=body.name, slug=body.slug, notes=body.notes)
    db.add(customer)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A customer with that slug already exists")
    await db.refresh(customer)
    record_event(
        db, event_type=et.CUSTOMER_CREATED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_CUSTOMER, entity_id=customer.id,
        metadata={"name": customer.name, "slug": customer.slug},
    )
    return CustomerOut.model_validate(customer)


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(
    customer_id: str, body: CustomerUpdate, db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_permission(*CUSTOMERS_MANAGE)),
):
    customer = (await db.execute(select(Customer).where(Customer.id == customer_id))).scalar_one_or_none()
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Customer not found")
    if body.name is not None:
        customer.name = body.name
    if body.notes is not None:
        customer.notes = body.notes
    await db.flush()
    record_event(
        db, event_type=et.CUSTOMER_UPDATED, source=et.SOURCE_HUB, actor_type=et.ACTOR_STAFF,
        actor_id=current_user.id, entity_type=et.ENTITY_CUSTOMER, entity_id=customer.id,
        metadata={"name": customer.name},
    )
    return CustomerOut.model_validate(customer)
