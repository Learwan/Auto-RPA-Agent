from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

logger = logging.getLogger(__name__)


class ModelStatus(StrEnum):
    NOT_DOWNLOADED = "not_downloaded"
    DOWNLOADING = "downloading"
    READY = "ready"
    LOADING = "loading"
    LOADED = "loaded"
    ERROR = "error"


@dataclass
class ModelInfo:
    name: str
    model_type: str
    path: str
    size_mb: float = 0.0
    status: ModelStatus = ModelStatus.NOT_DOWNLOADED
    description: str = ""
    quantization: str = ""
    context_length: int = 4096


class ModelManager:
    def __init__(self):
        self._models: dict[str, ModelInfo] = {}
        self._active_model: str | None = None
        self._active_grounding_model: str | None = None
        self._init_default_models()

    def _init_default_models(self) -> None:
        default_models = [
            ModelInfo(
                name="qwen3.5-4b-4bit",
                model_type="grounding",
                path="models/qwen3.5-4b-4bit",
                size_mb=2400,
                status=ModelStatus.NOT_DOWNLOADED,
                description="Qwen3.5 4B 4-bit quantized (default grounding model)",
                quantization="4-bit",
                context_length=32768,
            ),
            ModelInfo(
                name="ui-tars-1.5-7b",
                model_type="grounding",
                path="models/ui-tars-1.5-7b",
                size_mb=4200,
                status=ModelStatus.NOT_DOWNLOADED,
                description="UI-TARS 1.5 7B (high-precision grounding)",
                quantization="4-bit",
                context_length=16384,
            ),
            ModelInfo(
                name="btgenbot-2-1b",
                model_type="bt_generation",
                path="models/btgenbot-2-1b",
                size_mb=600,
                status=ModelStatus.NOT_DOWNLOADED,
                description="BTGenBot-2 1B (behavior tree generation)",
                quantization="4-bit",
                context_length=8192,
            ),
        ]
        for model in default_models:
            self._models[model.name] = model

    def list_models(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get_model(self, name: str) -> ModelInfo | None:
        return self._models.get(name)

    def get_active_grounding_model(self) -> ModelInfo | None:
        if self._active_grounding_model:
            return self._models.get(self._active_grounding_model)
        return None

    async def switch_model(self, name: str, model_type: str = "grounding") -> ModelInfo:
        model = self._models.get(name)
        if model is None:
            raise ValueError(f"Model not found: {name}")

        if model.status == ModelStatus.NOT_DOWNLOADED:
            raise ValueError(f"Model not downloaded: {name}. Run 'auto-agent model download {name}' first.")

        if model_type == "grounding":
            self._active_grounding_model = name
        self._active_model = name

        logger.info("Switched active %s model to: %s", model_type, name)
        return model

    async def download_model(self, name: str) -> ModelInfo:
        model = self._models.get(name)
        if model is None:
            raise ValueError(f"Model not found: {name}")

        if model.status == ModelStatus.READY or model.status == ModelStatus.LOADED:
            return model

        model.status = ModelStatus.DOWNLOADING
        logger.info("Starting download for model: %s", name)

        model_path = Path(model.path)
        if model_path.exists():
            model.status = ModelStatus.READY
            logger.info("Model already exists at: %s", model.path)
            return model

        logger.warning(
            "Model download requires manual setup. Please download '%s' to '%s' and run 'auto-agent model scan'",
            name,
            model.path,
        )
        model.status = ModelStatus.NOT_DOWNLOADED
        return model

    def scan_models(self) -> list[ModelInfo]:
        for model in self._models.values():
            model_path = Path(model.path)
            if model_path.exists() and any(model_path.iterdir()):
                model.status = ModelStatus.READY
            else:
                model.status = ModelStatus.NOT_DOWNLOADED
        return self.list_models()

    def register_model(self, model_info: ModelInfo) -> None:
        self._models[model_info.name] = model_info

    def unregister_model(self, name: str) -> bool:
        if name in self._models:
            del self._models[name]
            if self._active_model == name:
                self._active_model = None
            if self._active_grounding_model == name:
                self._active_grounding_model = None
            return True
        return False
