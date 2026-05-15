from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException

from src.audit.logger import AuditAction, AuditLogger, AuditResource
from src.auth.rbac import Permission, RBACManager, Role

router = APIRouter()

_rbac = RBACManager()
_audit = AuditLogger()


@router.post("/users")
async def create_user(username: str, role: str = "viewer"):
    try:
        user = _rbac.create_user(username, Role(role))
        _audit.log("system", AuditAction.CREATE, AuditResource.USER, user.user_id, f"Created user {username}")
        return {"user_id": user.user_id, "username": user.username, "role": user.role.value}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")


@router.get("/users")
async def list_users():
    users = _rbac.list_users()
    return [{"user_id": u.user_id, "username": u.username, "role": u.role.value, "active": u.active} for u in users]


@router.get("/users/{user_id}")
async def get_user(user_id: str):
    user = _rbac.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user_id": user.user_id, "username": user.username, "role": user.role.value, "active": user.active}


@router.put("/users/{user_id}/role")
async def update_role(user_id: str, role: str):
    try:
        user = _rbac.update_role(user_id, Role(role))
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        _audit.log("system", AuditAction.UPDATE, AuditResource.USER, user_id, f"Role changed to {role}")
        return {"user_id": user.user_id, "role": user.role.value}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid role: {role}")


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(user_id: str):
    if _rbac.deactivate_user(user_id):
        _audit.log("system", AuditAction.UPDATE, AuditResource.USER, user_id, "User deactivated")
        return {"status": "deactivated"}
    raise HTTPException(status_code=404, detail="User not found")


@router.get("/permissions/{user_id}/{permission}")
async def check_permission(user_id: str, permission: str):
    try:
        has_perm = _rbac.check_permission(user_id, Permission(permission))
        return {"user_id": user_id, "permission": permission, "granted": has_perm}
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid permission: {permission}")


@router.get("/audit")
async def query_audit_log(
    user_id: str | None = None,
    action: str | None = None,
    resource: str | None = None,
    limit: int = 100,
):
    kwargs = {"limit": limit}
    if user_id:
        kwargs["user_id"] = user_id
    if action:
        with contextlib.suppress(ValueError):
            kwargs["action"] = AuditAction(action)
    if resource:
        with contextlib.suppress(ValueError):
            kwargs["resource"] = AuditResource(resource)
    entries = _audit.query(**kwargs)
    return [e.to_dict() for e in entries]
