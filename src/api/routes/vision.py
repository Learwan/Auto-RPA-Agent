import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import resolve_local_vision_model, settings
from src.llm.gui_grounding import GUIGroundingEngine
from src.llm.local_engine import LocalLLMEngine
from src.llm.multimodal_service import (
    MultimodalLLMService,
    VisionError,
    VisionModelUnavailableError,
    VisionTimeoutError,
)
from src.monitoring.metrics import MetricsCollector

router = APIRouter()
logger = logging.getLogger(__name__)


class AnalyzeScreenshotRequest(BaseModel):
    image_base64: str
    task_description: str | None = None


class GroundActionRequest(BaseModel):
    image_base64: str
    action_description: str
    action_type: str
    image_width: int = 1920
    image_height: int = 1080


class GroundBatchRequest(BaseModel):
    image_base64: str
    actions: list[dict]
    image_width: int = 1920
    image_height: int = 1080


class CompareScreenshotsRequest(BaseModel):
    before_image_base64: str
    after_image_base64: str


class ExtractUIElementsRequest(BaseModel):
    image_base64: str


class GenerateWorkflowRequest(BaseModel):
    screenshots: list[str]
    operations: list[dict]


class VerifyActionRequest(BaseModel):
    before_image_base64: str
    after_image_base64: str
    expected_action: dict


def _check_vision_enabled() -> None:
    if not settings.VISION_ENABLED:
        raise HTTPException(status_code=503, detail="Vision model is disabled. Set VISION_ENABLED=true.")


def _handle_vision_error(e: Exception) -> HTTPException:
    if isinstance(e, VisionModelUnavailableError):
        return HTTPException(status_code=503, detail="Vision model unavailable. Ensure the model is loaded.")
    if isinstance(e, VisionTimeoutError):
        return HTTPException(status_code=504, detail=f"Vision request timed out: {e}")
    if isinstance(e, VisionError):
        return HTTPException(status_code=502, detail=str(e))
    msg = str(e)
    if "not loaded" in msg or "mlx" in msg.lower() or "engine" in msg.lower():
        return HTTPException(status_code=503, detail=f"Vision model unavailable: {msg}")
    logger.error(f"Vision error: {e}", exc_info=True)
    return HTTPException(status_code=500, detail="Vision processing failed")


@router.get("/status")
async def vision_status():
    runtime = LocalLLMEngine.get_runtime_snapshot()
    grounding = GUIGroundingEngine().get_runtime_status()
    return {
        "enabled": settings.VISION_ENABLED,
        "configured": bool(settings.LOCAL_LLM_ENABLED),
        "model": resolve_local_vision_model(settings) if settings.LOCAL_LLM_ENABLED else None,
        "engine": settings.LOCAL_LLM_ENGINE if settings.LOCAL_LLM_ENABLED else None,
        "runtime_loaded": runtime["vision_loaded"],
        "runtime_backend": runtime["vision_backend"],
        "runtime_device": runtime["vision_device"],
        "resolved_engine": runtime["resolved_engine"],
        "grounding_threshold": settings.VISION_GROUNDING_CONFIDENCE_THRESHOLD,
        "cache_enabled": settings.VISION_CACHE_ENABLED,
        "grounding": grounding,
    }


@router.get("/metrics")
async def vision_metrics():
    collector = MetricsCollector.get_instance()
    summary = collector.get_summary()
    return {
        "total_requests": summary.total_requests,
        "total_errors": summary.total_errors,
        "error_rate": round(summary.error_rate, 4),
        "avg_latency_ms": round(summary.avg_latency_ms, 2),
        "p95_latency_ms": round(summary.p95_latency_ms, 2),
        "total_tokens": summary.total_tokens,
        "total_images": summary.total_images,
    }


@router.post("/analyze-screenshot")
async def analyze_screenshot(request: AnalyzeScreenshotRequest):
    _check_vision_enabled()
    service = MultimodalLLMService()
    try:
        result = await service.analyze_screenshot(
            screenshot_base64=request.image_base64,
            task_description=request.task_description,
        )
        return {
            "app_name": result.app_name,
            "window_title": result.window_title,
            "ui_state_description": result.ui_state_description,
            "actionable_elements": [
                {
                    "element_type": el.element_type,
                    "label": el.label,
                    "description": el.description,
                    "bbox": list(el.bbox) if el.bbox else None,
                    "confidence": el.confidence,
                    "actionable": el.actionable,
                    "suggested_action": el.suggested_action,
                }
                for el in result.actionable_elements
            ],
            "user_intent": result.user_intent,
            "suggested_next_actions": result.suggested_next_actions,
            "confidence": result.confidence,
            "thinking_trace": result.thinking_trace,
        }
    except (VisionError, VisionModelUnavailableError, VisionTimeoutError) as e:
        raise _handle_vision_error(e)
    except Exception as e:
        raise _handle_vision_error(e)


