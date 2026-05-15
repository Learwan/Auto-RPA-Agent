from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.marketplace.service import MarketplaceService
from src.models.automation import AutomationFlow

router = APIRouter()

_marketplace = MarketplaceService()


@router.post("/templates")
async def publish_template(
    name: str,
    description: str = "",
    category: str = "general",
    flow_definition: dict | None = None,
):
    if not flow_definition:
        raise HTTPException(status_code=400, detail="flow_definition is required")
    try:
        flow = AutomationFlow.model_validate(flow_definition)
        template = _marketplace.publish_template(flow, name, description, category)
        return template.to_dict()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/templates")
async def search_templates(
    query: str = "",
    category: str | None = None,
    sort_by: str = "downloads",
    limit: int = 20,
):
    templates = _marketplace.search_templates(query=query, category=category, sort_by=sort_by, limit=limit)
    return [t.to_dict() for t in templates]


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    template = _marketplace.get_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return template.to_dict()


@router.post("/templates/{template_id}/download")
async def download_template(template_id: str):
    template = _marketplace.download_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template_id": template_id, "flow_definition": template.flow_definition, "downloads": template.downloads}


@router.post("/templates/{template_id}/rate")
async def rate_template(template_id: str, user_id: str, score: int, comment: str = ""):
    if not _marketplace.rate_template(template_id, user_id, score, comment):
        raise HTTPException(status_code=400, detail="Rating failed")
    return {"status": "rated"}


@router.post("/templates/import")
async def import_template(template_json: str):
    template = _marketplace.import_template(template_json)
    if not template:
        raise HTTPException(status_code=400, detail="Import failed")
    return template.to_dict()


@router.get("/templates/{template_id}/export")
async def export_template(template_id: str):
    result = _marketplace.export_template(template_id)
    if not result:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template_json": result}


@router.get("/categories")
async def list_categories():
    return _marketplace.list_categories()
