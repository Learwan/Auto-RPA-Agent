from __future__ import annotations

import logging
import math
import time

from src.executor.element_locator import ElementLocator, LocatedElement
from src.models.automation import StepTarget
from src.models.desktop import Rect
from src.models.execution import (
    AttributeMatchResult,
    VerificationLevel,
    VerificationResult,
)
from src.platform.base import BasePlatformAdapter

logger = logging.getLogger(__name__)

COORDINATE_TOLERANCE_PX = 50.0
CORE_ATTRIBUTES = {"identifier", "selector", "xpath"}
FUZZY_ATTRIBUTES = {"title", "role", "class_name"}
MIN_ELEMENT_SIZE_PX = 5
STABILITY_CHECK_INTERVAL_S = 0.05
STABILITY_CHECK_COUNT = 2


class AdaptiveTolerance:
    def __init__(self, base_tolerance_px: float = COORDINATE_TOLERANCE_PX):
        self._base = base_tolerance_px
        self._history: list[float] = []

    def compute(self, element_bounds: Rect | None = None) -> float:
        tolerance = self._base
        if element_bounds is not None:
            diagonal = math.sqrt(element_bounds.width ** 2 + element_bounds.height ** 2)
            if diagonal > 0:
                tolerance = max(self._base, diagonal * 0.15)
        if self._history:
            avg_offset = sum(self._history[-10:]) / len(self._history[-10:])
            tolerance = max(tolerance, avg_offset * 1.5)
        return tolerance

    def record_offset(self, offset_px: float) -> None:
        self._history.append(offset_px)
        if len(self._history) > 50:
            self._history = self._history[-50:]


class ElementStateChecker:
    NON_INTERACTIVE_ROLES = frozenset({
        "statictext", "image", "separator", "text", "heading",
        "group", "unknown", "generic", "none",
    })

    def __init__(self, platform_adapter: BasePlatformAdapter):
        self._adapter = platform_adapter

    async def check_actionability(self, located: LocatedElement, target: StepTarget) -> ActionabilityReport:
        checks: dict[str, bool] = {}
        details: list[str] = []
        element = located.element

        visible = self._check_visible(element)
        checks["visible"] = visible
        if not visible:
            details.append("元素不可见")

        enabled = self._check_enabled(element)
        checks["enabled"] = enabled
        if not enabled:
            details.append("元素可能被禁用")

        interactive = self._check_interactive(element)
        checks["interactive"] = interactive
        if not interactive:
            details.append(f"元素角色 {element.role} 可能不可交互")

        in_viewport = await self._check_in_viewport(element)
        checks["in_viewport"] = in_viewport
        if not in_viewport:
            details.append("元素不在可视区域内")

        stable = await self._check_stable(element)
        checks["stable"] = stable
        if not stable:
            details.append("元素位置不稳定（可能正在动画）")

        not_obscured = self._check_not_obscured(element)
        checks["not_obscured"] = not_obscured
        if not not_obscured:
            details.append("元素可能被遮挡")

        all_passed = all(checks.values())
        score = sum(1.0 for v in checks.values() if v) / len(checks) if checks else 0.0

        return ActionabilityReport(
            checks=checks,
            details="; ".join(details) if details else "所有可操作性检查通过",
            all_passed=all_passed,
            score=round(score, 3),
        )

    def _check_visible(self, element) -> bool:
        if element.bounds is None:
            return False
        b = element.bounds
        if b.width <= 0 or b.height <= 0:
            return False
        return not (b.x < -b.width or b.y < -b.height)

    def _check_enabled(self, element) -> bool:
        attrs = {}
        if hasattr(element, "attributes") and element.attributes:
            attrs = element.attributes
        disabled = attrs.get("disabled", False) or attrs.get("aria-disabled", "true") == "true"
        return not disabled

    def _check_interactive(self, element) -> bool:
        return not (element.role and element.role.lower() in self.NON_INTERACTIVE_ROLES)

    async def _check_in_viewport(self, element) -> bool:
        if element.bounds is None:
            return False
        try:
            screen_w, screen_h = await self._adapter.get_screen_size()
        except Exception:
            return True
        b = element.bounds
        if b.x + b.width < 0 or b.y + b.height < 0:
            return False
        if b.x > screen_w or b.y > screen_h:
            return False
        visible_area = max(0, min(b.x + b.width, screen_w) - max(b.x, 0)) * \
                       max(0, min(b.y + b.height, screen_h) - max(b.y, 0))
        total_area = b.width * b.height
        if total_area <= 0:
            return False
        return (visible_area / total_area) >= 0.3

    async def _check_stable(self, element) -> bool:
        if element.bounds is None:
            return True
        if not hasattr(self, '_locator') or self._locator is None:
            return True
        initial_bounds = element.bounds
        import asyncio
        for _ in range(STABILITY_CHECK_COUNT):
            await asyncio.sleep(STABILITY_CHECK_INTERVAL_S)
            try:
                re_located = await self._locator.locate(
                    StepTarget(
                        strategy=getattr(element, '_strategy_used', None) or element.role,
                        accessibility_id=element.identifier,
                    )
                ) if hasattr(element, 'identifier') and element.identifier else None
                if re_located and re_located.element and re_located.element.bounds:
                    b = re_located.element.bounds
                    if (abs(b.x - initial_bounds.x) > 3 or abs(b.y - initial_bounds.y) > 3
                            or abs(b.width - initial_bounds.width) > 3 or abs(b.height - initial_bounds.height) > 3):
                        return False
            except Exception:
                pass
        return True

    def _check_not_obscured(self, element) -> bool:
        if element.bounds is None:
            return True
        b = element.bounds
        return not (b.width < MIN_ELEMENT_SIZE_PX or b.height < MIN_ELEMENT_SIZE_PX)


