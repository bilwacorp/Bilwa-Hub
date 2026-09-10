"""Casbin enforcer singleton — ported near-verbatim from PoultryOS-CBP's
core/casbin_enforcer.py. Phase 1 only ever seeds a single 'admin' role (see
alembic/versions/002_seed_admin.py) but the engine itself is generic."""
import os
import casbin
from casbin_async_sqlalchemy_adapter import Adapter

from app.db.session import engine

MODEL_PATH = os.path.join(os.path.dirname(__file__), "rbac_model.conf")

_enforcer: casbin.AsyncEnforcer | None = None


async def init_enforcer() -> casbin.AsyncEnforcer:
    global _enforcer
    adapter = Adapter(engine)
    _enforcer = casbin.AsyncEnforcer(MODEL_PATH, adapter)
    await _enforcer.load_policy()
    return _enforcer


def get_enforcer() -> casbin.AsyncEnforcer:
    if _enforcer is None:
        raise RuntimeError("Casbin enforcer not initialized — init_enforcer() must run in app lifespan first")
    return _enforcer
