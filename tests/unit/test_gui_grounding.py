from unittest.mock import patch

import pytest

from src.llm.gui_grounding import GUIGroundingEngine, _extract_json


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
