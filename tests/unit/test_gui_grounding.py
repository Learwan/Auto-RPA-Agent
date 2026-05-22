from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.llm.gui_grounding import GUIGroundingEngine, _extract_json
from src.models.vision import GroundingResult


@pytest.fixture
def mock_settings():
    with patch("src.llm.gui_grounding.settings") as mock_s:
        mock_s.VISION_GROUNDING_CONFIDENCE_THRESHOLD = 0.5
        mock_s.VISION_ENABLED = False
        mock_s.GROUNDING_ENGINE = "qwen"
        yield mock_s


class TestExtractJson:
    def test_extract_from_code_block(self):
        text = '```json\n{"found": true, "confidence": 0.9}\n```'
        result = _extract_json(text)
        assert result == {"found": True, "confidence": 0.9}

    def test_extract_from_plain_json(self):
        text = '{"found": false, "bbox": null}'
        result = _extract_json(text)
        assert result == {"found": False, "bbox": None}

    def test_extract_from_mixed_text(self):
        text = 'Here is the result: {"found": true, "confidence": 0.8} end'
        result = _extract_json(text)
        assert result is not None
        assert result["found"] is True

    def test_extract_returns_none_for_invalid(self):
        text = "No JSON here at all"
        result = _extract_json(text)
        assert result is None

    def test_extract_array(self):
        text = "[1, 2, 3]"
        result = _extract_json(text)
        assert result == [1, 2, 3]

    def test_extract_nested_json(self):
        text = '{"outer": {"inner": "value"}, "count": 5}'
        result = _extract_json(text)
        assert result["outer"]["inner"] == "value"


class TestGUIGroundingEngineInit:
    def test_default_engine_is_qwen(self, mock_settings):
        engine = GUIGroundingEngine()
        assert engine._grounding_engine == "qwen"

    def test_threshold_from_settings(self, mock_settings):
        engine = GUIGroundingEngine()
        assert engine._threshold == 0.5


class TestGUIGroundingParseResult:
    def test_parse_found_result(self, mock_settings):
        engine = GUIGroundingEngine()
        data = {
            "found": True,
            "element_type": "button",
            "element_label": "Save",
            "bbox": [500, 300, 80, 30],
            "confidence": 0.92,
            "reasoning": "Located save button in toolbar",
        }
        result = engine._parse_grounding_result(data, "click", 1920, 1080)

        assert result.found is True
        assert result.element_type == "button"
        assert result.element_label == "Save"
        assert result.confidence == 0.92
        assert result.bbox_normalized == (500, 300, 80, 30)
        assert result.bbox_pixel is not None
        assert len(result.bbox_pixel) == 4

    def test_parse_not_found_result(self, mock_settings):
        engine = GUIGroundingEngine()
        data = {"found": False, "bbox": None, "confidence": 0.0}
        result = engine._parse_grounding_result(data, "click")

        assert result.found is False
        assert result.confidence == 0.0
        assert result.bbox_pixel is None
        assert result.bbox_normalized is None

    def test_parse_below_threshold(self, mock_settings):
        engine = GUIGroundingEngine()
        data = {
            "found": True,
            "bbox": [500, 300, 80, 30],
            "confidence": 0.3,
        }
        result = engine._parse_grounding_result(data, "click")

        assert result.found is False

    def test_parse_with_alternatives(self, mock_settings):
        engine = GUIGroundingEngine()
        data = {
            "found": True,
            "bbox": [500, 300, 80, 30],
            "confidence": 0.9,
            "alternative_targets": [
                {"label": "Cancel", "bbox": [600, 300, 80, 30], "confidence": 0.6},
            ],
        }
        result = engine._parse_grounding_result(data, "click")

        assert len(result.alternative_targets) == 1
        assert result.alternative_targets[0]["label"] == "Cancel"


class TestGUIGroundingNormalizedToPixel:
    def test_conversion(self, mock_settings):
        engine = GUIGroundingEngine()
        result = engine._normalized_to_pixel([500, 250, 100, 50], 1920, 1080)
        x, y, w, h = result
        assert x == int(500 * 1920 / 1000)
        assert y == int(250 * 1080 / 1000)
        assert w == int(100 * 1920 / 1000)
        assert h == int(50 * 1080 / 1000)

    def test_zero_coordinates(self, mock_settings):
        engine = GUIGroundingEngine()
        result = engine._normalized_to_pixel([0, 0, 0, 0], 1920, 1080)
        assert result == (0, 0, 0, 0)


