from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import platform
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import resolve_local_vision_model, settings

logger = logging.getLogger(__name__)

_thread_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-engine")
_VLM_MAX_TOKENS = 512
_TRANSFORMERS_MINICPM_DOWNSAMPLE_MODE = "16x"
_TRANSFORMERS_MINICPM_MAX_SLICE_NUMS = 9
_MINICPM_TEXT_FALLBACK_SYSTEM_PROMPT = (
    "严格遵守用户要求。不要解释，不要复述，不要添加前后缀。"
    "若用户要求只回复某个词，就只输出那个词。"
)
_MINICPM_TEXT_ONLY_FILTER_PREFIXES = (
    "vision_tower.",
    "vit_merger.",
    "merger.",
    "resampler.",
)
_MINICPM_TEXT_ONLY_FILTER_FRAGMENTS = (
    ".vision_tower.",
    ".vit_merger.",
    ".merger.",
    ".resampler.",
)


def _ensure_mlx_vlm_minicpm_compat() -> None:
    try:
        import mlx_vlm.prompt_utils as prompt_utils
        from mlx_vlm.models.minicpmo import config as minicpmo_config
        from mlx_vlm.models.minicpmo.processing_minicpmo import (
            MiniCPMOProcessor,
            install_auto_processor_patch,
        )
        from mlx_vlm.utils import MODEL_REMAPPING
    except Exception as exc:
        logger.debug("Skipping MiniCPM compatibility patch: %s", exc)
        return

    MODEL_REMAPPING.setdefault("minicpmv", "minicpmo")
    MODEL_REMAPPING.setdefault("minicpmv4_6", "minicpmo")
    prompt_utils.MODEL_CONFIG.setdefault("minicpmv", prompt_utils.MessageFormat.IMAGE_TOKEN)
    prompt_utils.MODEL_CONFIG.setdefault("minicpmv4_6", prompt_utils.MessageFormat.IMAGE_TOKEN)
    install_auto_processor_patch("minicpmv", MiniCPMOProcessor)
    install_auto_processor_patch("minicpmv4_6", MiniCPMOProcessor)

    if not getattr(minicpmo_config.TextConfig, "_auto_agent_minicpm_text_patch", False):
        original_text_from_dict = minicpmo_config.TextConfig.from_dict.__func__

        @classmethod
        def _patched_text_from_dict(cls, params):
            if isinstance(params, dict):
                params = dict(params)
                rope_params = params.get("rope_parameters")
                if isinstance(rope_params, dict):
                    if "rope_theta" not in params and "rope_theta" in rope_params:
                        params["rope_theta"] = rope_params["rope_theta"]
                    if "rope_scaling" not in params:
                        rope_scaling = dict(rope_params)
                        if "type" not in rope_scaling and "rope_type" in rope_scaling:
                            rope_scaling["type"] = rope_scaling.pop("rope_type")
                        params["rope_scaling"] = rope_scaling
            return original_text_from_dict(cls, params)

        minicpmo_config.TextConfig.from_dict = _patched_text_from_dict
        minicpmo_config.TextConfig._auto_agent_minicpm_text_patch = True

    if not getattr(minicpmo_config.VisionConfig, "_auto_agent_minicpm_vision_patch", False):
        original_vision_from_dict = minicpmo_config.VisionConfig.from_dict.__func__

        @classmethod
        def _patched_vision_from_dict(cls, params):
            if isinstance(params, dict):
                params = dict(params)
                if params.get("model_type") in {"minicpmv_vision", "minicpmv4_6_vision"}:
                    params["model_type"] = "siglip_vision_model"
            return original_vision_from_dict(cls, params)

        minicpmo_config.VisionConfig.from_dict = _patched_vision_from_dict
        minicpmo_config.VisionConfig._auto_agent_minicpm_vision_patch = True


def _detect_platform_capabilities() -> dict:
    caps = {
        "platform": sys.platform,
        "arch": platform.machine(),
        "cuda_available": False,
        "mps_available": False,
        "vllm_supported": False,
        "mlx_supported": False,
    }

    try:
        import torch

        if torch.cuda.is_available():
            caps["cuda_available"] = True
            try:
                import vllm  # noqa: F401

                caps["vllm_supported"] = True
            except ImportError:
                pass

        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            caps["mps_available"] = True
    except ImportError:
        pass

    if sys.platform == "darwin" and caps["arch"] == "arm64":
        try:
            import mlx.core  # noqa: F401

            caps["mlx_supported"] = True
        except ImportError:
            pass

    return caps


@dataclass
class EngineStatus:
    engine: str
    running: bool
    model: str
    model_loaded: bool
    vram_used_mb: float = 0.0
    tokens_per_second: float = 0.0
    context_length: int = 0
    gpu_name: str = ""
    error: str = ""


@dataclass
class InferenceResult:
    text: str
    tokens_generated: int
    elapsed_ms: float
    tokens_per_second: float
    finish_reason: str = "stop"


