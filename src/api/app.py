from contextlib import asynccontextmanager
from datetime import datetime, UTC
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from src.api import websocket
from src.api.routes import (
    agent,
    auth,
    automations,
    collab,
    desktop,
    executions,
    fusion,
    llm,
    memory,
    marketplace,
    notifications,
    scheduler,
    sessions,
    vault,
    vision,
)
from src.api.routes import settings as settings_routes
from src.config import resolve_local_vision_model, settings
from src.db.database import close_db, init_db
from src.llm.gui_grounding import GUIGroundingEngine
from src.llm.local_engine import LocalLLMEngine
from src.monitoring.middleware import MonitoringMiddleware
from src.security.rate_limiter import RateLimitMiddleware


def _mask_secret(secret: str) -> str:
    value = str(secret or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def _build_startup_check(
    check_id: str,
    label: str,
    *,
    enabled: bool,
    detail: str,
    value: str,
    action: str | None = None,
    status: str | None = None,
) -> dict:
    resolved_status = status
    if resolved_status is None:
        if enabled:
            resolved_status = "enabled"
        elif action:
            resolved_status = "action_required"
        else:
            resolved_status = "disabled"

    return {
        "id": check_id,
        "label": label,
        "enabled": enabled,
        "status": resolved_status,
        "detail": detail,
        "value": value,
        "action": action,
    }


def _summarize_startup_checks(sections: list[dict]) -> dict[str, int]:
    checks = [check for section in sections for check in section.get("checks", [])]
    status_counts = {
        "enabled": 0,
        "warning": 0,
        "action_required": 0,
        "optional_off": 0,
        "disabled": 0,
    }
    for check in checks:
        status = check.get("status") or "disabled"
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "total": len(checks),
        "enabled": status_counts.get("enabled", 0),
        "warning": status_counts.get("warning", 0),
        "action_required": status_counts.get("action_required", 0),
        "optional_off": status_counts.get("optional_off", 0),
        "disabled": status_counts.get("disabled", 0),
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_dirs()
    await init_db()
    yield
    await close_db()


def create_app() -> FastAPI:
    application = FastAPI(
        title="Auto Agent Workflow",
        description="Cross-platform automated agent workflow system API",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(RateLimitMiddleware, max_requests=100, window_seconds=60)
    application.add_middleware(MonitoringMiddleware)

    application.include_router(desktop.router, prefix="/api/desktop", tags=["Desktop"])
    application.include_router(sessions.router, prefix="/api/sessions", tags=["Sessions"])
    application.include_router(automations.router, prefix="/api/automations", tags=["Automations"])
    application.include_router(executions.router, prefix="/api/executions", tags=["Executions"])
    application.include_router(llm.router, prefix="/api/llm", tags=["LLM"])
    application.include_router(agent.router, prefix="/api/agent", tags=["Agent"])
    application.include_router(memory.router, prefix="/api/memory", tags=["Memory"])
    application.include_router(settings_routes.router, prefix="/api/settings", tags=["Settings"])
    application.include_router(collab.router, prefix="/api/collab", tags=["Collaboration"])
    application.include_router(vision.router, prefix="/api/vision", tags=["Vision"])
    application.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
    application.include_router(vault.router, prefix="/api/vault", tags=["Vault"])
    application.include_router(scheduler.router, prefix="/api/scheduler", tags=["Scheduler"])
    application.include_router(marketplace.router, prefix="/api/marketplace", tags=["Marketplace"])
    application.include_router(fusion.router, prefix="/api")
    application.include_router(notifications.router, prefix="/api")
    application.include_router(websocket.router)

    from src.settings_store import SettingsStore

    store = SettingsStore.get_instance()
    store.initialize(settings.DATA_DIR)

    @application.get("/api/health")
    async def health_check():
        return {"status": "ok", "version": "0.1.0"}

    @application.get("/api/health/details")
    async def health_details():
        from src.executor.execution_service import ExecutionService

        exec_svc = ExecutionService.get_instance()
        grounding_runtime = GUIGroundingEngine().get_runtime_status()
        ai_status = {}
        try:
            ai_status["intelligent_recovery"] = {
                "status": "active",
                "type": type(exec_svc.recovery).__name__,
            }
        except Exception:
            ai_status["intelligent_recovery"] = {"status": "unavailable"}
        try:
            ai_status["adaptive_workflow"] = {
                "status": "active",
                "type": type(exec_svc.adaptive).__name__,
            }
        except Exception:
            ai_status["adaptive_workflow"] = {"status": "unavailable"}
        try:
            ai_status["knowledge_graph"] = {
                "status": "active",
                "type": type(exec_svc.knowledge_graph).__name__,
            }
        except Exception:
            ai_status["knowledge_graph"] = {"status": "unavailable"}
        try:
            ai_status["self_evolution"] = {
                "status": "active",
                "type": type(exec_svc.evolution).__name__,
            }
        except Exception:
            ai_status["self_evolution"] = {"status": "unavailable"}

        return {
            "status": "ok",
            "version": "0.1.0",
            "data_dir": settings.DATA_DIR,
            "llm_configured": settings.llm_configured,
            "local_llm_enabled": settings.LOCAL_LLM_ENABLED,
            "local_llm_engine": settings.LOCAL_LLM_ENGINE if settings.LOCAL_LLM_ENABLED else None,
            "local_llm_model": settings.LOCAL_LLM_MODEL if settings.LOCAL_LLM_ENABLED else None,
            "local_vision_model": resolve_local_vision_model(settings)
            if settings.LOCAL_LLM_ENABLED and settings.VISION_ENABLED
            else None,
            "vision_enabled": settings.VISION_ENABLED,
            "vision_model": resolve_local_vision_model(settings)
            if settings.VISION_ENABLED and settings.LOCAL_LLM_ENABLED
            else None,
            "grounding_engine": getattr(settings, "GROUNDING_ENGINE", "qwen"),
            "grounding_runtime": grounding_runtime,
            "collab_enabled": settings.COLLAB_ENABLED,
            "recorders": {
                "mouse": settings.RECORD_ENABLE_MOUSE,
                "keyboard": settings.RECORD_ENABLE_KEYBOARD,
                "window": settings.RECORD_ENABLE_WINDOW,
                "clipboard": settings.RECORD_ENABLE_CLIPBOARD,
                "filesystem": settings.RECORD_ENABLE_FILESYSTEM,
            },
            "ai_modules": ai_status,
        }

    @application.get("/api/health/startup-check")
    async def startup_check():
        user_settings = store.get_settings()
        remote_llm = user_settings.remote_llm
        runtime = LocalLLMEngine.get_runtime_snapshot()
        grounding_runtime = GUIGroundingEngine().get_runtime_status()

        try:
            import mlx_vlm  # noqa: F401

            local_vlm_dependency_ok = True
            local_vlm_detail = "mlx_vlm 依赖可用，本地多模态模型可尝试加载。"
        except Exception as exc:
            local_vlm_dependency_ok = False
            local_vlm_detail = f"mlx_vlm 不可用：{exc}"

        effective_llm_source = "settings" if remote_llm.enabled else "env"
        effective_llm_label = "用户配置" if remote_llm.enabled else "环境变量"
        runtime_backend = runtime["vision_backend"]
        runtime_device = runtime["vision_device"]
        if runtime_backend:
            runtime_suffix = f" 当前运行后端：{runtime_backend}（{runtime_device or 'unknown'}）。"
        elif runtime["instance_initialized"]:
            runtime_suffix = " 视觉模型尚未加载到运行时。"
        else:
            runtime_suffix = " 视觉模型尚未参与推理。"

        sections = [
            {
                "id": "ai",
                "title": "AI 与视觉",
                "description": "远端 LLM、本地模型与视觉分析能力。",
                "checks": [
                    _build_startup_check(
                        "remote_llm",
                        "兼容 OpenAI 的远端 LLM",
                        enabled=settings.llm_configured,
                        detail=(
                            f"当前来源：{effective_llm_label}。"
                            f" 模型：{settings.LLM_MODEL or '未配置'}。"
                            f" Base URL：{settings.LLM_BASE_URL or '未配置'}。"
                        ),
                        value="已配置" if settings.llm_configured else "未配置",
                        action="在本页填写 Base URL、API Key、模型名称并保存。" if not settings.llm_configured else None,
                    ),
                    _build_startup_check(
                        "local_llm",
                        "本地 LLM",
                        enabled=settings.LOCAL_LLM_ENABLED,
                        detail=(
                            f"引擎：{settings.LOCAL_LLM_ENGINE}；文本模型：{settings.LOCAL_LLM_MODEL}。"
                            if settings.LOCAL_LLM_ENABLED
                            else "当前服务未启用本地 LLM。"
                        ),
                        value="开启" if settings.LOCAL_LLM_ENABLED else "关闭",
                        action=(
                            "设置 AUTO_AGENT_LOCAL_LLM_ENABLED=true 并重启服务。"
                            if not settings.LOCAL_LLM_ENABLED
                            else None
                        ),
                    ),
                    _build_startup_check(
                        "vision",
                        "本地视觉分析",
                        enabled=settings.VISION_ENABLED,
                        detail=(
                            f"视觉已启用；当前视觉模型路径：{resolve_local_vision_model(settings)}；请求引擎：{settings.LOCAL_LLM_ENGINE}。{runtime_suffix}"
                            if settings.VISION_ENABLED
                            else "当前服务已将视觉分析关闭。"
                        ),
                        value=(
                            f"开启 · {runtime_backend}"
                            if settings.VISION_ENABLED and runtime_backend
                            else ("开启" if settings.VISION_ENABLED else "关闭")
                        ),
                        action=(
                            "设置 AUTO_AGENT_VISION_ENABLED=true 并重启服务。"
                            if not settings.VISION_ENABLED
                            else None
                        ),
                    ),
                    _build_startup_check(
                        "local_vlm_dependency",
                        "本地视觉依赖 mlx_vlm",
                        enabled=local_vlm_dependency_ok,
                        detail=local_vlm_detail,
                        value="可用" if local_vlm_dependency_ok else "不可用",
                        action=(
                            "在当前 .venv 中安装 mlx-vlm，并重新验证视觉能力。"
                            if not local_vlm_dependency_ok
                            else None
                        ),
                        status="enabled" if local_vlm_dependency_ok else "warning",
                    ),
                ],
            },
            {
                "id": "recording",
                "title": "录制与交互",
                "description": "录制器、热键、通知与交互辅助能力。",
                "checks": [
                    _build_startup_check(
                        "window_recorder",
                        "窗口录制",
                        enabled=settings.RECORD_ENABLE_WINDOW,
                        detail="负责记录活动窗口切换与焦点变化。",
                        value="开启" if settings.RECORD_ENABLE_WINDOW else "关闭",
                        action="设置 AUTO_AGENT_RECORD_ENABLE_WINDOW=true 并重启服务。" if not settings.RECORD_ENABLE_WINDOW else None,
                    ),
                    _build_startup_check(
                        "mouse_recorder",
                        "鼠标录制",
                        enabled=settings.RECORD_ENABLE_MOUSE,
                        detail="记录点击、滚动、拖拽等鼠标操作。",
                        value="开启" if settings.RECORD_ENABLE_MOUSE else "关闭",
                        action="设置 AUTO_AGENT_RECORD_ENABLE_MOUSE=true 并重启服务。" if not settings.RECORD_ENABLE_MOUSE else None,
                        status="enabled" if settings.RECORD_ENABLE_MOUSE else "optional_off",
                    ),
                    _build_startup_check(
                        "keyboard_recorder",
                        "键盘录制",
                        enabled=settings.RECORD_ENABLE_KEYBOARD,
                        detail="记录键盘输入和热键操作。",
                        value="开启" if settings.RECORD_ENABLE_KEYBOARD else "关闭",
                        action="设置 AUTO_AGENT_RECORD_ENABLE_KEYBOARD=true 并重启服务。" if not settings.RECORD_ENABLE_KEYBOARD else None,
                        status="enabled" if settings.RECORD_ENABLE_KEYBOARD else "optional_off",
                    ),
                    _build_startup_check(
                        "hotkeys",
                        "全局热键",
                        enabled=settings.RECORD_HOTKEY_ENABLED,
                        detail="支持开始/暂停/停止录制、HUD 切换和抓焦点快捷键。",
                        value="开启" if settings.RECORD_HOTKEY_ENABLED else "关闭",
                        action="设置 AUTO_AGENT_RECORD_HOTKEY_ENABLED=true 并重启服务。" if not settings.RECORD_HOTKEY_ENABLED else None,
                    ),
                    _build_startup_check(
                        "audio_feedback",
                        "音效反馈",
                        enabled=settings.RECORD_AUDIO_FEEDBACK_ENABLED,
                        detail="开始/暂停/停止录制时播放提示音。",
                        value="开启" if settings.RECORD_AUDIO_FEEDBACK_ENABLED else "关闭",
                        action="设置 AUTO_AGENT_RECORD_AUDIO_FEEDBACK_ENABLED=true 并重启服务。" if not settings.RECORD_AUDIO_FEEDBACK_ENABLED else None,
                        status="enabled" if settings.RECORD_AUDIO_FEEDBACK_ENABLED else "optional_off",
                    ),
                    _build_startup_check(
                        "system_notifications",
                        "系统通知",
                        enabled=settings.RECORD_SYSTEM_NOTIFICATIONS_ENABLED,
                        detail="录制状态变化时发送系统通知。",
                        value="开启" if settings.RECORD_SYSTEM_NOTIFICATIONS_ENABLED else "关闭",
                        action="设置 AUTO_AGENT_RECORD_SYSTEM_NOTIFICATIONS_ENABLED=true 并重启服务。" if not settings.RECORD_SYSTEM_NOTIFICATIONS_ENABLED else None,
                        status="enabled" if settings.RECORD_SYSTEM_NOTIFICATIONS_ENABLED else "optional_off",
                    ),
                ],
            },
            {
                "id": "platform",
                "title": "协作与平台能力",
                "description": "协作、grounding 与项目运行环境。",
                "checks": [
                    _build_startup_check(
                        "collab",
                        "协作工作流",
                        enabled=settings.COLLAB_ENABLED,
                        detail="支持协作房间、实时建议与共享会话。",
                        value="开启" if settings.COLLAB_ENABLED else "关闭",
                        action="设置 AUTO_AGENT_COLLAB_ENABLED=true 并重启服务。" if not settings.COLLAB_ENABLED else None,
                        status="enabled" if settings.COLLAB_ENABLED else "optional_off",
                    ),
                    _build_startup_check(
                        "grounding_engine",
                        "Grounding 引擎",
                        enabled=grounding_runtime["can_attempt_grounding"],
                        detail=grounding_runtime["detail"],
                        value=(
                            f"{grounding_runtime['requested_engine']} -> {grounding_runtime['preferred_backend']}"
                        ),
                        action=(
                            "设置 AUTO_AGENT_VISION_ENABLED=true 并重启服务。"
                            if grounding_runtime["status"] == "disabled"
                            else None
                        ),
                        status=(
                            "enabled"
                            if grounding_runtime["status"] == "ready"
                            else "warning"
                        ),
                    ),
                    _build_startup_check(
                        "data_dir",
                        "数据目录",
                        enabled=True,
                        detail=f"当前数据目录：{settings.DATA_DIR}。",
                        value=settings.DATA_DIR,
                    ),
                ],
            },
        ]

        return {
            "status": "ok",
            "checked_at": datetime.now(UTC).isoformat(),
            "summary": _summarize_startup_checks(sections),
            "effective_llm": {
                "source": effective_llm_source,
                "configured": settings.llm_configured,
                "remote": {
                    "enabled": remote_llm.enabled,
                    "configured": remote_llm.configured,
                    "base_url": remote_llm.base_url,
                    "api_key_masked": _mask_secret(remote_llm.api_key),
                    "model": remote_llm.model,
                },
                "local": {
                    "enabled": settings.LOCAL_LLM_ENABLED,
                    "engine": settings.LOCAL_LLM_ENGINE,
                    "resolved_engine": runtime["resolved_engine"],
                    "model": settings.LOCAL_LLM_MODEL,
                },
                "vision": {
                    "enabled": settings.VISION_ENABLED,
                    "model": resolve_local_vision_model(settings),
                    "dependency_ok": local_vlm_dependency_ok,
                    "runtime_loaded": runtime["vision_loaded"],
                    "backend": runtime_backend,
                    "device": runtime_device,
                    "grounding": grounding_runtime,
                },
            },
            "sections": sections,
        }

    static_dir = Path(__file__).parent.parent.parent / "static"
    if static_dir.exists():
        application.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return application


app = create_app()