class TestGUIGroundingShouldUseUITars:
    def test_qwen_engine_returns_false(self, mock_settings):
        mock_settings.GROUNDING_ENGINE = "qwen"
        engine = GUIGroundingEngine()
        assert engine._should_use_ui_tars() is False

    def test_ui_tars_engine_returns_true(self, mock_settings):
        mock_settings.GROUNDING_ENGINE = "ui_tars"
        engine = GUIGroundingEngine()
        assert engine._should_use_ui_tars() is True

    def test_auto_engine_with_model_path(self, mock_settings):
        mock_settings.GROUNDING_ENGINE = "auto"
        mock_settings.UI_TARS_MODEL_PATH = "/path/to/model"
        mock_settings.UI_TARS_SERVER_URL = ""
        engine = GUIGroundingEngine()
        assert engine._should_use_ui_tars() is True

    def test_auto_engine_without_model(self, mock_settings):
        mock_settings.GROUNDING_ENGINE = "auto"
        mock_settings.UI_TARS_MODEL_PATH = ""
        mock_settings.UI_TARS_SERVER_URL = ""
        engine = GUIGroundingEngine()
        assert engine._should_use_ui_tars() is False


class TestGUIGroundingRuntimeStatus:
    def test_disabled_vision_reports_disabled_status(self, mock_settings):
        with patch("src.llm.local_engine.LocalLLMEngine.get_runtime_snapshot", return_value={
            "vision_loaded": False,
            "vision_backend": None,
            "vision_device": None,
            "resolved_engine": None,
            "model_path": "models/qwen3.5-4b-4bit",
        }):
            engine = GUIGroundingEngine()
            status = engine.get_runtime_status()

        assert status["status"] == "disabled"
        assert status["preferred_backend"] == "qwen"
        assert status["can_attempt_grounding"] is False
        assert status["backends"]["qwen"]["selected"] is True

    def test_ui_tars_reports_deferred_with_qwen_fallback(self, mock_settings):
        mock_settings.VISION_ENABLED = True
        mock_settings.GROUNDING_ENGINE = "ui_tars"
        mock_settings.UI_TARS_MODEL_PATH = ""
        mock_settings.UI_TARS_SERVER_URL = ""
        with patch("src.llm.local_engine.LocalLLMEngine.get_runtime_snapshot", return_value={
            "vision_loaded": False,
            "vision_backend": None,
            "vision_device": None,
            "resolved_engine": "mlx",
            "model_path": "models/qwen3.5-4b-4bit",
        }):
            engine = GUIGroundingEngine()
            with patch.object(engine, "_remote_llm_available", return_value=False):
                status = engine.get_runtime_status()

        assert status["status"] == "deferred"
        assert status["preferred_backend"] == "ui_tars"
        assert status["fallback_chain"] == ["qwen_local"]
        assert status["backends"]["ui_tars"]["selected"] is True
        assert status["backends"]["ui_tars"]["config_source"] == "implicit_local_adapter"

    def test_ui_tars_server_config_reports_ready(self, mock_settings):
        mock_settings.VISION_ENABLED = True
        mock_settings.GROUNDING_ENGINE = "ui_tars"
        mock_settings.UI_TARS_MODEL_PATH = ""
        mock_settings.UI_TARS_SERVER_URL = "http://ui-tars.example/v1"
        with patch("src.llm.local_engine.LocalLLMEngine.get_runtime_snapshot", return_value={
            "vision_loaded": False,
            "vision_backend": None,
            "vision_device": None,
            "resolved_engine": "mlx",
            "model_path": "models/qwen3.5-4b-4bit",
        }):
            engine = GUIGroundingEngine()
            with patch.object(engine, "_remote_llm_available", return_value=False):
                status = engine.get_runtime_status()

        assert status["status"] == "ready"
        assert status["backends"]["ui_tars"]["server_enabled"] is True
        assert status["backends"]["ui_tars"]["config_source"] == "server_url"
        assert "远端 UI-TARS provider" in status["detail"]

    def test_qwen_reports_ready_once_runtime_loaded(self, mock_settings):
        mock_settings.VISION_ENABLED = True
        mock_settings.LOCAL_LLM_ENABLED = True
        with patch("src.llm.local_engine.LocalLLMEngine.get_runtime_snapshot", return_value={
            "vision_loaded": True,
            "vision_backend": "transformers",
            "vision_device": "mps",
            "resolved_engine": "mlx",
            "model_path": "models/openbmb_MiniCPM-V-4.6",
        }):
            engine = GUIGroundingEngine()
            status = engine.get_runtime_status()

        assert status["status"] == "ready"
        assert status["preferred_backend"] == "qwen"
        assert status["runtime_backend"] == "transformers"
        assert status["runtime_device"] == "mps"
        assert status["local_llm_enabled"] is True

    def test_runtime_status_includes_cloud_provider_when_remote_llm_configured(self, mock_settings):
        mock_settings.VISION_ENABLED = True
        with patch("src.llm.local_engine.LocalLLMEngine.get_runtime_snapshot", return_value={
            "vision_loaded": False,
            "vision_backend": None,
            "vision_device": None,
            "resolved_engine": "mlx",
            "model_path": "models/qwen3.5-4b-4bit",
        }):
            engine = GUIGroundingEngine()
            with patch.object(engine, "_remote_llm_available", return_value=True):
                status = engine.get_runtime_status()

        assert status["status"] == "degraded"
        assert status["provider_chain"] == ["qwen_local", "cloud_llm"]
        assert status["backends"]["cloud_llm"]["configured"] is True


