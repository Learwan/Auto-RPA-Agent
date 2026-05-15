from unittest.mock import patch
from unittest.mock import AsyncMock

import pytest

from src.llm.local_engine import (
    EngineStatus,
    InferenceResult,
    LocalLLMEngine,
    MLXDriver,
    VLLMDriver,
    _detect_platform_capabilities,
)


class TestDetectPlatformCapabilities:
    def test_returns_dict_with_required_keys(self):
        caps = _detect_platform_capabilities()
        assert "platform" in caps
        assert "arch" in caps
        assert "cuda_available" in caps
        assert "mps_available" in caps
        assert "vllm_supported" in caps
        assert "mlx_supported" in caps


class TestInferenceResult:
    def test_creation(self):
        result = InferenceResult(
            text="Hello",
            tokens_generated=5,
            elapsed_ms=100.0,
            tokens_per_second=50.0,
        )
        assert result.text == "Hello"
        assert result.tokens_generated == 5
        assert result.finish_reason == "stop"

    def test_custom_finish_reason(self):
        result = InferenceResult(
            text="",
            tokens_generated=0,
            elapsed_ms=10.0,
            tokens_per_second=0.0,
            finish_reason="length",
        )
        assert result.finish_reason == "length"


class TestEngineStatus:
    def test_creation(self):
        status = EngineStatus(
            engine="mlx",
            running=True,
            model="test-model",
            model_loaded=True,
        )
        assert status.engine == "mlx"
        assert status.running is True
        assert status.vram_used_mb == 0.0
        assert status.error == ""


class TestVLLMDriver:
    def test_engine_name(self):
        driver = VLLMDriver(
            model_path="test-model",
            temperature=0.7,
            max_tokens=512,
            context_length=4096,
            base_url="http://localhost:8000/v1",
        )
        assert driver.engine_name == "vllm"

    def test_messages_to_prompt(self):
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]
        prompt = VLLMDriver._messages_to_prompt(messages)
        assert "You are helpful." in prompt
        assert "Hello" in prompt
        assert "Hi there!" in prompt


class TestMLXDriver:
    def test_engine_name(self):
        driver = MLXDriver(
            model_path="test-model",
            temperature=0.7,
            max_tokens=512,
            context_length=4096,
            base_url="http://localhost:8000/v1",
        )
        assert driver.engine_name == "mlx"

    def test_gpu_name(self):
        driver = MLXDriver(
            model_path="test-model",
            temperature=0.7,
            max_tokens=512,
            context_length=4096,
            base_url="http://localhost:8000/v1",
        )
        assert "Apple Silicon" in driver.gpu_name

    def test_build_chat_prompt_fallback(self):
        driver = MLXDriver(
            model_path="test-model",
            temperature=0.7,
            max_tokens=512,
            context_length=4096,
            base_url="http://localhost:8000/v1",
        )
        driver._tokenizer = None
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        prompt = driver._build_chat_prompt(messages)
        assert "<|im_start|>system" in prompt
        assert "<|im_start|>user" in prompt
        assert "<|im_start|>assistant" in prompt


class TestLocalLLMEngine:
    def test_init_reads_settings(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "mlx"
            mock_s.LOCAL_LLM_MODEL = "test-model"
            mock_s.LOCAL_LLM_BASE_URL = "http://localhost:8000/v1"
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 512
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096

            engine = LocalLLMEngine()
            assert engine._engine_type == "mlx"
            assert engine._model_path == "test-model"

    def test_resolve_auto_engine_mlx(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "auto"
            mock_s.LOCAL_LLM_MODEL = "test"
            mock_s.LOCAL_LLM_BASE_URL = ""
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 512
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096

            engine = LocalLLMEngine()
            engine._caps = {"mlx_supported": True, "vllm_supported": False, "mps_available": True}
            engine._resolve_auto_engine()
            assert engine._engine_type == "mlx"

    def test_resolve_auto_engine_vllm(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "auto"
            mock_s.LOCAL_LLM_MODEL = "test"
            mock_s.LOCAL_LLM_BASE_URL = ""
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 512
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096

            engine = LocalLLMEngine()
            engine._caps = {"mlx_supported": False, "vllm_supported": True, "mps_available": False}
            engine._resolve_auto_engine()
            assert engine._engine_type == "vllm"

    def test_effective_vlm_max_tokens_clamps_large_budget(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "mlx"
            mock_s.LOCAL_LLM_MODEL = "test"
            mock_s.LOCAL_LLM_BASE_URL = ""
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 4096
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096

            engine = LocalLLMEngine()
            assert engine._effective_vlm_max_tokens() == 512

    def test_effective_vlm_max_tokens_keeps_small_budget(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "mlx"
            mock_s.LOCAL_LLM_MODEL = "test"
            mock_s.LOCAL_LLM_BASE_URL = ""
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 256
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096

            engine = LocalLLMEngine()
            assert engine._effective_vlm_max_tokens() == 256

    @pytest.mark.asyncio
    async def test_generate_raises_when_not_loaded(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "mlx"
            mock_s.LOCAL_LLM_MODEL = "test"
            mock_s.LOCAL_LLM_BASE_URL = ""
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 512
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096

            engine = LocalLLMEngine()
            engine._model_loaded = False
            with pytest.raises(RuntimeError, match="not loaded"):
                await engine.generate([{"role": "user", "content": "test"}])

    @pytest.mark.asyncio
    async def test_get_status_when_not_loaded(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "mlx"
            mock_s.LOCAL_LLM_MODEL = "test"
            mock_s.LOCAL_LLM_BASE_URL = ""
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 512
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096

            engine = LocalLLMEngine()
            engine._model_loaded = False
            status = await engine.get_status()
            assert status.running is False
            assert status.model_loaded is False
            assert "请先安装依赖" in status.error

    @pytest.mark.asyncio
    async def test_analyze_with_image_forwards_system_prompt_to_vlm(self):
        with patch("src.llm.local_engine.settings") as mock_s:
            mock_s.LOCAL_LLM_ENGINE = "mlx"
            mock_s.LOCAL_LLM_MODEL = "test"
            mock_s.LOCAL_LLM_BASE_URL = ""
            mock_s.LOCAL_LLM_TEMPERATURE = 0.7
            mock_s.LOCAL_LLM_MAX_TOKENS = 512
            mock_s.LOCAL_LLM_CONTEXT_LENGTH = 4096
            mock_s.VISION_ENABLED = True

            engine = LocalLLMEngine()
            engine._analyze_with_vlm = AsyncMock(
                return_value=InferenceResult(
                    text="{}",
                    tokens_generated=1,
                    elapsed_ms=1.0,
                    tokens_per_second=1.0,
                )
            )

            await engine.analyze_with_image(
                prompt="test prompt",
                image_base64="ZmFrZQ==",
                system_prompt="strict json prompt",
            )

            engine._analyze_with_vlm.assert_awaited_once_with(
                "test prompt",
                None,
                "ZmFrZQ==",
                system_prompt="strict json prompt",
            )
