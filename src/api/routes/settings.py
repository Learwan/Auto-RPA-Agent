import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.models.hotkey import HotkeyConfig, UserSettings
from src.settings_store import SettingsStore

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_store() -> SettingsStore:
    return SettingsStore.get_instance()


class UpdateSettingsRequest(BaseModel):
    updates: dict

    model_config = {"extra": "forbid"}


class UpdateHotkeyRequest(BaseModel):
    binding: dict

    model_config = {"extra": "forbid"}


@router.get("", response_model=UserSettings)
async def get_settings():
    return _get_store().get_settings()


@router.put("", response_model=UserSettings)
async def update_settings(req: UpdateSettingsRequest):
    try:
        return _get_store().update_settings(req.updates)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Update settings error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Settings update failed")


@router.get("/hotkeys", response_model=HotkeyConfig)
async def get_hotkey_config():
    return _get_store().get_settings().hotkeys


@router.put("/hotkeys/{action}", response_model=UserSettings)
async def update_hotkey(action: str, req: UpdateHotkeyRequest):
    store = _get_store()
    conflicts = store.get_settings().hotkeys.check_conflicts()
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={"message": "快捷键冲突", "conflicts": conflicts},
        )
    try:
        return store.update_hotkey(action, req.binding)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Update hotkey error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Hotkey update failed")


@router.get("/hotkeys/conflicts")
async def check_hotkey_conflicts():
    return {
        "has_conflicts": len(_get_store().get_settings().hotkeys.check_conflicts()) > 0,
        "conflicts": _get_store().get_settings().hotkeys.check_conflicts(),
    }


@router.post("/reset", response_model=UserSettings)
async def reset_settings():
    return _get_store().reset_to_defaults()


@router.get("/defaults", response_model=UserSettings)
async def get_default_settings():
    from src.models.hotkey import UserSettings

    return UserSettings()
