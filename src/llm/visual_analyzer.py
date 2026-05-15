from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.llm.multimodal_service import MultimodalLLMService, VisionError
from src.models.operation import OperationEvent
from src.models.vision import UIElement

logger = logging.getLogger(__name__)


@dataclass
class TaskSegment:
    segment_id: int
    start_op_index: int
    end_op_index: int
    task_type: str
    description: str
    confidence: float


@dataclass
class UIPattern:
    pattern_id: int
    pattern_type: str
    description: str
    element_types: list[str]
    frequency: int
    sample_screenshots: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class EnrichedOperation:
    operation: OperationEvent
    target_element: UIElement | None = None
    neighbor_elements: list[UIElement] = field(default_factory=list)
    ocr_text: str = ""
    business_label: str = ""
    is_critical: bool = False
    depends_on: list[int] = field(default_factory=list)


class VisualRecordingAnalyzer:
    def __init__(self) -> None:
        self._service: MultimodalLLMService | None = None

    async def _get_service(self) -> MultimodalLLMService:
        if self._service is None:
            self._service = MultimodalLLMService()
        return self._service

    async def segment_by_visual_boundaries(
        self,
        screenshots: list[tuple[float, str]],
        operations: list[OperationEvent],
    ) -> list[TaskSegment]:
        if len(screenshots) < 2:
            return []

        segments: list[TaskSegment] = []
        current_start = 0
        seg_id = 0

        service = await self._get_service()

        for i in range(1, len(screenshots)):
            _ts_before, before = screenshots[i - 1]
            _ts_after, after = screenshots[i]

            try:
                diff = await service.compare_screenshots(before, after)
            except VisionError:
                continue

            if diff.is_significant_change:
                op_end = min(i, len(operations) - 1) if operations else i
                if op_end > current_start:
                    segments.append(
                        TaskSegment(
                            segment_id=seg_id,
                            start_op_index=current_start,
                            end_op_index=op_end,
                            task_type="segment",
                            description=diff.state_transition[:200],
                            confidence=0.7 if diff.is_significant_change else 0.5,
                        )
                    )
                    seg_id += 1
                current_start = op_end + 1 if operations else i + 1

        if operations and current_start < len(operations):
            segments.append(
                TaskSegment(
                    segment_id=seg_id,
                    start_op_index=current_start,
                    end_op_index=len(operations) - 1,
                    task_type="segment",
                    description="Final task segment",
                    confidence=0.5,
                )
            )

        return segments

    async def recognize_repeated_patterns(
        self,
        screenshots: list[tuple[float, str]],
    ) -> list[UIPattern]:
        patterns: list[UIPattern] = []
        if len(screenshots) < 3:
            return patterns

        service = await self._get_service()
        elements_by_frame: list[set[str]] = []

        for _, screenshot in screenshots[:20]:
            try:
                elements = await service.extract_ui_elements(screenshot)
                types = {el.element_type for el in elements if el.confidence >= 0.5}
                elements_by_frame.append(types)
            except VisionError:
                elements_by_frame.append(set())

        pid = 0
        for i in range(len(elements_by_frame)):
            for j in range(i + 1, len(elements_by_frame)):
                if elements_by_frame[i] and elements_by_frame[i] == elements_by_frame[j]:
                    patterns.append(
                        UIPattern(
                            pattern_id=pid,
                            pattern_type="same_ui_layout",
                            description=f"Matching UI layout across frames {i} and {j}",
                            element_types=list(elements_by_frame[i]),
                            frequency=2,
                            confidence=0.7,
                        )
                    )
                    pid += 1

        return patterns

    async def enrich_operation_context(
        self,
        screenshot_base64: str,
        operation: OperationEvent,
    ) -> EnrichedOperation:
        service = await self._get_service()
        try:
            analysis = await service.analyze_screenshot(
                screenshot_base64,
                task_description=f"分析这个截图，重点关注与操作 '{operation.type}' 相关的UI元素。",
            )
        except VisionError:
            return EnrichedOperation(operation=operation)

        target = None
        for el in analysis.actionable_elements:
            if el.confidence >= 0.6 and el.actionable:
                target = el
                break

        return EnrichedOperation(
            operation=operation,
            target_element=target,
            neighbor_elements=analysis.actionable_elements,
            ocr_text=analysis.ui_state_description,
            business_label=analysis.user_intent,
            is_critical=analysis.confidence >= 0.8,
        )
