from __future__ import annotations

from fastapi import APIRouter, Depends

from app.modules.admin.dependencies import require_admin


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