class LocalVisionRuntimeUnavailableError(RuntimeError):
    pass


class VLLMDriver:
    def __init__(self, model_path: str, temperature: float, max_tokens: int, context_length: int, base_url: str):
        self._model_path = model_path
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._context_length = context_length
        self._base_url = base_url
        self._llm = None
        self._sampling_params = None
        self._loaded = False
        self._server_mode = False
        self._gpu_name = ""

    @property
    def engine_name(self) -> str:
        return "vllm"

    @property
    def gpu_name(self) -> str:
        return self._gpu_name

    def _init_programmatic(self) -> bool:
        try:
            import torch
            from vllm import LLM, SamplingParams

            self._gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"

            self._llm = LLM(
                model=self._model_path,
                max_model_len=self._context_length,
                gpu_memory_utilization=0.92,
                trust_remote_code=True,
            )
            self._sampling_params = SamplingParams(
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            self._loaded = True
            logger.info(f"vLLM programmatic driver loaded model '{self._model_path}' on {self._gpu_name}")
            return True
        except Exception as e:
            logger.warning(f"vLLM programmatic init failed: {e}, falling back to server mode")
            return False

    def _init_server_mode(self) -> bool:
        from openai import OpenAI

        try:
            client = OpenAI(base_url=self._base_url, api_key="not-needed")
            models = client.models.list()
            model_ids = [m.id for m in models.data]
            if self._model_path in model_ids or any(m for m in model_ids if self._model_path.split("/")[-1] in m):
                self._server_mode = True
                self._loaded = True
                logger.info(f"vLLM server mode connected at {self._base_url}")
                return True
        except Exception as e:
            logger.warning(f"vLLM server check failed: {e}")
        return False

    def load(self) -> bool:
        if not self._init_programmatic():
            return self._init_server_mode()
        return True

    def generate_sync(
        self, messages: list[dict[str, str]], temperature: float | None = None, max_tokens: int | None = None
    ) -> InferenceResult:
        temp = temperature if temperature is not None else self._temperature
        max_tok = max_tokens if max_tokens is not None else self._max_tokens

        if self._server_mode:
            return self._generate_server(messages, temp, max_tok)
        return self._generate_programmatic(messages, temp, max_tok)

    def stream_sync(
        self, messages: list[dict[str, str]], temperature: float | None = None, max_tokens: int | None = None
    ):
        temp = temperature if temperature is not None else self._temperature
        max_tok = max_tokens if max_tokens is not None else self._max_tokens

        if self._server_mode:
            return self._stream_server(messages, temp, max_tok)
        return self._stream_programmatic(messages, temp, max_tok)

    def _generate_programmatic(self, messages, temp, max_tok) -> InferenceResult:
        from vllm import SamplingParams

        prompt = self._messages_to_prompt(messages)
        sp = SamplingParams(temperature=temp, max_tokens=max_tok)

        start = time.perf_counter()
        outputs = self._llm.generate([prompt], sp)
        elapsed = (time.perf_counter() - start) * 1000

        text = outputs[0].outputs[0].text if outputs and outputs[0].outputs else ""
        tokens = len(outputs[0].outputs[0].token_ids) if outputs and outputs[0].outputs else 0

        return InferenceResult(
            text=text,
            tokens_generated=tokens,
            elapsed_ms=round(elapsed, 1),
            tokens_per_second=round(tokens / max(elapsed / 1000, 0.001), 1),
        )

    def _generate_server(self, messages, temp, max_tok) -> InferenceResult:
        from openai import OpenAI

        client = OpenAI(base_url=self._base_url, api_key="not-needed")

        start = time.perf_counter()
        resp = client.chat.completions.create(
            model=self._model_path,
            messages=messages,
            temperature=temp,
            max_tokens=max_tok,
            stream=False,
        )
        elapsed = (time.perf_counter() - start) * 1000

        text = resp.choices[0].message.content or ""
        tokens = resp.usage.completion_tokens if resp.usage else 0

        return InferenceResult(
            text=text,
            tokens_generated=tokens,
            elapsed_ms=round(elapsed, 1),
            tokens_per_second=round(tokens / max(elapsed / 1000, 0.001), 1),
        )

    def _stream_server(self, messages, temp, max_tok):
        from openai import OpenAI

        client = OpenAI(base_url=self._base_url, api_key="not-needed")

        stream = client.chat.completions.create(
            model=self._model_path,
            messages=messages,
            temperature=temp,
            max_tokens=max_tok,
            stream=True,
        )
        total_tokens = 0
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                total_tokens += 1
                yield chunk.choices[0].delta.content

    def _stream_programmatic(self, messages, temp, max_tok):
        from vllm import SamplingParams

        prompt = self._messages_to_prompt(messages)
        sp = SamplingParams(temperature=temp, max_tokens=max_tok)

        for output in self._llm.generate([prompt], sp):
            for step in output.outputs:
                yield step.text

    @staticmethod
    def _messages_to_prompt(messages: list[dict[str, str]]) -> str:
        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                parts.append(f"<|system|>\n{content}</s>")
            elif role == "user":
                parts.append(f"<|user|>\n{content}</s>")
            elif role == "assistant":
                parts.append(f"<|assistant|>\n{content}</s>")
        parts.append("<|assistant|>\n")
        return "\n".join(parts)

    def get_vram_mb(self) -> float:
        try:
            import torch

            if torch.cuda.is_available():
                return round(torch.cuda.memory_allocated(0) / (1024 * 1024), 1)
        except Exception:
            pass
        return 0.0

    def unload(self) -> None:
        if self._llm is not None:
            del self._llm
            self._llm = None
            self._loaded = False
            try:
                import torch

                torch.cuda.empty_cache()
            except Exception:
                pass


class MLXDriver:
    def __init__(self, model_path: str, temperature: float, max_tokens: int, context_length: int, base_url: str):
        self._model_path = model_path
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._context_length = context_length
        self._base_url = base_url
        self._model = None
        self._tokenizer = None
        self._loaded = False
        self._server_mode = False
        self._uses_minicpm_text_fallback = False
        self._gpu_name = "Apple Silicon (MLX)"

    @property
    def engine_name(self) -> str:
        return "mlx"

    @property
    def gpu_name(self) -> str:
        return self._gpu_name

    @staticmethod
    def _is_minicpm_text_only_filtered_weight(key: str) -> bool:
        if key.startswith(_MINICPM_TEXT_ONLY_FILTER_PREFIXES):
            return True
        return any(fragment in key for fragment in _MINICPM_TEXT_ONLY_FILTER_FRAGMENTS)

    def _is_minicpm_text_checkpoint(self) -> bool:
        config_path = Path(self._model_path) / "config.json"
        if not config_path.exists():
            return False

        try:
            config = json.loads(config_path.read_text())
        except Exception:
            return False

        text_config = config.get("text_config") or {}
        model_type = str(config.get("model_type") or "")
        text_model_type = str(text_config.get("model_type") or "")
        return model_type in {"minicpmv4_6", "minicpmv"} or text_model_type == "qwen3_5_text"

    def _load_minicpm_text_fallback(self) -> tuple[Any, Any]:
        from mlx_lm import utils as mlx_utils
        from mlx_lm.models import qwen3_5

        model_path = Path(self._model_path)
        config = mlx_utils.load_config(model_path)
        config["model_type"] = "qwen3_5"

        text_config = dict(config.get("text_config") or {})
        text_config["model_type"] = "qwen3_5"
        config["text_config"] = text_config

        class MiniCPMTextOnlyModel(qwen3_5.Model):
            def sanitize(self, weights):
                sanitized = super().sanitize(weights)
                return {
                    key: value
                    for key, value in sanitized.items()
                    if not MLXDriver._is_minicpm_text_only_filtered_weight(key)
                }

        model, resolved_config = mlx_utils.load_model(
            model_path,
            model_config=config,
            get_model_classes=lambda config: (MiniCPMTextOnlyModel, qwen3_5.ModelArgs),
        )
        tokenizer = mlx_utils.load_tokenizer(
            model_path,
            {"trust_remote_code": True},
            eos_token_ids=resolved_config.get("eos_token_id"),
        )
        return model, tokenizer

    @staticmethod
    def _message_content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
            return "\n".join(part for part in parts if part)
        if content is None:
            return ""
        return str(content)

    def _build_minicpm_text_prompt(self, messages: list[dict[str, str]]) -> str:
        prompt_parts: list[str] = []
        system_parts = [_MINICPM_TEXT_FALLBACK_SYSTEM_PROMPT]
        start_index = 0

        if messages and messages[0].get("role") == "system":
            system_text = self._message_content_to_text(messages[0].get("content", "")).strip()
            if system_text:
                system_parts.append(system_text)
            start_index = 1

        prompt_parts.append(f"<|im_start|>system\n{'\n\n'.join(system_parts)}<|im_end|>")

        for message in messages[start_index:]:
            role = message.get("role", "user")
            if role not in {"user", "assistant", "system"}:
                role = "user"
            content = self._message_content_to_text(message.get("content", ""))
            prompt_parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")

        prompt_parts.append("<|im_start|>assistant\n")
        return "\n".join(prompt_parts)

    @staticmethod
    def _strip_minicpm_think_output(text: str) -> tuple[str, bool, bool]:
        stripped = text.lstrip()
        if not stripped.startswith("<think>"):
            return text, False, False

        end_marker = "</think>"
        end_index = stripped.find(end_marker)
        if end_index == -1:
            return stripped, True, False

        answer = stripped[end_index + len(end_marker) :].lstrip()
        return (answer or stripped), True, True

    def _minicpm_retry_max_tokens(self, max_tok: int) -> int:
        configured_limit = max(self._max_tokens, 192)
        return max(max_tok, min(max(max_tok * 8, 192), configured_limit))

    def _generate_programmatic_once(self, prompt: str, max_tok: int) -> tuple[str, int, float]:
        from mlx_lm import stream_generate

        start = time.perf_counter()
        text_parts: list[str] = []
        token_count = 0
        for resp in stream_generate(self._model, self._tokenizer, prompt, max_tokens=max_tok):
            text_parts.append(resp.text)
            token_count += 1
            if token_count >= max_tok:
                break

        elapsed = (time.perf_counter() - start) * 1000
        return "".join(text_parts).lstrip(), token_count, elapsed

    def _init_programmatic(self) -> bool:
        try:
            from mlx_lm import load

            self._model, self._tokenizer = load(
                self._model_path,
                tokenizer_config={"trust_remote_code": True},
            )
            self._uses_minicpm_text_fallback = False
            self._loaded = True
            logger.info(f"MLX driver loaded model '{self._model_path}' on Apple Silicon")
            return True
        except Exception as e:
            if self._is_minicpm_text_checkpoint():
                try:
                    self._model, self._tokenizer = self._load_minicpm_text_fallback()
                    self._uses_minicpm_text_fallback = True
                    self._loaded = True
                    logger.info(
                        "MLX driver loaded MiniCPM text checkpoint '%s' via qwen3_5 fallback",
                        self._model_path,
                    )
                    return True
                except Exception as fallback_exc:
                    logger.warning(
                        "MLX MiniCPM text fallback failed for '%s': %s",
                        self._model_path,
                        fallback_exc,
                    )
            logger.warning(f"MLX programmatic init failed: {e}, falling back to server mode")
            return False

    def _init_server_mode(self) -> bool:
        try:
            import httpx

            resp = httpx.get(f"{self._base_url}/v1/models", timeout=5.0)
            if resp.status_code == 200:
                self._server_mode = True
                self._loaded = True
                logger.info(f"MLX server mode connected at {self._base_url}")
                return True
        except Exception:
            pass
        return False

    def load(self) -> bool:
        if not self._init_programmatic():
            return self._init_server_mode()
        return True

    def generate_sync(
        self, messages: list[dict[str, str]], temperature: float | None = None, max_tokens: int | None = None
    ) -> InferenceResult:
        max_tok = max_tokens if max_tokens is not None else self._max_tokens

        if self._server_mode:
            return self._generate_server(messages, None, max_tok)
        return self._generate_programmatic(messages, max_tok)

    def stream_sync(
        self, messages: list[dict[str, str]], temperature: float | None = None, max_tokens: int | None = None
    ):
        max_tok = max_tokens if max_tokens is not None else self._max_tokens

        if self._server_mode:
            return self._stream_server(messages, None, max_tok)
        return self._stream_programmatic(messages, max_tok)

    def _generate_programmatic(self, messages, max_tok) -> InferenceResult:
        prompt = self._build_chat_prompt(messages)
        full_text, token_count, elapsed = self._generate_programmatic_once(prompt, max_tok)

        if self._uses_minicpm_text_fallback:
            cleaned_text, had_think, think_closed = self._strip_minicpm_think_output(full_text)
            if had_think and not think_closed:
                retry_max_tok = self._minicpm_retry_max_tokens(max_tok)
                if retry_max_tok > max_tok:
                    retried_text, retried_tokens, retried_elapsed = self._generate_programmatic_once(prompt, retry_max_tok)
                    full_text = retried_text
                    token_count = retried_tokens
                    elapsed += retried_elapsed
                    cleaned_text, had_think, think_closed = self._strip_minicpm_think_output(full_text)
            if had_think and think_closed:
                full_text = cleaned_text

        return InferenceResult(
            text=full_text,
            tokens_generated=token_count,
            elapsed_ms=round(elapsed, 1),
            tokens_per_second=round(token_count / max(elapsed / 1000, 0.001), 1),
        )

    def _generate_server(self, messages, _temp, max_tok) -> InferenceResult:
        from openai import OpenAI

        client = OpenAI(base_url=self._base_url, api_key="not-needed")

        start = time.perf_counter()
        resp = client.chat.completions.create(
            model=self._model_path,
            messages=messages,
            temperature=0.7,
            max_tokens=max_tok,
            stream=False,
        )
        elapsed = (time.perf_counter() - start) * 1000

        text = resp.choices[0].message.content or ""
        tokens = resp.usage.completion_tokens if resp.usage else 0

        return InferenceResult(
            text=text,
            tokens_generated=tokens,
            elapsed_ms=round(elapsed, 1),
            tokens_per_second=round(tokens / max(elapsed / 1000, 0.001), 1),
        )

    def _stream_programmatic(self, messages, max_tok):
        from mlx_lm import stream_generate

        prompt = self._build_chat_prompt(messages)
        n = 0
        for resp in stream_generate(self._model, self._tokenizer, prompt, max_tokens=max_tok):
            n += 1
            if n > max_tok:
                break
            yield resp.text

    def _stream_server(self, messages, _temp, max_tok):
        from openai import OpenAI

        client = OpenAI(base_url=self._base_url, api_key="not-needed")

        stream = client.chat.completions.create(
            model=self._model_path,
            messages=messages,
            temperature=0.7,
            max_tokens=max_tok,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _build_chat_prompt(self, messages: list[dict[str, str]]) -> str:
        if self._uses_minicpm_text_fallback:
            return self._build_minicpm_text_prompt(messages)

        if hasattr(self._tokenizer, "apply_chat_template") and self._tokenizer is not None:
            try:
                return self._tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                )
            except Exception:
                pass

        parts: list[str] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>")
        parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)

    def get_vram_mb(self) -> float:
        try:
            import mlx.core as mx

            active = mx.metal.get_active_memory() / (1024 * 1024)
            return round(active, 1)
        except Exception:
            return 0.0

    def unload(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            self._tokenizer = None
            self._loaded = False


class LocalLLMEngine:
    _instance: LocalLLMEngine | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_instance(cls) -> LocalLLMEngine:
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    engine = cls()
                    await engine.initialize()
                    cls._instance = engine
        return cls._instance

    @classmethod
    def get_runtime_snapshot(cls) -> dict[str, Any]:
        instance = cls._instance
        if instance is None:
            return {
                "instance_initialized": False,
                "resolved_engine": None,
                "text_model_loaded": False,
                "vision_loaded": False,
                "vision_backend": None,
                "vision_device": None,
                "text_model_path": settings.LOCAL_LLM_MODEL,
                "vision_model_path": resolve_local_vision_model(settings),
                "model_path": settings.LOCAL_LLM_MODEL,
            }

        return {
            "instance_initialized": True,
            "resolved_engine": instance._engine_type,
            "text_model_loaded": instance._model_loaded,
            "vision_loaded": instance._vlm_loaded,
            "vision_backend": instance._vlm_backend or None,
            "vision_device": instance._vlm_device or None,
            "text_model_path": instance._model_path,
            "vision_model_path": instance._vision_model_path,
            "model_path": instance._model_path,
        }

    def __init__(self):
        self._engine_type = settings.LOCAL_LLM_ENGINE
        self._model_path = settings.LOCAL_LLM_MODEL
        self._vision_model_path = resolve_local_vision_model(settings)
        self._base_url = settings.LOCAL_LLM_BASE_URL
        self._temperature = settings.LOCAL_LLM_TEMPERATURE
        self._max_tokens = settings.LOCAL_LLM_MAX_TOKENS
        self._context_length = settings.LOCAL_LLM_CONTEXT_LENGTH
        self._driver: VLLMDriver | MLXDriver | None = None
        self._model_loaded = False
        self._caps: dict = {}
        self._last_inference_elapsed_ms = 0.0
        self._total_tokens = 0
        self._total_inferences = 0
        self._vlm_model = None
        self._vlm_processor = None
        self._vlm_loaded = False
        self._vlm_backend = ""
        self._vlm_device = ""
        self._vlm_load_error: LocalVisionRuntimeUnavailableError | None = None

    async def initialize(self) -> None:
        loop = asyncio.get_running_loop()
        self._caps = await loop.run_in_executor(_thread_pool, _detect_platform_capabilities)

        if self._engine_type == "auto":
            self._resolve_auto_engine()

        logger.info(
            "LocalLLMEngine initializing: engine=%s, text_model=%s, vision_model=%s, caps=%s",
            self._engine_type,
            self._model_path,
            self._vision_model_path,
            self._caps,
        )

        await self._try_load_model()

    def _resolve_auto_engine(self) -> None:
        if self._caps["mlx_supported"]:
            self._engine_type = "mlx"
        elif self._caps["vllm_supported"]:
            self._engine_type = "vllm"
        elif self._caps["mps_available"]:
            self._engine_type = "mlx"
        else:
            self._engine_type = "vllm"

    async def _try_load_model(self) -> None:
        loop = asyncio.get_running_loop()

        if self._engine_type == "mlx":
            self._driver = MLXDriver(
                model_path=self._model_path,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                context_length=self._context_length,
                base_url=self._base_url,
            )
            ok = await loop.run_in_executor(_thread_pool, self._driver.load)
            self._model_loaded = ok

        elif self._engine_type == "vllm":
            self._driver = VLLMDriver(
                model_path=self._model_path,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                context_length=self._context_length,
                base_url=self._base_url,
            )
            ok = await loop.run_in_executor(_thread_pool, self._driver.load)
            self._model_loaded = ok

    async def get_status(self) -> EngineStatus:
        loop = asyncio.get_running_loop()

        vram = 0.0
        if self._driver is not None:
            vram = await loop.run_in_executor(_thread_pool, self._driver.get_vram_mb)

        avg_tps = 0.0
        if self._last_inference_elapsed_ms > 0 and self._total_inferences > 0:
            avg_tps = round(self._total_tokens / max(self._last_inference_elapsed_ms / 1000, 0.001), 1)

        return EngineStatus(
            engine=self._engine_type,
            running=self._model_loaded,
            model=self._model_path,
            model_loaded=self._model_loaded,
            vram_used_mb=vram,
            tokens_per_second=avg_tps,
            context_length=self._context_length,
            gpu_name=self._driver.gpu_name if self._driver else "",
            error="" if self._model_loaded else ("请先安装依赖: pip install vllm  或  pip install mlx mlx-lm"),
        )

    async def generate(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> InferenceResult:
        if not self._model_loaded or self._driver is None:
            raise RuntimeError(
                f"Local model '{self._model_path}' is not loaded. "
                f"Ensure the engine ({self._engine_type}) is properly installed."
            )

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            _thread_pool,
            lambda: self._driver.generate_sync(messages, temperature, max_tokens),
        )

        self._last_inference_elapsed_ms += result.elapsed_ms
        self._total_tokens += result.tokens_generated
        self._total_inferences += 1

        return result

    async def generate_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        if not self._model_loaded or self._driver is None:
            raise RuntimeError(f"Local model '{self._model_path}' is not loaded.")

        loop = asyncio.get_running_loop()

        def _sync_stream():
            return list(self._driver.stream_sync(messages, temperature, max_tokens))

        chunks = await loop.run_in_executor(_thread_pool, _sync_stream)

        start = time.perf_counter()
        token_count = 0
        for chunk in chunks:
            token_count += 1
            yield chunk

        elapsed = (time.perf_counter() - start) * 1000
        self._last_inference_elapsed_ms += elapsed
        self._total_tokens += token_count
        self._total_inferences += 1

    async def analyze_with_image(
        self,
        prompt: str,
        image_path: str | None = None,
        image_base64: str | None = None,
        system_prompt: str | None = None,
    ) -> InferenceResult:
        if settings.VISION_ENABLED and (image_path or image_base64):
            return await self._analyze_with_vlm(
                prompt,
                image_path,
                image_base64,
                system_prompt=system_prompt,
            )

        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": system_prompt or (
                    "你是一个桌面自动化分析专家。分析屏幕截图中的UI元素和操作上下文，"
                    "提供准确、详尽的分析。使用中文回答。"
                ),
            },
        ]

        user_content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

        if image_path and Path(image_path).exists():
            with open(image_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode("utf-8")
            ext = Path(image_path).suffix.lower().lstrip(".")
            mime = f"image/{ext}" if ext in ("png", "jpeg", "jpg", "gif", "webp") else "image/png"
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{img_data}"},
                }
            )
        elif image_base64:
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_base64}"},
                }
            )

        messages.append({"role": "user", "content": user_content})
        return await self.generate(messages)

    async def reload_model(
        self,
        engine: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
    ) -> EngineStatus:
        old_model_path = self._model_path
        old_vision_model_path = self._vision_model_path
        if engine:
            self._engine_type = engine
        if model:
            self._model_path = model
        if vision_model is not None:
            self._vision_model_path = vision_model
        elif model and old_vision_model_path == old_model_path:
            self._vision_model_path = model

        if self._driver is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_thread_pool, self._driver.unload)
            self._driver = None
            self._model_loaded = False

        if self._vision_model_path != old_vision_model_path:
            self._reset_vlm_runtime()

        await self._try_load_model()
        return await self.get_status()

    async def close(self) -> None:
        if self._driver is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_thread_pool, self._driver.unload)
            self._driver = None
            self._model_loaded = False
        self._reset_vlm_runtime()

    def _reset_vlm_runtime(self) -> None:
        self._vlm_model = None
        self._vlm_processor = None
        self._vlm_loaded = False
        self._vlm_backend = ""
        self._vlm_load_error = None
        if self._vlm_device:
            with contextlib.suppress(Exception):
                import torch

                if self._vlm_device == "mps" and hasattr(torch, "mps"):
                    torch.mps.empty_cache()
        self._vlm_device = ""

    def _should_use_transformers_vlm_fallback(self, exc: Exception) -> bool:
        model_name = self._vision_model_path.lower()
        if "openbmb" not in model_name or "minicpm-v-4.6" not in model_name:
            return False

        return True

    def _load_transformers_vlm_sync(self):
        try:
            import packaging  # noqa: F401
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Transformers MiniCPM fallback requires the 'packaging' package. "
                "Please install the project dependencies again so the local vision runtime can start."
            ) from exc

        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        device = "mps" if torch.backends.mps.is_available() else "cpu"
        processor = AutoProcessor.from_pretrained(self._vision_model_path, trust_remote_code=True)
        model = AutoModelForImageTextToText.from_pretrained(
            self._vision_model_path,
            trust_remote_code=True,
            torch_dtype="auto",
        )
        model = model.to(device)
        model.eval()
        return model, processor, device

    def _summarize_vlm_load_error(self, exc: Exception, *, stage: str = "transformers") -> str:
        message = " ".join(str(exc).split())
        if "Missing " in message and "parameters:" in message:
            if stage == "mlx":
                return (
                    f"Local vision checkpoint '{self._vision_model_path}' could not be opened by mlx_vlm. "
                    "Trying the transformers fallback instead."
                )
            return (
                f"Local vision runtime could not load '{self._vision_model_path}'. "
                "The configured MiniCPM checkpoint appears incompatible or incomplete for the transformers fallback."
            )
        if "requires the 'packaging' package" in message:
            return message
        if len(message) > 240:
            message = f"{message[:237]}..."
        return f"Local vision runtime could not start for '{self._vision_model_path}': {message}"

    def _generate_with_transformers_vlm_sync(
        self,
        prompt_text: str,
        image_paths: list[str],
        system_prompt: str | None,
        max_tokens: int,
    ) -> tuple[str, int]:
        import torch

        user_content: list[dict[str, str]] = [
            {"type": "image", "url": image_path}
            for image_path in image_paths
        ]
        user_content.append({"type": "text", "text": prompt_text})

        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append(
                {
                    "role": "system",
                    "content": [{"type": "text", "text": system_prompt}],
                }
            )
        messages.append({"role": "user", "content": user_content})

        inputs = self._vlm_processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
            processor_kwargs={
                "images_kwargs": {
                    "downsample_mode": _TRANSFORMERS_MINICPM_DOWNSAMPLE_MODE,
                    "max_slice_nums": _TRANSFORMERS_MINICPM_MAX_SLICE_NUMS,
                }
            },
        ).to(self._vlm_device)

        with torch.inference_mode():
            generated_ids = self._vlm_model.generate(
                **inputs,
                downsample_mode=_TRANSFORMERS_MINICPM_DOWNSAMPLE_MODE,
                max_new_tokens=max_tokens,
            )

        trimmed = [out_ids[len(in_ids):] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)]
        output_text = self._vlm_processor.batch_decode(
            trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        generated_tokens = int(trimmed[0].shape[-1]) if trimmed else 0
        return (output_text[0] if output_text else ""), generated_tokens

    async def _generate_with_transformers_vlm(
        self,
        prompt_text: str,
        image_paths: list[str],
        system_prompt: str | None = None,
    ) -> InferenceResult:
        vlm_max_tokens = self._effective_vlm_max_tokens()
        loop = asyncio.get_running_loop()
        t0 = time.perf_counter()
        text, generated_tokens = await loop.run_in_executor(
            _thread_pool,
            lambda: self._generate_with_transformers_vlm_sync(
                prompt_text,
                image_paths,
                system_prompt,
                vlm_max_tokens,
            ),
        )
        elapsed = (time.perf_counter() - t0) * 1000

        return InferenceResult(
            text=text,
            tokens_generated=generated_tokens,
            elapsed_ms=round(elapsed, 1),
            tokens_per_second=round(generated_tokens / max(elapsed / 1000, 0.001), 1),
            finish_reason="stop",
        )

    async def _load_vlm(self) -> None:
        if self._vlm_loaded:
            return
        if self._vlm_load_error is not None:
            raise self._vlm_load_error
        loop = asyncio.get_running_loop()

        def _load():
            import mlx_vlm

            _ensure_mlx_vlm_minicpm_compat()
            return mlx_vlm.load(self._vision_model_path)

        try:
            self._vlm_model, self._vlm_processor = await loop.run_in_executor(_thread_pool, _load)
            self._vlm_backend = "mlx"
            self._vlm_device = ""
            self._vlm_loaded = True
            logger.info("VLM model loaded via mlx_vlm: '%s'", self._vision_model_path)
        except Exception as exc:
            if not self._should_use_transformers_vlm_fallback(exc):
                raise

            logger.warning(
                "mlx_vlm load failed for '%s', falling back to transformers MiniCPM runtime: %s",
                self._vision_model_path,
                self._summarize_vlm_load_error(exc, stage="mlx"),
            )
            try:
                self._vlm_model, self._vlm_processor, self._vlm_device = await loop.run_in_executor(
                    _thread_pool,
                    self._load_transformers_vlm_sync,
                )
            except Exception as fallback_exc:
                self._vlm_load_error = LocalVisionRuntimeUnavailableError(
                    self._summarize_vlm_load_error(fallback_exc)
                )
                raise self._vlm_load_error from fallback_exc
            self._vlm_backend = "transformers"
            self._vlm_loaded = True
            logger.info(
                "VLM model loaded via transformers: '%s' on %s",
                self._vision_model_path,
                self._vlm_device,
            )

    def _effective_vlm_max_tokens(self) -> int:
        # Vision generation becomes unstable on Apple GPU when it inherits the large
        # text-generation budget. Clamp it to a smaller ceiling for screenshot analysis.
        return max(64, min(self._max_tokens, _VLM_MAX_TOKENS))

    async def _analyze_with_vlm(
        self,
        prompt: str,
        image_path: str | None = None,
        image_base64: str | None = None,
        system_prompt: str | None = None,
    ) -> InferenceResult:
        await self._load_vlm()

        resolved_path: str | None = None
        tmp_file: str | None = None

        if image_path and Path(image_path).exists():
            resolved_path = image_path
        elif image_base64:
            try:
                raw = image_base64.split(",", 1)[-1] if "," in image_base64 else image_base64
                img_bytes = base64.b64decode(raw)
            except Exception:
                raise RuntimeError("Invalid base64 image data")
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(img_bytes)
                tmp_file = f.name
            resolved_path = tmp_file

        if not resolved_path:
            return await self.generate(
                [
                    {"role": "system", "content": system_prompt or "你是一个桌面自动化分析专家。使用中文回答。"},
                    {"role": "user", "content": prompt},
                ]
            )

        if self._vlm_backend == "transformers":
            try:
                return await self._generate_with_transformers_vlm(
                    prompt,
                    [resolved_path],
                    system_prompt=system_prompt,
                )
            finally:
                if tmp_file:
                    with contextlib.suppress(Exception):
                        Path(tmp_file).unlink()

        try:
            system_content = system_prompt or (
                "你是一个桌面自动化分析专家。分析屏幕截图中的UI元素和操作上下文，提供准确、详尽的分析。使用中文回答。"
            )
            vlm_max_tokens = self._effective_vlm_max_tokens()
            messages = [
                {"role": "system", "content": system_content},
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "image": resolved_path},
                        {"type": "text", "text": prompt},
                    ],
                },
            ]

            chat_prompt = self._vlm_processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

            def _generate():
                import mlx_vlm

                return mlx_vlm.generate(
                    self._vlm_model,
                    self._vlm_processor,
                    prompt=chat_prompt,
                    image=resolved_path,
                    max_tokens=vlm_max_tokens,
                    temp=self._temperature,
                )

            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            result = await loop.run_in_executor(_thread_pool, _generate)
            elapsed = (time.perf_counter() - t0) * 1000

            return InferenceResult(
                text=result.text,
                tokens_generated=result.generation_tokens,
                elapsed_ms=round(elapsed, 1),
                tokens_per_second=round(result.generation_tokens / max(elapsed / 1000, 0.001), 1),
                finish_reason="stop",
            )
        finally:
            if tmp_file:
                with contextlib.suppress(Exception):
                    Path(tmp_file).unlink()

    async def generate_with_images(
        self,
        prompt_text: str,
        image_base64s: list[str],
        system_prompt: str | None = None,
    ) -> InferenceResult:
        if not settings.VISION_ENABLED:
            return await self.generate(
                [
                    {"role": "system", "content": system_prompt or "你是一个桌面自动化分析专家。使用中文回答。"},
                    {"role": "user", "content": prompt_text},
                ]
            )

        await self._load_vlm()

        tmp_files: list[str] = []
        try:
            for b64_data in image_base64s:
                raw = b64_data.split(",", 1)[-1] if "," in b64_data else b64_data
                img_bytes = base64.b64decode(raw)
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                    f.write(img_bytes)
                    tmp_files.append(f.name)

            if self._vlm_backend == "transformers":
                return await self._generate_with_transformers_vlm(
                    prompt_text,
                    tmp_files,
                    system_prompt=system_prompt,
                )

            sys_content = system_prompt or (
                "你是一个桌面自动化分析专家。分析屏幕截图中的UI元素和操作上下文，提供准确、详尽的分析。使用中文回答。"
            )
            vlm_max_tokens = self._effective_vlm_max_tokens()
            content_parts: list[dict[str, Any]] = []
            for fp in tmp_files:
                content_parts.append({"type": "image", "image": fp})
            content_parts.append({"type": "text", "text": prompt_text})

            messages = [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": content_parts},
            ]

            chat_prompt = self._vlm_processor.apply_chat_template(
                messages,
                tokenize=False,
                add_generation_prompt=True,
            )

            def _generate():
                import mlx_vlm

                return mlx_vlm.generate(
                    self._vlm_model,
                    self._vlm_processor,
                    prompt=chat_prompt,
                    image=tmp_files,
                    max_tokens=vlm_max_tokens,
                    temp=self._temperature,
                )

            loop = asyncio.get_running_loop()
            t0 = time.perf_counter()
            result = await loop.run_in_executor(_thread_pool, _generate)
            elapsed = (time.perf_counter() - t0) * 1000

            return InferenceResult(
                text=result.text,
                tokens_generated=result.generation_tokens,
                elapsed_ms=round(elapsed, 1),
                tokens_per_second=round(result.generation_tokens / max(elapsed / 1000, 0.001), 1),
                finish_reason="stop",
            )
        finally:
            for fp in tmp_files:
                with contextlib.suppress(Exception):
                    Path(fp).unlink()
