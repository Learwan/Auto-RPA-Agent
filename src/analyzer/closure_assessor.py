from __future__ import annotations

from pydantic import BaseModel, Field

from src.models.automation import AutomationFlow, AutomationStep, LocateStrategy, StepType

_INTERACTIVE_STEP_TYPES = {
    StepType.CLICK,
    StepType.TYPE,
    StepType.SCROLL,
    StepType.DRAG,
    StepType.HOTKEY,
    StepType.UPLOAD_FILE,
    StepType.DOWNLOAD_FILE,
    StepType.SWITCH_WINDOW,
    StepType.NAVIGATE,
}

_POSTCHECK_STEP_TYPES = _INTERACTIVE_STEP_TYPES


class FlowClosureIssue(BaseModel):
    severity: str
    code: str
    message: str
    step_id: str | None = None
    step_index: int | None = None


class FlowClosureAssessment(BaseModel):
    ready: bool
    status: str
    score: float
    summary: str
    metrics: dict = Field(default_factory=dict)
    issues: list[FlowClosureIssue] = Field(default_factory=list)
    readiness_level: str = "unknown"
    remediation_hints: list[str] = Field(default_factory=list)


class FlowClosureError(RuntimeError):
    def __init__(self, assessment: FlowClosureAssessment):
        super().__init__(assessment.summary)
        self.assessment = assessment


