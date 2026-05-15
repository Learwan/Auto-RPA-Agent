from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum

from src.analyzer.preprocessor import NormalizedOperation

logger = logging.getLogger(__name__)


class AnnotationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class StepAnnotation:
    annotation_id: str
    operation_index: int
    description: str = ""
    ui_elements: list[dict] = field(default_factory=list)
    intent: str = ""
    semantic_tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: AnnotationStatus = AnnotationStatus.PENDING
    error: str = ""


@dataclass
class AnnotationBatch:
    batch_id: str
    session_id: str
    annotations: list[StepAnnotation] = field(default_factory=list)
    total_operations: int = 0
    completed_count: int = 0
    failed_count: int = 0


class SemanticAnnotator:
    def __init__(self, llm_client=None, batch_size: int = 5, max_concurrent: int = 3):
        self._llm_client = llm_client
        self._batch_size = batch_size
        self._max_concurrent = max_concurrent
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def annotate_operation(
        self,
        operation: NormalizedOperation,
        index: int,
        screenshot_b64: str | None = None,
    ) -> StepAnnotation:
        annotation = StepAnnotation(
            annotation_id=str(uuid.uuid4()),
            operation_index=index,
            status=AnnotationStatus.PROCESSING,
        )

        try:
            if self._llm_client is None:
                annotation.description = self._rule_based_description(operation)
                annotation.intent = self._infer_intent(operation)
                annotation.semantic_tags = self._extract_tags(operation)
                annotation.confidence = 0.6
                annotation.status = AnnotationStatus.COMPLETED
                return annotation

            prompt = self._build_prompt(operation, screenshot_b64 is not None)
            messages = [{"role": "user", "content": prompt}]

            if screenshot_b64:
                messages[0]["content"] = [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}},
                ]

            result = await self._call_llm(messages)
            annotation.description = result.get("description", "")
            annotation.ui_elements = result.get("ui_elements", [])
            annotation.intent = result.get("intent", "")
            annotation.semantic_tags = result.get("tags", [])
            annotation.confidence = result.get("confidence", 0.5)
            annotation.status = AnnotationStatus.COMPLETED

        except Exception as e:
            logger.warning("Annotation failed for operation %d: %s", index, e)
            annotation.status = AnnotationStatus.FAILED
            annotation.error = str(e)
            annotation.description = self._rule_based_description(operation)
            annotation.confidence = 0.3

        return annotation

    async def annotate_batch(
        self,
        operations: list[NormalizedOperation],
        screenshots: dict[int, str] | None = None,
        on_progress: callable = None,
    ) -> AnnotationBatch:
        batch = AnnotationBatch(
            batch_id=str(uuid.uuid4()),
            session_id="",
            total_operations=len(operations),
        )
        screenshots = screenshots or {}

        tasks = []
        for i, op in enumerate(operations):
            screenshot = screenshots.get(i)
            tasks.append(self._annotate_with_semaphore(op, i, screenshot))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                annotation = StepAnnotation(
                    annotation_id=str(uuid.uuid4()),
                    operation_index=i,
                    status=AnnotationStatus.FAILED,
                    error=str(result),
                    description=self._rule_based_description(operations[i]),
                    confidence=0.3,
                )
            else:
                annotation = result

            batch.annotations.append(annotation)
            if annotation.status == AnnotationStatus.COMPLETED:
                batch.completed_count += 1
            elif annotation.status == AnnotationStatus.FAILED:
                batch.failed_count += 1

            if on_progress:
                on_progress(batch.completed_count + batch.failed_count, batch.total_operations)

        return batch

    async def annotate_stream(
        self,
        operations: list[NormalizedOperation],
        screenshots: dict[int, str] | None = None,
    ):
        screenshots = screenshots or {}
        for i, op in enumerate(operations):
            screenshot = screenshots.get(i)
            annotation = await self._annotate_with_semaphore(op, i, screenshot)
            yield annotation

    async def _annotate_with_semaphore(
        self,
        operation: NormalizedOperation,
        index: int,
        screenshot_b64: str | None,
    ) -> StepAnnotation:
        async with self._semaphore:
            return await self.annotate_operation(operation, index, screenshot_b64)

    async def _call_llm(self, messages: list[dict]) -> dict:
        if self._llm_client is None:
            return {}

        import json

        response = await self._llm_client.chat(messages)
        text = response if isinstance(response, str) else str(response)

        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass

        return {"description": text[:200], "confidence": 0.4}

    def _build_prompt(self, operation: NormalizedOperation, has_screenshot: bool) -> str:
        screenshot_note = "I've also attached a screenshot of the UI state." if has_screenshot else ""
        return f"""Analyze this desktop automation operation and provide a structured annotation.

Operation Details:
- Type: {operation.op_type}
- Title: {operation.data.get("title", "N/A")}
- Application: {operation.data.get("app_name", "N/A")}
- Position: x={operation.data.get("x", "N/A")}, y={operation.data.get("y", "N/A")}
- Timestamp: {operation.timestamp}
{screenshot_note}

Respond in JSON format:
{{
    "description": "A natural language description of what this step does",
    "ui_elements": [{{"type": "button/input/menu", "label": "element text", "action": "click/type/select"}}],
    "intent": "The user's intent for this step",
    "tags": ["relevant", "semantic", "tags"],
    "confidence": 0.9
}}"""

    def _rule_based_description(self, operation: NormalizedOperation) -> str:
        action_map = {
            "click": "Click on",
            "type": "Type text into",
            "scroll": "Scroll in",
            "hotkey": "Press hotkey in",
            "drag": "Drag in",
            "wait": "Wait in",
            "switch_window": "Switch to window",
            "navigate": "Navigate to",
        }
        action = action_map.get(operation.op_type, f"Perform {operation.op_type} in")
        title = operation.data.get("title", "element")
        app = operation.data.get("app_name", "application")
        return f"{action} '{title}' in {app}"

    def _infer_intent(self, operation: NormalizedOperation) -> str:
        op_type = operation.op_type
        title = str(operation.data.get("title", "")).lower()

        if op_type == "click":
            if any(kw in title for kw in ["save", "submit", "ok", "confirm", "apply"]):
                return "confirm_action"
            if any(kw in title for kw in ["cancel", "close", "no"]):
                return "cancel_action"
            if any(kw in title for kw in ["delete", "remove"]):
                return "delete_item"
            return "navigate_or_select"

        if op_type == "type":
            return "enter_data"

        if op_type == "scroll":
            return "browse_content"

        if op_type == "hotkey":
            return "shortcut_action"

        return "interact_with_ui"

    def _extract_tags(self, operation: NormalizedOperation) -> list[str]:
        tags = [operation.op_type]
        app_name = operation.data.get("app_name", "")
        if app_name:
            tags.append(str(app_name).lower().replace(" ", "_"))
        title = str(operation.data.get("title", "")).lower()
        if any(kw in title for kw in ["save", "submit"]):
            tags.append("form_submission")
        if any(kw in title for kw in ["search", "find"]):
            tags.append("search")
        if any(kw in title for kw in ["login", "sign"]):
            tags.append("authentication")
        if any(kw in title for kw in ["download", "export"]):
            tags.append("data_export")
        if any(kw in title for kw in ["upload", "import"]):
            tags.append("data_import")
        return tags