class ActionabilityReport:
    def __init__(
        self,
        checks: dict[str, bool],
        details: str,
        all_passed: bool,
        score: float,
    ):
        self.checks = checks
        self.details = details
        self.all_passed = all_passed
        self.score = score


class VisualVerificationResult:
    def __init__(
        self,
        similarity: float,
        changed_regions: int,
        is_significant: bool,
        details: str,
    ):
        self.similarity = similarity
        self.changed_regions = changed_regions
        self.is_significant = is_significant
        self.details = details


class StepVerifier:
    def __init__(
        self,
        locator: ElementLocator,
        platform_adapter: BasePlatformAdapter,
        coordinate_tolerance_px: float = COORDINATE_TOLERANCE_PX,
        grounding_engine=None,
    ):
        self._locator = locator
        self._adapter = platform_adapter
        self._adaptive_tolerance = AdaptiveTolerance(coordinate_tolerance_px)
        self._state_checker = ElementStateChecker(platform_adapter)
        self._grounding_engine = grounding_engine
        self._verification_history: list[dict] = []

    async def verify(
        self,
        target: StepTarget,
        located: LocatedElement | None = None,
        strict: bool = False,
        screenshot_before: bytes | None = None,
        screenshot_after: bytes | None = None,
        action_description: str | None = None,
    ) -> VerificationResult:
        if located is None:
            located = await self._locator.locate(target)

        if located is None:
            return VerificationResult(
                visual_verification=VerificationLevel.FAILED,
                visual_details="元素未找到，无法执行验证",
                coordinate_verification=VerificationLevel.FAILED,
                coordinate_details="元素未找到，无法验证坐标",
                attribute_verification=VerificationLevel.FAILED,
                attribute_details="元素未找到，无法验证属性",
                overall_level=VerificationLevel.FAILED,
                overall_confidence=0.0,
                can_proceed=False,
                recovery_suggestion="等待页面加载后重试，或检查元素定位策略是否正确",
            )

        visual_result = self._verify_visual(located, target)
        coord_result = self._verify_coordinate(located, target)
        attr_result = self._verify_attributes(located, target)
        state_result = await self._state_checker.check_actionability(located, target)

        visual_verification = self._integrate_state_into_visual(
            visual_result.visual_verification, state_result
        )

        if screenshot_before and screenshot_after and action_description:
            post_result = await self._verify_post_action(
                screenshot_before, screenshot_after, action_description
            )
            if post_result and not post_result.is_significant and visual_verification == VerificationLevel.PASSED:
                visual_verification = VerificationLevel.WARNING

        overall_level = self._compute_overall_level_v2(
            visual_verification,
            coord_result.coordinate_verification,
            attr_result.attribute_verification,
            state_result,
            strict,
        )

        overall_confidence = self._compute_overall_confidence_v2(
            visual_result, coord_result, attr_result, state_result,
        )

        can_proceed = overall_level != VerificationLevel.FAILED
        if strict:
            can_proceed = overall_level == VerificationLevel.PASSED

        recovery_suggestion = self._generate_recovery_suggestion_v2(
            visual_result, coord_result, attr_result, state_result, overall_level
        )

        self._record_verification(located, overall_level, overall_confidence, state_result)

        state_detail = f" [状态: {state_result.details}]" if not state_result.all_passed else ""
        return VerificationResult(
            visual_verification=visual_verification,
            visual_details=visual_result.visual_details + state_detail,
            coordinate_verification=coord_result.coordinate_verification,
            coordinate_distance_px=coord_result.coordinate_distance_px,
            coordinate_details=coord_result.coordinate_details,
            attribute_verification=attr_result.attribute_verification,
            attribute_matches=attr_result.attribute_matches,
            attribute_details=attr_result.attribute_details,
            overall_level=overall_level,
            overall_confidence=overall_confidence,
            can_proceed=can_proceed,
            recovery_suggestion=recovery_suggestion,
        )

    async def verify_post_action(
        self,
        screenshot_before: bytes,
        screenshot_after: bytes,
        action_description: str,
    ) -> VisualVerificationResult | None:
        return await self._verify_post_action(
            screenshot_before, screenshot_after, action_description
        )

    def _verify_visual(self, located: LocatedElement, target: StepTarget) -> VerificationResult:
        element = located.element
        details_parts: list[str] = []
        level = VerificationLevel.PASSED

        if element.bounds is None:
            details_parts.append("元素无边界信息")
            level = VerificationLevel.WARNING
        else:
            screen_w, screen_h = 0, 0
            try:
                import pyautogui

                screen_w, screen_h = pyautogui.size()
            except Exception:
                pass

            if screen_w > 0 and screen_h > 0:
                bounds = element.bounds
                if (
                    bounds.x < 0
                    or bounds.y < 0
                    or bounds.x + bounds.width > screen_w
                    or bounds.y + bounds.height > screen_h
                ):
                    details_parts.append(
                        f"元素部分超出屏幕可见区域 "
                        f"(bounds=({bounds.x},{bounds.y}) "
                        f"{bounds.width}x{bounds.height}, screen=({screen_w}x{screen_h}))"
                    )
                    level = VerificationLevel.WARNING
                else:
                    details_parts.append(
                        f"元素在屏幕可见区域内 "
                        f"(bounds=({bounds.x},{bounds.y}) {bounds.width}x{bounds.height})"
                    )
            else:
                details_parts.append("无法获取屏幕尺寸，跳过可见区域检查")

        if element.role and element.role.lower() in ElementStateChecker.NON_INTERACTIVE_ROLES:
            details_parts.append(f"元素角色为 {element.role}，可能不可交互")
            if level == VerificationLevel.PASSED:
                level = VerificationLevel.WARNING

        if not details_parts:
            details_parts.append("元素可见性检查通过")

        return VerificationResult(
            visual_verification=level,
            visual_details="; ".join(details_parts),
        )

    def _verify_coordinate(self, located: LocatedElement, target: StepTarget) -> VerificationResult:
        if target.position is None:
            return VerificationResult(
                coordinate_verification=VerificationLevel.PASSED,
                coordinate_details="目标无预期坐标，跳过坐标验证",
            )

        if located.center is None:
            return VerificationResult(
                coordinate_verification=VerificationLevel.WARNING,
                coordinate_details="定位到的元素无中心坐标",
            )

        expected_x = target.position.x
        expected_y = target.position.y
        actual_x = located.center.x
        actual_y = located.center.y

        distance = math.sqrt((actual_x - expected_x) ** 2 + (actual_y - expected_y) ** 2)

        tolerance = self._adaptive_tolerance.compute(located.element.bounds)
        self._adaptive_tolerance.record_offset(distance)

        if distance <= tolerance:
            level = VerificationLevel.PASSED
            details = f"坐标偏差 {distance:.1f}px 在容忍范围内 (≤{tolerance:.1f}px, 自适应)"
        elif distance <= tolerance * 2:
            level = VerificationLevel.WARNING
            details = (
                f"坐标偏差 {distance:.1f}px 超出容忍范围但可能因窗口位置变化 "
                f"(预期=({expected_x},{expected_y}), 实际=({actual_x},{actual_y}), "
                f"容忍={tolerance:.1f}px)"
            )
        else:
            level = VerificationLevel.FAILED
            details = (
                f"坐标偏差 {distance:.1f}px 严重超出容忍范围 "
                f"(预期=({expected_x},{expected_y}), 实际=({actual_x},{actual_y}), "
                f"容忍={tolerance:.1f}px)"
            )

        return VerificationResult(
            coordinate_verification=level,
            coordinate_distance_px=round(distance, 1),
            coordinate_details=details,
        )

    def _verify_attributes(self, located: LocatedElement, target: StepTarget) -> VerificationResult:
        element = located.element
        matches: list[AttributeMatchResult] = []
        core_mismatch = False
        aux_mismatch = False

        attribute_checks = [
            ("identifier", target.accessibility_id, element.identifier),
            ("role", target.role, element.role),
            ("title", target.title, element.title),
            ("class_name", target.class_name, element.class_name),
            ("selector", target.selector, element.selector),
            ("xpath", target.xpath, element.xpath),
        ]

        expected_attrs = target.expected_attributes or {}
        existing_keys = {k for k, _, _ in attribute_checks}
        for key, value in expected_attrs.items():
            if key not in existing_keys:
                actual = getattr(element, key, None)
                attribute_checks.append(
                    (key, str(value) if value is not None else None, actual)
                )

        for attr_name, expected, actual in attribute_checks:
            if expected is None:
                continue

            is_core = attr_name in CORE_ATTRIBUTES
            matched = False

            if actual is None:
                matched = False
            elif is_core:
                matched = str(actual) == str(expected)
            else:
                exp_lower = str(expected).lower()
                act_lower = str(actual).lower()
                matched = exp_lower in act_lower or act_lower in exp_lower

            result = AttributeMatchResult(
                attribute_name=attr_name,
                expected=str(expected) if expected is not None else None,
                actual=str(actual) if actual is not None else None,
                matched=matched,
                is_core=is_core,
            )
            matches.append(result)

            if not matched:
                if is_core:
                    core_mismatch = True
                else:
                    aux_mismatch = True

        if core_mismatch:
            level = VerificationLevel.FAILED
        elif aux_mismatch:
            level = VerificationLevel.WARNING
        elif not matches:
            level = VerificationLevel.PASSED
        else:
            level = VerificationLevel.PASSED

        mismatched = [m for m in matches if not m.matched]
        if not mismatched:
            details = f"属性验证通过 ({len(matches)} 项匹配)"
        else:
            core_fail = [m for m in mismatched if m.is_core]
            aux_fail = [m for m in mismatched if not m.is_core]
            parts = []
            if core_fail:
                parts.append(f"核心属性不匹配: {', '.join(m.attribute_name for m in core_fail)}")
            if aux_fail:
                parts.append(f"辅助属性不匹配: {', '.join(m.attribute_name for m in aux_fail)}")
            details = "; ".join(parts)

        return VerificationResult(
            attribute_verification=level,
            attribute_matches=matches,
            attribute_details=details,
        )

    async def _verify_post_action(
        self,
        screenshot_before: bytes,
        screenshot_after: bytes,
        action_description: str,
    ) -> VisualVerificationResult | None:
        if self._grounding_engine is None:
            return self._pixel_comparison_verify(screenshot_before, screenshot_after)

        try:
            import base64

            before_b64 = base64.b64encode(screenshot_before).decode("utf-8")
            after_b64 = base64.b64encode(screenshot_after).decode("utf-8")

            success, reasoning = await self._grounding_engine.verify_action(
                before_base64=before_b64,
                after_base64=after_b64,
                expected_action={"description": action_description},
            )

            similarity = 0.9 if success else 0.5
            return VisualVerificationResult(
                similarity=similarity,
                changed_regions=1 if success else 0,
                is_significant=success,
                details=f"视觉验证(LLM): {'成功' if success else '未检测到预期变化'} - {reasoning[:100]}",
            )
        except Exception as e:
            logger.debug(f"Grounding engine verification failed, falling back to pixel comparison: {e}")
            return self._pixel_comparison_verify(screenshot_before, screenshot_after)

    def _pixel_comparison_verify(
        self,
        screenshot_before: bytes,
        screenshot_after: bytes,
    ) -> VisualVerificationResult | None:
        try:
            from src.executor.screenshot_comparator import ScreenshotComparator

            comparator = ScreenshotComparator()
            screen_w, screen_h = self._adapter.get_screen_size()
            if isinstance(screen_w, int) and isinstance(screen_h, int):
                result = comparator.compare(
                    screenshot_before, screenshot_after, screen_w, screen_h
                )
                return VisualVerificationResult(
                    similarity=result.similarity,
                    changed_regions=1 if result.is_significant else 0,
                    is_significant=result.is_significant,
                    details=(
                        f"像素对比: similarity={result.similarity:.3f}, "
                        f"change_ratio={result.change_ratio:.4f}"
                    ),
                )
        except Exception as e:
            logger.debug(f"Pixel comparison failed: {e}")
        return None

    def _integrate_state_into_visual(
        self,
        visual_level: VerificationLevel,
        state: ActionabilityReport,
    ) -> VerificationLevel:
        if not state.all_passed:
            if state.score < 0.4:
                return VerificationLevel.FAILED
            if visual_level == VerificationLevel.PASSED:
                return VerificationLevel.WARNING
        return visual_level

    @staticmethod
    def _compute_overall_level_v2(
        visual: VerificationLevel,
        coordinate: VerificationLevel,
        attribute: VerificationLevel,
        state: ActionabilityReport,
        strict: bool,
    ) -> VerificationLevel:
        levels = [visual, coordinate, attribute]

        if any(lvl == VerificationLevel.FAILED for lvl in levels):
            return VerificationLevel.FAILED

        if not state.all_passed and state.score < 0.4:
            return VerificationLevel.FAILED

        if strict:
            if any(lvl == VerificationLevel.WARNING for lvl in levels):
                return VerificationLevel.WARNING
            if not state.all_passed:
                return VerificationLevel.WARNING
            return VerificationLevel.PASSED

        if all(lvl == VerificationLevel.PASSED for lvl in levels) and state.all_passed:
            return VerificationLevel.PASSED

        return VerificationLevel.WARNING

    @staticmethod
    def _compute_overall_confidence_v2(
        visual: VerificationResult,
        coord: VerificationResult,
        attr: VerificationResult,
        state: ActionabilityReport,
    ) -> float:
        level_scores = {
            VerificationLevel.PASSED: 1.0,
            VerificationLevel.WARNING: 0.6,
            VerificationLevel.FAILED: 0.0,
        }

        visual_score = level_scores[visual.visual_verification]
        coord_score = level_scores[coord.coordinate_verification]
        attr_score = level_scores[attr.attribute_verification]

        if attr.attribute_matches:
            total = len(attr.attribute_matches)
            matched = sum(1 for m in attr.attribute_matches if m.matched)
            attr_detail_score = matched / total if total > 0 else 1.0
        else:
            attr_detail_score = 1.0

        overall = (
            visual_score * 0.20
            + coord_score * 0.15
            + attr_score * 0.25
            + attr_detail_score * 0.15
            + state.score * 0.25
        )
        return round(overall, 3)

    @staticmethod
    def _generate_recovery_suggestion_v2(
        visual: VerificationResult,
        coord: VerificationResult,
        attr: VerificationResult,
        state: ActionabilityReport,
        overall: VerificationLevel,
    ) -> str:
        if overall == VerificationLevel.PASSED:
            return ""

        suggestions: list[str] = []

        if visual.visual_verification == VerificationLevel.FAILED:
            suggestions.append("元素不可见，建议等待页面加载完成后重试")
        elif visual.visual_verification == VerificationLevel.WARNING:
            suggestions.append("元素部分超出屏幕，建议先滚动或调整窗口位置")

        if coord.coordinate_verification == VerificationLevel.FAILED:
            suggestions.append("坐标偏差过大，建议重新定位元素或更新目标坐标")
        elif coord.coordinate_verification == VerificationLevel.WARNING:
            suggestions.append("坐标有偏移但元素属性匹配，可尝试使用新坐标执行")

        if attr.attribute_verification == VerificationLevel.FAILED:
            core_mismatch = [
                m for m in attr.attribute_matches if m.is_core and not m.matched
            ]
            if core_mismatch:
                names = ", ".join(m.attribute_name for m in core_mismatch)
                suggestions.append(
                    f"核心属性不匹配 ({names})，建议检查目标元素是否正确"
                )
        elif attr.attribute_verification == VerificationLevel.WARNING:
            suggestions.append("辅助属性部分不匹配，建议确认目标元素")

        if not state.all_passed:
            failed_checks = [k for k, v in state.checks.items() if not v]
            if "visible" in failed_checks:
                suggestions.append("元素不可见，可能需要等待或滚动到元素位置")
            if "enabled" in failed_checks:
                suggestions.append("元素被禁用，检查是否需要先执行其他操作启用它")
            if "interactive" in failed_checks:
                suggestions.append("元素不可交互，可能需要选择其他目标元素")
            if "in_viewport" in failed_checks:
                suggestions.append("元素不在可视区域，建议先滚动页面")
            if "stable" in failed_checks:
                suggestions.append("元素位置不稳定，建议等待动画完成后重试")
            if "not_obscured" in failed_checks:
                suggestions.append("元素可能被遮挡，检查是否有弹窗或其他元素覆盖")

        return "; ".join(suggestions) if suggestions else ""

    def _record_verification(
        self,
        located: LocatedElement,
        level: VerificationLevel,
        confidence: float,
        state: ActionabilityReport,
    ) -> None:
        record = {
            "timestamp": time.time(),
            "level": level.value,
            "confidence": confidence,
            "strategy": located.strategy_used.value,
            "state_score": state.score,
            "state_passed": state.all_passed,
        }
        self._verification_history.append(record)
        if len(self._verification_history) > 200:
            self._verification_history = self._verification_history[-200:]

    def get_verification_stats(self) -> dict:
        if not self._verification_history:
            return {"total": 0, "pass_rate": 0.0, "avg_confidence": 0.0}
        total = len(self._verification_history)
        passed = sum(1 for r in self._verification_history if r["level"] == "passed")
        avg_conf = sum(r["confidence"] for r in self._verification_history) / total
        return {
            "total": total,
            "pass_rate": round(passed / total, 3),
            "avg_confidence": round(avg_conf, 3),
        }