@router.post("/ground-action")
async def ground_action(request: GroundActionRequest):
    _check_vision_enabled()
    engine = GUIGroundingEngine()
    try:
        result = await engine.ground_action(
            screenshot_base64=request.image_base64,
            action_description=request.action_description,
            action_type=request.action_type,
            image_width=request.image_width,
            image_height=request.image_height,
        )
        return {
            "found": result.found,
            "element_type": result.element_type,
            "element_label": result.element_label,
            "bbox_pixel": list(result.bbox_pixel) if result.bbox_pixel else None,
            "bbox_normalized": list(result.bbox_normalized) if result.bbox_normalized else None,
            "confidence": result.confidence,
            "alternative_targets": result.alternative_targets,
            "reasoning": result.reasoning,
        }
    except Exception as e:
        raise _handle_vision_error(e)


@router.post("/ground-batch")
async def ground_batch(request: GroundBatchRequest):
    _check_vision_enabled()
    engine = GUIGroundingEngine()
    try:
        results = await engine.ground_batch(
            screenshot_base64=request.image_base64,
            actions=request.actions,
            image_width=request.image_width,
            image_height=request.image_height,
        )
        return {
            "results": [
                {
                    "found": r.found,
                    "element_type": r.element_type,
                    "element_label": r.element_label,
                    "bbox_pixel": list(r.bbox_pixel) if r.bbox_pixel else None,
                    "bbox_normalized": list(r.bbox_normalized) if r.bbox_normalized else None,
                    "confidence": r.confidence,
                    "alternative_targets": r.alternative_targets,
                    "reasoning": r.reasoning,
                }
                for r in results
            ]
        }
    except Exception as e:
        raise _handle_vision_error(e)


@router.post("/compare-screenshots")
async def compare_screenshots(request: CompareScreenshotsRequest):
    _check_vision_enabled()
    service = MultimodalLLMService()
    try:
        result = await service.compare_screenshots(
            before_base64=request.before_image_base64,
            after_base64=request.after_image_base64,
        )
        return {
            "added_elements": [
                {
                    "element_type": el.element_type,
                    "label": el.label,
                    "description": el.description,
                    "bbox": list(el.bbox) if el.bbox else None,
                    "confidence": el.confidence,
                }
                for el in result.added_elements
            ],
            "removed_elements": [
                {
                    "element_type": el.element_type,
                    "label": el.label,
                    "description": el.description,
                    "bbox": list(el.bbox) if el.bbox else None,
                    "confidence": el.confidence,
                }
                for el in result.removed_elements
            ],
            "changed_elements": [
                {
                    "element_type": el.element_type,
                    "label": el.label,
                    "description": el.description,
                    "bbox": list(el.bbox) if el.bbox else None,
                    "confidence": el.confidence,
                }
                for el in result.changed_elements
            ],
            "state_transition": result.state_transition,
            "is_significant_change": result.is_significant_change,
        }
    except Exception as e:
        raise _handle_vision_error(e)


@router.post("/extract-ui-elements")
async def extract_ui_elements(request: ExtractUIElementsRequest):
    _check_vision_enabled()
    service = MultimodalLLMService()
    try:
        elements = await service.extract_ui_elements(
            screenshot_base64=request.image_base64,
        )
        return {
            "elements": [
                {
                    "element_type": el.element_type,
                    "label": el.label,
                    "description": el.description,
                    "bbox": list(el.bbox) if el.bbox else None,
                    "confidence": el.confidence,
                    "actionable": el.actionable,
                    "suggested_action": el.suggested_action,
                }
                for el in elements
            ],
            "total": len(elements),
        }
    except Exception as e:
        raise _handle_vision_error(e)


@router.post("/generate-workflow")
async def generate_workflow(request: GenerateWorkflowRequest):
    _check_vision_enabled()
    service = MultimodalLLMService()
    try:
        steps = await service.generate_workflow_from_trace(
            screenshots_base64=request.screenshots,
            operations=request.operations,
        )
        return {
            "steps": [
                {
                    "step_index": s.step_index,
                    "description": s.description,
                    "action_type": s.action_type,
                    "confidence": s.confidence,
                }
                for s in steps
            ]
        }
    except Exception as e:
        raise _handle_vision_error(e)


@router.post("/verify-action")
async def verify_action(request: VerifyActionRequest):
    _check_vision_enabled()
    engine = GUIGroundingEngine()
    try:
        success, description = await engine.verify_action(
            before_base64=request.before_image_base64,
            after_base64=request.after_image_base64,
            expected_action=request.expected_action,
        )
        return {"success": success, "description": description}
    except Exception as e:
        raise _handle_vision_error(e)
