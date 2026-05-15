from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.llm.gui_grounding import GUIGroundingEngine
from src.llm.multimodal_service import MultimodalLLMService, VisionError
from src.models.operation import OperationEvent
from src.models.vision import GroundingResult, ScreenshotAnalysis, UIElement

logger = logging.getLogger(__name__)


@dataclass
class ActionPlan:
    action_type: str
    description: str
    target_element: UIElement | None = None
    target_bbox: tuple[int, int, int, int] | None = None
    confidence: float = 0.0
    reasoning: str = ""


@dataclass
class ExecutionResult:
    success: bool
    description: str
    confidence: float
    before_analysis: ScreenshotAnalysis | None = None
    after_analysis: ScreenshotAnalysis | None = None
    grounding: GroundingResult | None = None


@dataclass
class AutomationFlow:
    flow_name: str
    description: str
    steps: list[ActionPlan] = field(default_factory=list)
    confidence: float = 0.0
    source_screenshots: list[str] = field(default_factory=list)


class VisionAgent:
    def __init__(self) -> None:
        self._service: MultimodalLLMService | None = None
        self._grounding: GUIGroundingEngine | None = None
        self._history: list[dict] = []

    async def _get_service(self) -> MultimodalLLMService:
        if self._service is None:
            self._service = MultimodalLLMService()
        return self._service

    async def _get_grounding(self) -> GUIGroundingEngine:
        if self._grounding is None:
            self._grounding = GUIGroundingEngine()
        return self._grounding

    async def plan_next_action(
        self,
        screenshot_base64: str,
        goal: str,
    ) -> ActionPlan:
        service = await self._get_service()
        grounding = await self._get_grounding()

        try:
            analysis = await service.analyze_screenshot(
                screenshot_base64,
                task_description=f"目标：{goal}。请分析当前截图，建议下一步操作。",
            )
        except VisionError:
            return ActionPlan(
                action_type="wait",
                description="Unable to analyze screenshot",
                confidence=0.0,
            )

        if not analysis.suggested_next_actions:
            return ActionPlan(
                action_type="done",
                description="No further actions required",
                confidence=0.5,
            )

        next_action = analysis.suggested_next_actions[0]
        target_element = None
        best_el = None

        for el in analysis.actionable_elements:
            if el.confidence >= 0.6 and el.actionable:
                best_el = el
                break

        try:
            grounding_result = await grounding.ground_action(
                screenshot_base64=screenshot_base64,
                action_description=next_action,
                action_type=best_el.suggested_action or "click" if best_el else "click",
            )
        except Exception:
            grounding_result = None

        if best_el:
            target_element = UIElement(
                element_type=best_el.element_type,
                label=best_el.label,
                description=best_el.description,
                bbox=grounding_result.bbox_pixel if grounding_result and grounding_result.found else best_el.bbox,
                confidence=best_el.confidence,
                actionable=best_el.actionable,
                suggested_action=best_el.suggested_action,
            )

        self._history.append(
            {
                "screenshot": screenshot_base64,
                "analysis": analysis,
                "action": next_action,
            }
        )

        return ActionPlan(
            action_type=best_el.suggested_action or "click" if best_el else "click",
            description=next_action,
            target_element=target_element,
            target_bbox=grounding_result.bbox_pixel if grounding_result and grounding_result.found else None,
            confidence=analysis.confidence,
            reasoning=grounding_result.reasoning if grounding_result else "",
        )

    async def execute_and_verify(
        self,
        before_base64: str,
        action: dict,
        after_base64: str,
    ) -> ExecutionResult:
        grounding = await self._get_grounding()

        success, description = await grounding.verify_action(
            before_base64=before_base64,
            after_base64=after_base64,
            expected_action=action,
        )

        return ExecutionResult(
            success=success,
            description=description,
            confidence=0.7 if success else 0.3,
        )

    async def learn_from_demonstration(
        self,
        screenshots: list[str],
        operations: list[OperationEvent],
    ) -> AutomationFlow:
        if not screenshots or not operations:
            return AutomationFlow(flow_name="Empty", description="No data", confidence=0.0)

        service = await self._get_service()
        actions: list[dict] = [{"type": str(op.type), "description": f"Operation: {op.type}"} for op in operations]

        try:
            steps = await service.generate_workflow_from_trace(screenshots, actions)
        except VisionError:
            steps = []

        action_plans = []
        for i, step in enumerate(steps):
            action_plans.append(
                ActionPlan(
                    action_type=step.action_type,
                    description=step.description,
                    confidence=step.confidence,
                )
            )

        if not action_plans and operations:
            for i, op in enumerate(operations):
                action_plans.append(
                    ActionPlan(
                        action_type=str(op.type),
                        description=f"Step {i + 1}: {op.type}",
                        confidence=0.5,
                    )
                )

        avg_conf = sum(a.confidence for a in action_plans) / len(action_plans) if action_plans else 0.0
        return AutomationFlow(
            flow_name="Learned Workflow",
            description=f"Automation flow learned from {len(operations)} operations and {len(screenshots)} screenshots",
            steps=action_plans,
            confidence=avg_conf,
            source_screenshots=screenshots,
        )