class TestGUIGroundingFallbacks:
    @pytest.mark.asyncio
    async def test_ground_action_falls_back_to_cloud_llm_when_local_grounding_fails(self, mock_settings):
        mock_settings.VISION_ENABLED = True
        engine = GUIGroundingEngine()

        cloud_result = GroundingResult(
            found=True,
            element_type="button",
            element_label="保存",
            bbox_pixel=(100, 200, 80, 30),
            bbox_normalized=(52, 185, 41, 28),
            confidence=0.88,
            alternative_targets=[],
            reasoning="cloud fallback matched save button",
        )

        with patch.object(engine, "_should_use_ui_tars", return_value=False), \
             patch.object(engine, "_ground_action_qwen", new=AsyncMock(side_effect=RuntimeError("local vlm unavailable"))), \
             patch.object(engine, "_remote_llm_available", return_value=True), \
             patch.object(engine, "_ground_action_cloud_llm", new=AsyncMock(return_value=cloud_result)):
            result = await engine.ground_action("abc", "点击保存按钮", "click")

        assert result.found is True
        assert result.element_label == "保存"
        assert result.provider == "cloud_llm"
        assert result.attempted_providers == ["qwen_local", "cloud_llm"]
        assert result.provider_chain == ["qwen_local", "cloud_llm"]
        assert result.fallback_chain == ["cloud_llm"]

    @pytest.mark.asyncio
    async def test_ground_action_uses_cloud_llm_when_local_returns_not_found(self, mock_settings):
        mock_settings.VISION_ENABLED = True
        engine = GUIGroundingEngine()

        local_result = GroundingResult(
            found=False,
            element_type="click",
            element_label="点击保存按钮",
            bbox_pixel=None,
            bbox_normalized=None,
            confidence=0.0,
            alternative_targets=[],
            reasoning="local did not find target",
        )
        cloud_result = GroundingResult(
            found=True,
            element_type="button",
            element_label="保存",
            bbox_pixel=(100, 200, 80, 30),
            bbox_normalized=(52, 185, 41, 28),
            confidence=0.88,
            alternative_targets=[],
            reasoning="cloud fallback matched save button",
        )

        with patch.object(engine, "_should_use_ui_tars", return_value=False), \
             patch.object(engine, "_ground_action_qwen", new=AsyncMock(return_value=local_result)), \
             patch.object(engine, "_remote_llm_available", return_value=True), \
             patch.object(engine, "_ground_action_cloud_llm", new=AsyncMock(return_value=cloud_result)):
            result = await engine.ground_action("abc", "点击保存按钮", "click")

        assert result.found is True
        assert result.provider == "cloud_llm"
        assert result.attempted_providers == ["qwen_local", "cloud_llm"]

    @pytest.mark.asyncio
    async def test_verify_action_falls_back_to_cloud_llm(self, mock_settings):
        mock_settings.VISION_ENABLED = True
        engine = GUIGroundingEngine()
        fake_local_engine = SimpleNamespace(generate_with_images=AsyncMock(side_effect=RuntimeError("vlm crash")))

        with patch.object(engine, "_get_engine", new=AsyncMock(return_value=fake_local_engine)), \
             patch.object(engine, "_remote_llm_available", return_value=True), \
             patch.object(engine, "_verify_action_cloud_llm", new=AsyncMock(return_value=(True, "cloud verified"))):
            success, reasoning = await engine.verify_action("before", "after", {"description": "点击保存"})

        assert success is True
        assert reasoning == "cloud verified"