class FlowClosureAssessor:
    def assess(self, flow: AutomationFlow) -> FlowClosureAssessment:
        issues: list[FlowClosureIssue] = []
        ordered_steps = flow.ordered_steps()
        interactive_steps = [step for step in ordered_steps if step.type in _INTERACTIVE_STEP_TYPES]

        certified_steps = 0
        steps_with_context = 0
        steps_with_checkpoints = 0
        confirmation_required_steps = 0
        position_steps = 0

        for index, step in enumerate(ordered_steps):
            if step.type not in _INTERACTIVE_STEP_TYPES:
                continue

            step_issues = self._assess_step(step, index)
            issues.extend(step_issues)

            if self._has_execution_context(step):
                steps_with_context += 1
            if self._has_step_checkpoints(step):
                steps_with_checkpoints += 1
            if step.locator_requires_confirmation():
                confirmation_required_steps += 1
            if step.target.strategy == LocateStrategy.POSITION:
                position_steps += 1
            if not any(item.severity in {"critical", "high"} for item in step_issues):
                certified_steps += 1

        interactive_count = len(interactive_steps)
        certified_ratio = certified_steps / interactive_count if interactive_count else 0.0
        context_ratio = steps_with_context / interactive_count if interactive_count else 0.0
        checkpoint_ratio = steps_with_checkpoints / interactive_count if interactive_count else 0.0
        confidence_component = min(max(float(flow.confidence or 0.0), 0.0), 1.0)

        penalty = min(
            0.6,
            sum(
                0.22 if item.severity == "critical" else 0.1 if item.severity == "high" else 0.03
                for item in issues
            ),
        )
        score = max(
            0.0,
            min(
                1.0,
                certified_ratio * 0.55
                + checkpoint_ratio * 0.20
                + context_ratio * 0.15
                + confidence_component * 0.10
                - penalty,
            ),
        )
        score = round(score, 3)

        blocking_issue_count = sum(1 for item in issues if item.severity in {"critical", "high"})
        ready = (
            interactive_count > 0
            and blocking_issue_count == 0
            and certified_ratio >= 0.999
            and checkpoint_ratio >= 0.999
            and context_ratio >= 0.999
            and score >= 0.9
        )

        if ready:
            summary = "流程已满足闭环执行条件，可正式执行。"
            status = "certified"
        elif interactive_count == 0:
            summary = "流程缺少可执行操作步骤，无法进行闭环认证。"
            status = "incomplete"
        else:
            summary = f"流程仍有 {blocking_issue_count} 个关键闭环风险，已禁止正式执行。"
            status = "needs_review"

        readiness_level = self._compute_readiness_level(
            ready, score, blocking_issue_count, certified_ratio, context_ratio, checkpoint_ratio
        )
        remediation_hints = self._generate_remediation_hints(issues, position_steps, interactive_count)

        if confidence_component < 0.55:
            issues.append(
                FlowClosureIssue(
                    severity="medium",
                    code="low_flow_confidence",
                    message="流程整体置信度偏低，建议重新录制一次以验证稳定性。",
                )
            )

        metrics = {
            "step_count": len(ordered_steps),
            "interactive_step_count": interactive_count,
            "certified_interactive_steps": certified_steps,
            "certified_ratio": round(certified_ratio, 3),
            "context_ratio": round(context_ratio, 3),
            "checkpoint_ratio": round(checkpoint_ratio, 3),
            "confirmation_required_steps": confirmation_required_steps,
            "position_steps": position_steps,
            "flow_confidence": round(confidence_component, 3),
            "blocking_issue_count": blocking_issue_count,
        }

        return FlowClosureAssessment(
            ready=ready,
            status=status,
            score=score,
            summary=summary,
            metrics=metrics,
            issues=issues,
            readiness_level=readiness_level,
            remediation_hints=remediation_hints,
        )

    def _assess_step(self, step: AutomationStep, index: int) -> list[FlowClosureIssue]:
        issues: list[FlowClosureIssue] = []
        target = step.target

        if step.locator_requires_confirmation():
            issues.append(
                self._issue(
                    "critical",
                    "locator_requires_confirmation",
                    "步骤定位仍需人工确认，不能作为闭环自动执行步骤。",
                    step,
                    index,
                )
            )

        if target.strategy == LocateStrategy.POSITION:
            issues.append(
                self._issue(
                    "critical",
                    "position_only_target",
                    "步骤仍主要依赖坐标定位，界面轻微变化就会失效。",
                    step,
                    index,
                )
            )

        if not self._has_execution_context(step):
            issues.append(
                self._issue(
                    "high",
                    "missing_window_context",
                    "步骤缺少窗口或页面上下文，无法稳定确认执行目标。",
                    step,
                    index,
                )
            )

        if not step.preconditions:
            issues.append(
                self._issue(
                    "high",
                    "missing_preconditions",
                    "步骤缺少执行前检查，无法保证进入正确状态后再执行。",
                    step,
                    index,
                )
            )

        if step.type in _POSTCHECK_STEP_TYPES and not self._has_postconditions(step):
            issues.append(
                self._issue(
                    "high",
                    "missing_postconditions",
                    "步骤缺少执行后校验，无法确认动作是否真正生效。",
                    step,
                    index,
                )
            )

        if target.strategy == LocateStrategy.TEXT_MATCH and not self._has_text_match_reinforcement(step):
            issues.append(
                self._issue(
                    "high",
                    "weak_text_target",
                    "文本定位缺少 role/class/属性等补充约束，仍然过于脆弱。",
                    step,
                    index,
                )
            )

        return issues

    @staticmethod
    def _issue(severity: str, code: str, message: str, step: AutomationStep, index: int) -> FlowClosureIssue:
        return FlowClosureIssue(
            severity=severity,
            code=code,
            message=message,
            step_id=step.id,
            step_index=index,
        )

    @staticmethod
    def _has_execution_context(step: AutomationStep) -> bool:
        target = step.target
        if target is None:
            return False
        if target.url:
            return True
        return bool(target.window_title)

    @staticmethod
    def _has_postconditions(step: AutomationStep) -> bool:
        metadata = step.metadata or {}
        postconditions = metadata.get("postconditions")
        if isinstance(postconditions, list) and postconditions:
            return True
        checkpoints = metadata.get("checkpoints") or {}
        post = checkpoints.get("post")
        return isinstance(post, list) and len(post) > 0

    def _has_step_checkpoints(self, step: AutomationStep) -> bool:
        return bool(step.preconditions) and self._has_postconditions(step)

    @staticmethod
    def _has_text_match_reinforcement(step: AutomationStep) -> bool:
        target = step.target
        if target is None:
            return False
        if target.strategy != LocateStrategy.TEXT_MATCH:
            return True
        return bool(
            target.role
            or target.class_name
            or target.expected_attributes
            or target.bounds
            or target.accessibility_id
            or target.selector
            or target.xpath
        )

    @staticmethod
    def _compute_readiness_level(
        ready: bool,
        score: float,
        blocking_issues: int,
        certified_ratio: float,
        context_ratio: float,
        checkpoint_ratio: float,
    ) -> str:
        if ready:
            return "production_ready"
        if blocking_issues == 0 and score >= 0.75:
            return "near_ready"
        if certified_ratio >= 0.7 and context_ratio >= 0.5:
            return "draft_viable"
        if certified_ratio >= 0.3:
            return "needs_work"
        return "not_viable"

    @staticmethod
    def _generate_remediation_hints(
        issues: list[FlowClosureIssue],
        position_steps: int,
        interactive_count: int,
    ) -> list[str]:
        hints: list[str] = []
        issue_codes = {item.code for item in issues}

        if "position_only_target" in issue_codes:
            hints.append(
                f"将 {position_steps} 个坐标定位步骤替换为 css_selector/xpath/accessibility_id 等稳定定位策略。"
            )
        if "missing_window_context" in issue_codes:
            hints.append("为步骤添加 window_title 或 url 上下文，帮助运行时验证执行环境。")
        if "missing_preconditions" in issue_codes:
            hints.append("添加 element_exists 或 window_exists 类型前置条件，确保步骤在正确状态下执行。")
        if "missing_postconditions" in issue_codes:
            hints.append("添加 page_stable 或自定义后置校验，确认动作生效。")
        if "locator_requires_confirmation" in issue_codes:
            hints.append("确认定位器策略或对步骤标记 requires_confirmation=false。")
        if "weak_text_target" in issue_codes:
            hints.append("为文本定位补充 role、class_name 或 xpath 以增强可靠性。")

        if not hints and interactive_count > 0:
            hints.append("流程已通过所有闭环检查。")

        return hints
