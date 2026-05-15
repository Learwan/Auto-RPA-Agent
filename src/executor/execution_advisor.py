from __future__ import annotations

import asyncio
import json
import logging
import re

from src.config import settings
from src.llm.local_engine import LocalLLMEngine
from src.models.automation import AutomationFlow, AutomationStep, LocateStrategy, StepType
from src.models.execution import (
    ExecutionAdvice,
    ExecutionAIOptions,
    ExecutionFragileStep,
    ExecutionRecord,
    ExecutionSummary,
    StepStatus,
)

logger = logging.getLogger(__name__)
LOCAL_AI_TIMEOUT_S = {
    "pre": 1.5,
    "post": 4.0,
    "assist": 4.0,
    "summary": 8.0,
}


def _extract_json(text: str) -> dict | None:
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if fenced:
        text = fenced.group(1)

    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


class ExecutionAdvisor:
    def __init__(self, options: ExecutionAIOptions | None = None):
        self._options = options or ExecutionAIOptions()
        self._step_insights: dict[str, dict] = {}
        self._used_local_ai = False

    @property
    def enabled(self) -> bool:
        return bool(self._options.enabled)

    @property
    def step_insights(self) -> dict[str, dict]:
        return self._step_insights

    def _store_step_advice(
        self,
        step_index: int,
        step: AutomationStep,
        slot: str,
        advice: ExecutionAdvice,
    ) -> None:
        key = str(step_index + 1)
        entry = self._step_insights.setdefault(
            key,
            {
                "step_index": step_index + 1,
                "step_id": step.id,
                "step_type": step.type.value,
                "description": step.description or "",
            },
        )
        entry[slot] = advice.model_dump()

    def _is_risky_step(self, step: AutomationStep) -> bool:
        metadata = step.metadata or {}
        target = step.target
        strategy = target.strategy if target else None
        return any(
            (
                strategy == LocateStrategy.POSITION,
                not step.verification_enabled,
                bool(metadata.get("locator_requires_confirmation")),
                (step.retry_count or 0) > 0,
                step.type in {StepType.UPLOAD_FILE, StepType.DOWNLOAD_FILE, StepType.DRAG},
            )
        )

    def _should_use_local_ai(
        self,
        *,
        phase: str,
        step: AutomationStep,
        success: bool | None = None,
        warned: bool = False,
    ) -> bool:
        if not settings.LOCAL_LLM_ENABLED:
            return False
        if self._options.mode == "always":
            return True
        if phase == "summary":
            return True
        if phase == "assist":
            return bool(self._options.assist_enabled)
        if success is False or warned:
            return True
        return self._is_risky_step(step)

    async def _run_local_ai(self, system_prompt: str, payload: dict, *, timeout_s: float | None = None) -> dict | None:
        async def _invoke():
            engine = await LocalLLMEngine.get_instance()
            return await engine.generate(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.2,
                max_tokens=min(settings.LOCAL_LLM_MAX_TOKENS, 512),
            )

        try:
            result = await asyncio.wait_for(_invoke(), timeout=timeout_s) if timeout_s else await _invoke()
        except TimeoutError:
            logger.info("Execution advisor local AI timed out after %.1fs", timeout_s or 0.0)
            return None
        except Exception as exc:
            logger.debug("Execution advisor local AI unavailable: %s", exc)
            return None

        parsed = _extract_json(result.text)
        if parsed:
            self._used_local_ai = True
        return parsed

    @staticmethod
    def _normalize_image_inputs(*images: str | None, limit: int = 3) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in images:
            if not isinstance(item, str):
                continue
            candidate = item.strip()
            if not candidate:
                continue
            if candidate in seen:
                continue
            normalized.append(candidate)
            seen.add(candidate)
            if len(normalized) >= limit:
                break
        return normalized

    async def _run_local_ai_with_images(
        self,
        system_prompt: str,
        payload: dict,
        image_base64s: list[str],
        *,
        timeout_s: float | None = None,
    ) -> dict | None:
        if not (settings.VISION_ENABLED and image_base64s):
            return await self._run_local_ai(system_prompt, payload, timeout_s=timeout_s)

        async def _invoke():
            engine = await LocalLLMEngine.get_instance()
            prompt_text = json.dumps(payload, ensure_ascii=False)
            if len(image_base64s) == 1:
                return await engine.analyze_with_image(
                    prompt=prompt_text,
                    image_base64=image_base64s[0],
                    system_prompt=system_prompt,
                )
            return await engine.generate_with_images(
                prompt_text=prompt_text,
                image_base64s=image_base64s[:3],
                system_prompt=system_prompt,
            )

        try:
            result = await asyncio.wait_for(_invoke(), timeout=timeout_s) if timeout_s else await _invoke()
        except TimeoutError:
            logger.info("Execution advisor vision AI timed out after %.1fs", timeout_s or 0.0)
            return None
        except Exception as exc:
            logger.debug("Execution advisor vision AI unavailable: %s", exc)
            return None

        parsed = _extract_json(result.text)
        if parsed:
            self._used_local_ai = True
        return parsed

    @staticmethod
    def _collect_summary_visuals(record: ExecutionRecord, max_images: int = 3) -> tuple[list[str], list[dict]]:
        images: list[str] = []
        contexts: list[dict] = []
        if not record.step_logs:
            return images, contexts

        candidate_logs = sorted(
            record.step_logs,
            key=lambda log: (
                0 if log.status == StepStatus.FAILED else 1,
                0 if (log.error_message or "") else 1,
                log.step_index,
            ),
        )
        for log in candidate_logs:
            if len(images) >= max_images:
                break
            for label, image in (("after", log.screenshot_after), ("before", log.screenshot_before)):
                if len(images) >= max_images:
                    break
                if not isinstance(image, str) or not image.strip():
                    continue
                images.append(image)
                contexts.append(
                    {
                        "step_id": log.step_id,
                        "step_index": log.step_index + 1,
                        "status": log.status.value if hasattr(log.status, "value") else str(log.status),
                        "image_role": label,
                        "error_message": log.error_message,
                    }
                )
        return images, contexts

    def _build_rule_precheck(self, step: AutomationStep, step_index: int) -> ExecutionAdvice:
        warnings: list[str] = []
        suggestions: list[str] = []
        metadata = step.metadata or {}
        target = step.target

        if target and target.strategy == LocateStrategy.POSITION:
            warnings.append("该步骤主要依赖坐标定位，界面偏移或分辨率变化时容易失效。")
            suggestions.append("优先补充 selector、xpath 或 accessibility_id。")
        if metadata.get("locator_requires_confirmation"):
            warnings.append("该步骤录制时目标定位不稳定，执行前应确认页面已处于正确状态。")
            suggestions.append("为该步骤补充更稳定的定位信息或前置检查。")
        if not step.verification_enabled:
            warnings.append("该步骤未开启执行验证，成功与否只能依赖后续步骤间接判断。")
            suggestions.append("为关键步骤增加结果检查或 postcondition。")
        if step.delay and step.delay > 1500:
            suggestions.append("当前步骤等待时间较长，可考虑用显式条件替代固定延迟。")

        level = "warning" if warnings else "info"
        summary = "步骤执行前已完成规则检查。" if not warnings else "步骤执行前发现潜在脆弱点。"
        return ExecutionAdvice(
            phase="pre",
            level=level,
            title=f"步骤 {step_index + 1} 执行前检查",
            summary=summary,
            warnings=warnings,
            suggestions=suggestions,
        )

    def _build_rule_postcheck(
        self,
        step: AutomationStep,
        step_index: int,
        success: bool,
        error: str | None,
        verification_result: dict | None,
        visual_comparison: dict | None,
    ) -> ExecutionAdvice:
        warnings: list[str] = []
        suggestions: list[str] = []
        level = "success" if success else "error"

        if not success and error:
            warnings.append(error)
            suggestions.append("先确认目标窗口/页面状态，再重试该步骤。")

        verification_level = (verification_result or {}).get("overall_level")
        recovery_suggestion = (verification_result or {}).get("recovery_suggestion")
        if verification_level == "warning":
            level = "warning"
            warnings.append("步骤执行后存在验证警告，结果可能并不稳定。")
            if recovery_suggestion:
                suggestions.append(recovery_suggestion)
        elif verification_level == "failed":
            level = "error"
            warnings.append("步骤执行后验证失败，结果不可依赖。")
            if recovery_suggestion:
                suggestions.append(recovery_suggestion)

        if visual_comparison and visual_comparison.get("is_significant") is False and step.type in {
            StepType.CLICK,
            StepType.TYPE,
            StepType.NAVIGATE,
            StepType.UPLOAD_FILE,
            StepType.DOWNLOAD_FILE,
        }:
            warnings.append("执行后未检测到明显界面变化，可能点到了错误位置或页面未响应。")
            suggestions.append("为该步骤增加更明确的结果校验。")
            if level == "success":
                level = "warning"

        summary = "步骤执行成功。" if level == "success" else "步骤执行后需要人工关注。"
        return ExecutionAdvice(
            phase="post",
            level=level,
            title=f"步骤 {step_index + 1} 结果检查",
            summary=summary,
            warnings=warnings,
            suggestions=suggestions,
        )

    def _build_rule_assist(
        self,
        step: AutomationStep,
        step_index: int,
        error: str | None,
        verification_result: dict | None,
    ) -> ExecutionAdvice:
        suggestions: list[str] = []
        warnings: list[str] = []

        if error:
            warnings.append(error)
        if step.target and step.target.strategy == LocateStrategy.POSITION:
            suggestions.append("优先回到录制页面，为该步骤补充稳定定位器后再执行。")
        if verification_result and verification_result.get("recovery_suggestion"):
            suggestions.append(verification_result["recovery_suggestion"])
        if not suggestions:
            suggestions.append("先确认前置窗口、页面和登录态，再执行当前步骤。")

        return ExecutionAdvice(
            phase="assist",
            level="warning",
            title=f"步骤 {step_index + 1} 协助执行建议",
            summary="该步骤执行失败，已生成协助执行建议。",
            warnings=warnings,
            suggestions=suggestions,
        )

    def _merge_advice(self, base: ExecutionAdvice, parsed: dict | None) -> ExecutionAdvice:
        if not parsed:
            return base

        return ExecutionAdvice(
            phase=base.phase,
            level=str(parsed.get("level") or base.level),
            title=str(parsed.get("title") or base.title),
            summary=str(parsed.get("summary") or base.summary),
            rationale=parsed.get("rationale") or base.rationale,
            warnings=[str(item) for item in parsed.get("warnings", [])] or base.warnings,
            suggestions=[str(item) for item in parsed.get("suggestions", [])] or base.suggestions,
            provider="local_ai",
            local_ai_used=True,
        )

    async def advise_before_step(
        self,
        step: AutomationStep,
        step_index: int,
        total_steps: int,
    ) -> ExecutionAdvice | None:
        if not (self.enabled and self._options.check_enabled):
            return None

        advice = self._build_rule_precheck(step, step_index)
        if self._should_use_local_ai(
            phase="pre",
            step=step,
            warned=advice.level == "warning",
        ):
            parsed = await self._run_local_ai(
                (
                    "你是桌面/Web 自动化执行顾问。请基于提供的步骤信息输出严格 JSON："
                    '{"title":"", "summary":"", "level":"info|warning|success|error", '
                    '"rationale":"", "warnings":[""], "suggestions":[""]}。'
                    "不要输出任何额外文字。"
                ),
                {
                    "phase": "pre",
                    "step_index": step_index + 1,
                    "total_steps": total_steps,
                    "step": step.model_dump(mode="json"),
                    "base_advice": advice.model_dump(),
                },
                timeout_s=LOCAL_AI_TIMEOUT_S["pre"],
            )
            advice = self._merge_advice(advice, parsed)

        self._store_step_advice(step_index, step, "pre", advice)
        return advice

    async def advise_after_step(
        self,
        step: AutomationStep,
        step_index: int,
        total_steps: int,
        success: bool,
        error: str | None,
        verification_result: dict | None,
        visual_comparison: dict | None,
        screenshot_before: str | None = None,
        screenshot_after: str | None = None,
    ) -> ExecutionAdvice | None:
        if not (self.enabled and self._options.check_enabled):
            return None

        advice = self._build_rule_postcheck(
            step=step,
            step_index=step_index,
            success=success,
            error=error,
            verification_result=verification_result,
            visual_comparison=visual_comparison,
        )

        warned = advice.level in {"warning", "error"}
        if self._should_use_local_ai(phase="post", step=step, success=success, warned=warned):
            images = self._normalize_image_inputs(screenshot_before, screenshot_after, limit=2)
            parsed = await self._run_local_ai_with_images(
                (
                    "你是桌面/Web 自动化执行复盘顾问。若提供了两张图片，则第一张是步骤执行前截图，第二张是执行后截图；"
                    "请结合视觉变化、执行结果和校验信息输出严格 JSON："
                    '{"title":"", "summary":"", "level":"info|warning|success|error", '
                    '"rationale":"", "warnings":[""], "suggestions":[""]}。'
                    "不要输出任何额外文字。"
                ),
                {
                    "phase": "post",
                    "step_index": step_index + 1,
                    "total_steps": total_steps,
                    "step": step.model_dump(mode="json"),
                    "success": success,
                    "error": error,
                    "verification_result": verification_result,
                    "visual_comparison": visual_comparison,
                    "visual_inputs": {
                        "image_count": len(images),
                        "image_order": ["before", "after"][: len(images)],
                    },
                    "base_advice": advice.model_dump(),
                },
                images,
                timeout_s=LOCAL_AI_TIMEOUT_S["post"],
            )
            advice = self._merge_advice(advice, parsed)

        self._store_step_advice(step_index, step, "post", advice)
        return advice

    async def advise_assist(
        self,
        step: AutomationStep,
        step_index: int,
        total_steps: int,
        error: str | None,
        verification_result: dict | None,
        screenshot_before: str | None = None,
        screenshot_after: str | None = None,
    ) -> ExecutionAdvice | None:
        if not (self.enabled and self._options.assist_enabled):
            return None

        advice = self._build_rule_assist(step, step_index, error, verification_result)
        if self._should_use_local_ai(phase="assist", step=step, success=False, warned=True):
            images = self._normalize_image_inputs(screenshot_after, screenshot_before, limit=2)
            parsed = await self._run_local_ai_with_images(
                (
                    "你是桌面/Web 自动化执行协助顾问。若提供图片，则它们表示当前失败现场与执行上下文；"
                    "请输出严格 JSON："
                    '{"title":"", "summary":"", "level":"warning|error|info", '
                    '"rationale":"", "warnings":[""], "suggestions":[""]}。'
                    "重点给出下一步可操作建议，不要输出任何额外文字。"
                ),
                {
                    "phase": "assist",
                    "step_index": step_index + 1,
                    "total_steps": total_steps,
                    "step": step.model_dump(mode="json"),
                    "error": error,
                    "verification_result": verification_result,
                    "visual_inputs": {
                        "image_count": len(images),
                        "image_order": ["after", "before"][: len(images)],
                    },
                    "base_advice": advice.model_dump(),
                },
                images,
                timeout_s=LOCAL_AI_TIMEOUT_S["assist"],
            )
            advice = self._merge_advice(advice, parsed)

        self._store_step_advice(step_index, step, "assist", advice)
        return advice

    def _build_rule_summary(self, flow: AutomationFlow, record: ExecutionRecord) -> ExecutionSummary:
        warnings: list[str] = []
        optimization_items: list[str] = []
        fragile_steps: list[ExecutionFragileStep] = []

        if record.failed_steps > 0:
            warnings.append(f"本次执行共有 {record.failed_steps} 个失败步骤，优先修复失败链路。")
        if record.error_summary:
            warnings.append(record.error_summary)

        position_steps = [
            step
            for step in flow.steps
            if step.target and step.target.strategy == LocateStrategy.POSITION
        ]
        unverified_steps = [step for step in flow.steps if not step.verification_enabled]
        uncertain_steps = [
            step
            for step in flow.steps
            if (step.metadata or {}).get("locator_requires_confirmation")
        ]

        if position_steps:
            optimization_items.append(f"当前有 {len(position_steps)} 个步骤仍依赖坐标定位，建议优先替换为稳定定位器。")
        if unverified_steps:
            optimization_items.append(
                f"当前有 {len(unverified_steps)} 个步骤未开启执行验证，建议为关键节点补充结果检查。"
            )
        if uncertain_steps:
            optimization_items.append(
                f"当前有 {len(uncertain_steps)} 个步骤录制时已标记为定位不稳定，建议重新录制或补齐前置条件。"
            )

        failed_step_ids = {log.step_id for log in (record.step_logs or []) if log.status == StepStatus.FAILED}
        for index, step in enumerate(flow.steps, start=1):
            if step.id in failed_step_ids:
                fragile_steps.append(
                    ExecutionFragileStep(
                        step_index=index,
                        step_id=step.id,
                        reason="本次执行失败",
                        recommendation="优先检查目标窗口、定位器和前置状态。",
                    )
                )
            elif step.target and step.target.strategy == LocateStrategy.POSITION:
                fragile_steps.append(
                    ExecutionFragileStep(
                        step_index=index,
                        step_id=step.id,
                        reason="依赖坐标定位",
                        recommendation="补充 selector / xpath / accessibility_id。",
                    )
                )
            elif (step.metadata or {}).get("locator_requires_confirmation"):
                fragile_steps.append(
                    ExecutionFragileStep(
                        step_index=index,
                        step_id=step.id,
                        reason="录制阶段已提示定位不稳定",
                        recommendation="重新确认目标元素并添加前置校验。",
                    )
                )

        overview = (
            f"执行状态：{record.status.value}。共 {record.total_steps} 步，"
            f"成功 {record.completed_steps} 步，失败 {record.failed_steps} 步。"
        )
        return ExecutionSummary(
            overview=overview,
            warnings=warnings[:5],
            optimization_items=optimization_items[:5],
            fragile_steps=fragile_steps[:8],
            provider="local_ai" if self._used_local_ai else "rules",
            local_ai_used=self._used_local_ai,
        )

    async def build_summary(self, flow: AutomationFlow, record: ExecutionRecord) -> ExecutionSummary | None:
        if not (self.enabled and self._options.summary_enabled):
            return None

        summary = self._build_rule_summary(flow, record)
        summary_step = flow.steps[0] if flow.steps else AutomationStep(id="summary", type=StepType.WAIT)
        if self._should_use_local_ai(phase="summary", step=summary_step):
            summary_images, visual_context = self._collect_summary_visuals(record)
            parsed = await self._run_local_ai_with_images(
                (
                    "你是桌面/Web 自动化执行优化顾问。若提供了图片，它们来自本次执行中最值得关注的步骤截图；"
                    "请结合这些视觉线索输出严格 JSON："
                    '{"overview":"", "warnings":[""], "optimization_items":[""], '
                    '"fragile_steps":[{"step_index":1,"step_id":"","reason":"","recommendation":""}]}.'
                    "仅输出 JSON，不要输出其他文字。"
                ),
                {
                    "flow": flow.model_dump(mode="json"),
                    "record": record.model_dump(mode="json"),
                    "step_insights": self._step_insights,
                    "visual_context": visual_context,
                    "base_summary": summary.model_dump(),
                },
                summary_images,
                timeout_s=LOCAL_AI_TIMEOUT_S["summary"],
            )
            if parsed:
                try:
                    summary = ExecutionSummary(
                        overview=str(parsed.get("overview") or summary.overview),
                        warnings=[str(item) for item in parsed.get("warnings", [])] or summary.warnings,
                        optimization_items=[str(item) for item in parsed.get("optimization_items", [])]
                        or summary.optimization_items,
                        fragile_steps=[
                            ExecutionFragileStep(
                                step_index=int(item.get("step_index", 0)),
                                step_id=str(item.get("step_id", "")),
                                reason=str(item.get("reason", "")),
                                recommendation=item.get("recommendation"),
                            )
                            for item in parsed.get("fragile_steps", [])
                            if isinstance(item, dict)
                        ]
                        or summary.fragile_steps,
                        provider="local_ai",
                        local_ai_used=True,
                    )
                except Exception as exc:
                    logger.debug("Execution advisor summary parse failed: %s", exc)

        return summary
