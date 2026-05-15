from src.llm.service import LLMService


class TestLLMServiceExtractJson:
    def test_extract_json_from_plain_object(self):
        service = LLMService()

        result = service._extract_json('{"found": true, "confidence": 0.9}')

        assert result == {"found": True, "confidence": 0.9}

    def test_extract_json_ignores_extra_braces_after_payload(self):
        service = LLMService()
        text = 'Result:\n{"summary": "ok", "confidence": 0.8}\nmetadata {ignored}'

        result = service._extract_json(text)

        assert result == {"summary": "ok", "confidence": 0.8}

    def test_extract_json_from_code_fence_with_python_style_dict(self):
        service = LLMService()
        text = "```json\n{'summary': 'ok', 'risks': ['a',], 'confidence': 0.6,}\n```"

        result = service._extract_json(text)

        assert result == {"summary": "ok", "risks": ["a"], "confidence": 0.6}

    def test_extract_json_from_nested_object_candidate(self):
        service = LLMService()
        text = 'prefix {"outer": {"inner": [1, 2, 3]}, "status": "ok"} suffix'

        result = service._extract_json(text)

        assert result == {"outer": {"inner": [1, 2, 3]}, "status": "ok"}
