import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.config import settings
from src.llm.service import LLMService

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    messages: list[dict[str, str]]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    images: list[str] | None = None


class ChatVisionRequest(BaseModel):
    prompt: str
    images_base64: list[str]
    temperature: float | None = None
    max_tokens: int | None = None


class AnalyzeFlowRequest(BaseModel):
    flow_description: str
    operations_summary: str


class SuggestNameRequest(BaseModel):
    flow_description: str


class ExplainOperationsRequest(BaseModel):
    operations_text: str


class EnhanceScriptRequest(BaseModel):
    flow_json: str


def _get_service() -> LLMService:
    return LLMService()


@router.get("/status")
async def llm_status():
    return {
        "configured": settings.llm_configured,
        "model": settings.LLM_MODEL if settings.llm_configured else None,
        "base_url": settings.LLM_BASE_URL if settings.llm_configured else None,
        "vision_available": settings.VISION_ENABLED and settings.LOCAL_LLM_ENABLED,
        "vision_model": settings.LOCAL_LLM_MODEL if settings.LOCAL_LLM_ENABLED else None,
    }


@router.post("/chat")
async def chat(request: ChatRequest):
    svc = _get_service()
    if not svc.is_configured:
        raise HTTPException(status_code=400, detail="LLM is not configured. Set LLM_API_KEY in .env file.")
    try:
        result = await svc.chat(
            messages=request.messages,
            temperature=request.temperature,
            top_p=request.top_p,
            max_tokens=request.max_tokens,
            stream=request.stream,
        )
        return {"response": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LLM chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="LLM request failed")


@router.post("/analyze-flow")
async def analyze_flow(request: AnalyzeFlowRequest):
    svc = _get_service()
    if not svc.is_configured:
        raise HTTPException(status_code=400, detail="LLM is not configured.")
    try:
        result = await svc.analyze_flow(request.flow_description, request.operations_summary)
        return {"analysis": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LLM analyze flow error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Flow analysis failed")


@router.post("/suggest-name")
async def suggest_name(request: SuggestNameRequest):
    svc = _get_service()
    if not svc.is_configured:
        raise HTTPException(status_code=400, detail="LLM is not configured.")
    try:
        result = await svc.suggest_flow_name(request.flow_description)
        return {"name": result.strip()}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LLM suggest name error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Name suggestion failed")


@router.post("/explain-operations")
async def explain_operations(request: ExplainOperationsRequest):
    svc = _get_service()
    if not svc.is_configured:
        raise HTTPException(status_code=400, detail="LLM is not configured.")
    try:
        result = await svc.explain_operations(request.operations_text)
        return {"explanation": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LLM explain operations error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Operation explanation failed")


@router.post("/enhance-script")
async def enhance_script(request: EnhanceScriptRequest):
    svc = _get_service()
    if not svc.is_configured:
        raise HTTPException(status_code=400, detail="LLM is not configured.")
    try:
        result = await svc.generate_script_enhancement(request.flow_json)
        return {"script": result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"LLM enhance script error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Script enhancement failed")


@router.post("/chat-vision")
async def chat_vision(request: ChatVisionRequest):
    if not settings.VISION_ENABLED:
        raise HTTPException(status_code=503, detail="Vision model is disabled.")
    if not settings.LOCAL_LLM_ENABLED:
        raise HTTPException(status_code=400, detail="Local LLM is not enabled.")

    try:
        from src.llm.local_engine import LocalLLMEngine

        engine = await LocalLLMEngine.get_instance()

        results = []
        for img in request.images_base64:
            result = await engine.analyze_with_image(
                prompt=request.prompt,
                image_base64=img,
            )
            results.append({"text": result.text, "tokens": result.tokens_generated})

        return {"responses": results}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Vision chat error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Vision chat failed")
