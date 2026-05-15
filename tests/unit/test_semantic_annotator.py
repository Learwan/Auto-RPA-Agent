import pytest

from src.analyzer.preprocessor import NormalizedOperation
from src.analyzer.semantic_annotator import SemanticAnnotator, AnnotationStatus


def _make_op(op_type: str = "click", title: str = "Test", app_name: str = "TestApp") -> NormalizedOperation:
    return NormalizedOperation(
        op_type=op_type,
        data={"title": title, "app_name": app_name, "x": 100, "y": 200},
        timestamp=1000,
        seq_num=0,
    )


class TestSemanticAnnotatorBasic:
    @pytest.mark.asyncio
    async def test_single_operation_annotated(self):
        annotator = SemanticAnnotator()
        op = _make_op("click", "Save Button", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert result.status == AnnotationStatus.COMPLETED
        assert result.description != ""
        assert result.intent != ""

    @pytest.mark.asyncio
    async def test_batch_annotation(self):
        annotator = SemanticAnnotator()
        ops = [
            _make_op("click", "Button-1", "Chrome"),
            _make_op("type", "Search", "Chrome"),
            _make_op("click", "Submit", "Chrome"),
        ]
        batch = await annotator.annotate_batch(ops)
        assert batch.total_operations == 3
        assert batch.completed_count == 3
        assert batch.failed_count == 0

    @pytest.mark.asyncio
    async def test_stream_annotation(self):
        annotator = SemanticAnnotator()
        ops = [_make_op("click"), _make_op("type")]
        results = []
        async for annotation in annotator.annotate_stream(ops):
            results.append(annotation)
        assert len(results) == 2
        assert all(a.status == AnnotationStatus.COMPLETED for a in results)


class TestSemanticAnnotatorDescription:
    @pytest.mark.asyncio
    async def test_click_description(self):
        annotator = SemanticAnnotator()
        op = _make_op("click", "Save", "Word")
        result = await annotator.annotate_operation(op, 0)
        assert "Click" in result.description or "click" in result.description.lower()

    @pytest.mark.asyncio
    async def test_type_description(self):
        annotator = SemanticAnnotator()
        op = _make_op("type", "Search Box", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert "Type" in result.description or "type" in result.description.lower()

    @pytest.mark.asyncio
    async def test_scroll_description(self):
        annotator = SemanticAnnotator()
        op = _make_op("scroll", "Page", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert "Scroll" in result.description or "scroll" in result.description.lower()


class TestSemanticAnnotatorIntent:
    @pytest.mark.asyncio
    async def test_save_intent(self):
        annotator = SemanticAnnotator()
        op = _make_op("click", "Save", "Word")
        result = await annotator.annotate_operation(op, 0)
        assert result.intent == "confirm_action"

    @pytest.mark.asyncio
    async def test_cancel_intent(self):
        annotator = SemanticAnnotator()
        op = _make_op("click", "Cancel", "Word")
        result = await annotator.annotate_operation(op, 0)
        assert result.intent == "cancel_action"

    @pytest.mark.asyncio
    async def test_type_intent(self):
        annotator = SemanticAnnotator()
        op = _make_op("type", "Input", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert result.intent == "enter_data"

    @pytest.mark.asyncio
    async def test_scroll_intent(self):
        annotator = SemanticAnnotator()
        op = _make_op("scroll", "Page", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert result.intent == "browse_content"


class TestSemanticAnnotatorTags:
    @pytest.mark.asyncio
    async def test_save_tag(self):
        annotator = SemanticAnnotator()
        op = _make_op("click", "Save", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert "form_submission" in result.semantic_tags

    @pytest.mark.asyncio
    async def test_search_tag(self):
        annotator = SemanticAnnotator()
        op = _make_op("click", "Search", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert "search" in result.semantic_tags

    @pytest.mark.asyncio
    async def test_app_name_tag(self):
        annotator = SemanticAnnotator()
        op = _make_op("click", "Button", "Chrome")
        result = await annotator.annotate_operation(op, 0)
        assert "chrome" in result.semantic_tags
