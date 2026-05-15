from __future__ import annotations

import asyncio
import base64
import contextlib
import logging
import platform
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.config import settings

logger = logging.getLogger(__name__)

_thread_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="llm-engine")
_VLM_MAX_TOKENS = 512


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
        self._gpu_name = "Apple Silicon (MLX)"

    @property
    def engine_name(self) -> str:
        return "mlx"

    @property
    def gpu_name(self) -> str:
        return self._gpu_name

    def _init_programmatic(self) -> bool:
        try:
            from mlx_lm import load

            self._model, self._tokenizer = load(
                self._model_path,
                tokenizer_config={"trust_remote_code": True},
            )
            self._loaded = True
            logger.info(f"MLX driver loaded model '{self._model_path}' on Apple Silicon")
            return True
        except Exception as e:
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
        from mlx_lm import stream_generate

        prompt = self._build_chat_prompt(messages)

        start = time.perf_counter()
        text_parts: list[str] = []
        token_count = 0
        for resp in stream_generate(self._model, self._tokenizer, prompt, max_tokens=max_tok):
            text_parts.append(resp.text)
            token_count += 1
            if token_count >= max_tok:
                break

        elapsed = (time.perf_counter() - start) * 1000
        full_text = "".join(text_parts).lstrip()

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

    def __init__(self):
        self._engine_type = settings.LOCAL_LLM_ENGINE
        self._model_path = settings.LOCAL_LLM_MODEL
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

    async def initialize(self) -> None:
        loop = asyncio.get_running_loop()
        self._caps = await loop.run_in_executor(_thread_pool, _detect_platform_capabilities)

        if self._engine_type == "auto":
            self._resolve_auto_engine()

        logger.info(
            f"LocalLLMEngine initializing: engine={self._engine_type}, model={self._model_path}, caps={self._caps}"
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

    async def reload_model(self, engine: str | None = None, model: str | None = None) -> EngineStatus:
        if engine:
            self._engine_type = engine
        if model:
            self._model_path = model

        if self._driver is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_thread_pool, self._driver.unload)
            self._driver = None
            self._model_loaded = False

        await self._try_load_model()
        return await self.get_status()

    async def close(self) -> None:
        if self._driver is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(_thread_pool, self._driver.unload)
            self._driver = None
            self._model_loaded = False
        if self._vlm_model is not None:
            self._vlm_model = None
            self._vlm_processor = None
            self._vlm_loaded = False

    async def _load_vlm(self) -> None:
        if self._vlm_loaded:
            return
        loop = asyncio.get_running_loop()

        def _load():
            import mlx_vlm

            return mlx_vlm.load(self._model_path)

        self._vlm_model, self._vlm_processor = await loop.run_in_executor(_thread_pool, _load)
        self._vlm_loaded = True
        logger.info(f"VLM model loaded via mlx_vlm: '{self._model_path}'")

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
