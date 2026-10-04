import uuid
from typing import Optional
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.db import execute, fetch_all
from app.routes.auth import require_auth
from app.services.kpi_service import get_kpi_targets_progress

router = APIRouter(prefix="/settings/targets", tags=["targets"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
async def targets_page(request: Request, _: None = Depends(require_auth)):
    targets = await get_kpi_targets_progress()
    return templates.TemplateResponse(
        request,
        "targets.html",
        {"targets": targets}
    )


@router.post("", response_class=HTMLResponse)
async def create_target(
    request: Request,
    metric: str = Form(...),
    period: str = Form(...),
    target_value: float = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    platform_id: Optional[str] = Form(None),
    _: None = Depends(require_auth)
):
    target_id = str(uuid.uuid4())
    plat_val = platform_id if platform_id and platform_id != "all" else None

    await execute(
        """
        INSERT INTO kpi_targets (id, platform_id, metric, period, target_value, start_date, end_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [target_id, plat_val, metric, period, target_value, start_date, end_date]
    )

    targets = await get_kpi_targets_progress()
    # If HTMX request, return the updated partial fragment
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            request,
            "partials/kpi_targets.html",
            {"targets": targets}
        )
    return RedirectResponse(url="/settings/targets", status_code=303)


@router.delete("/{target_id}")
async def delete_target(target_id: str, request: Request, _: None = Depends(require_auth)):
    await execute("DELETE FROM kpi_targets WHERE id = ?", [target_id])
    targets = await get_kpi_targets_progress()
    return templates.TemplateResponse(
        request,
        "partials/kpi_targets.html",
        {"targets": targets}
    )
