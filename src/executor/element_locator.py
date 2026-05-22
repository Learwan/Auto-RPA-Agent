from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Any

from src.models.automation import LocateStrategy, StepTarget
from src.models.desktop import ElementCriteria, Point, UIElement
from src.platform.base import BasePlatformAdapter

logger = logging.getLogger(__name__)

IMAGE_MATCH_CONFIDENCE = 0.8
MULTI_SCALE_FACTORS = [1.0, 0.9, 1.1, 0.8, 1.2]

STRATEGY_CONFIDENCE = {
    LocateStrategy.ACCESSIBILITY_ID: 0.95,
    LocateStrategy.CSS_SELECTOR: 0.90,
    LocateStrategy.XPATH: 0.85,
    LocateStrategy.TEXT_MATCH: 0.75,
    LocateStrategy.IMAGE_MATCH: 0.70,
    LocateStrategy.IMAGE_ANCHOR: 0.72,
    LocateStrategy.POSITION: 0.40,
}

HEALING_STRATEGY_CHAIN = [
    LocateStrategy.ACCESSIBILITY_ID,
    LocateStrategy.CSS_SELECTOR,
    LocateStrategy.XPATH,
    LocateStrategy.TEXT_MATCH,
    LocateStrategy.IMAGE_MATCH,
    LocateStrategy.IMAGE_ANCHOR,
    LocateStrategy.POSITION,
]

# Self-healing must not, by default, degrade to a naked pixel coordinate.
# That is the exact "fragile node" behaviour the closed-loop contract
# forbids.  Use ``AUTO_AGENT_ALLOW_COORDINATE_FALLBACK=1`` to bring it back
# when truly needed.
SAFE_HEALING_STRATEGY_CHAIN = [
    s for s in HEALING_STRATEGY_CHAIN if s != LocateStrategy.POSITION
]


def _coord_fallback_allowed() -> bool:
    return os.environ.get("AUTO_AGENT_ALLOW_COORDINATE_FALLBACK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


class StrategyConfidence:
    def __init__(self):
        self._history: dict[str, list[bool]] = {}

    def record(self, strategy: LocateStrategy, success: bool) -> None:
        key = strategy.value
        if key not in self._history:
            self._history[key] = []
        self._history[key].append(success)
        if len(self._history[key]) > 100:
            self._history[key] = self._history[key][-100:]

    def get_confidence(self, strategy: LocateStrategy) -> float:
        base = STRATEGY_CONFIDENCE.get(strategy, 0.5)
        key = strategy.value
        if key not in self._history or len(self._history[key]) < 3:
            return base
        recent = self._history[key][-20:]
        success_rate = sum(1 for s in recent if s) / len(recent)
        return base * 0.4 + success_rate * 0.6

    def get_ranked_strategies(self) -> list[tuple[LocateStrategy, float]]:
        all_strategies = list(STRATEGY_CONFIDENCE.keys())
        ranked = [(s, self.get_confidence(s)) for s in all_strategies]
        return sorted(ranked, key=lambda x: x[1], reverse=True)


class AdaptivePoller:
    def __init__(
        self,
        base_interval: float = 0.1,
        max_interval: float = 1.0,
        jitter_ratio: float = 0.2,
    ):
        self._base = base_interval
        self._max = max_interval
        self._jitter = jitter_ratio

    async def poll(
        self,
        check_fn,
        timeout_ms: int = 10000,
        stability_count: int = 1,
    ) -> bool:
        deadline = time.monotonic() + timeout_ms / 1000.0
        interval = self._base
        consecutive_success = 0

        while time.monotonic() < deadline:
            try:
                result = await check_fn()
                if result:
                    consecutive_success += 1
                    if consecutive_success >= stability_count:
                        return True
                else:
                    consecutive_success = 0
            except Exception:
                consecutive_success = 0

            jitter = random.uniform(-self._jitter, self._jitter) * interval
            await asyncio.sleep(max(0.01, interval + jitter))
            interval = min(interval * 1.5, self._max)

        return False


class LocatedElement:
    def __init__(
        self,
        element: UIElement,
        strategy_used: LocateStrategy,
        position: Point | None = None,
        confidence: float = 0.0,
        provider: str | None = None,
        provider_chain: list[str] | None = None,
        fallback_chain: list[str] | None = None,
        attempted_providers: list[str] | None = None,
        healed: bool = False,
    ):
        self.element = element
        self.strategy_used = strategy_used
        self.position = position or (
            element.bounds
            and Point(
                x=element.bounds.x + element.bounds.width // 2,
                y=element.bounds.y + element.bounds.height // 2,
            )
        )
        self.confidence = confidence or STRATEGY_CONFIDENCE.get(strategy_used, 0.5)
        self.provider = provider or self._default_provider(strategy_used)
        self.provider_chain = list(provider_chain or [])
        self.fallback_chain = list(fallback_chain or [])
        attempted = attempted_providers or ([self.provider] if self.provider else [])
        self.attempted_providers = list(dict.fromkeys(p for p in attempted if p))
        self.healed = healed

    @property
    def center(self) -> Point | None:
        return self.position

    @staticmethod
    def _default_provider(strategy_used: LocateStrategy) -> str:
        if strategy_used in {
            LocateStrategy.ACCESSIBILITY_ID,
            LocateStrategy.TEXT_MATCH,
            LocateStrategy.CSS_SELECTOR,
            LocateStrategy.XPATH,
        }:
            return "native_locator"
        if strategy_used == LocateStrategy.IMAGE_MATCH:
            return "template_match"
        if strategy_used == LocateStrategy.IMAGE_ANCHOR:
            return "image_anchor"
        if strategy_used == LocateStrategy.POSITION:
            return "coordinates"
        return strategy_used.value

    def telemetry(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "provider": self.provider,
            "strategy": self.strategy_used.value,
            "confidence": round(float(self.confidence or 0.0), 4),
            "healed": self.healed,
        }
        if self.center is not None:
            payload["center"] = {
                "x": self.center.x,
                "y": self.center.y,
            }
        if self.provider_chain:
            payload["provider_chain"] = list(self.provider_chain)
        if self.fallback_chain:
            payload["fallback_chain"] = list(self.fallback_chain)
        if self.attempted_providers:
            payload["attempted_providers"] = list(self.attempted_providers)
        if self.element.role:
            payload["element_role"] = self.element.role
        if self.element.title:
            payload["element_title"] = self.element.title
        return payload


class ElementLocator:
    def __init__(
        self,
        platform_adapter: BasePlatformAdapter,
        enable_self_healing: bool = True,
        grounding_engine=None,
    ):
        self._adapter = platform_adapter
        self._enable_self_healing = enable_self_healing
        self._grounding_engine = grounding_engine
        self._strategy_confidence = StrategyConfidence()
        self._adaptive_poller = AdaptivePoller()
        self._location_history: list[dict] = []

    def _grounding_runtime_status(self) -> dict[str, Any]:
        if self._grounding_engine is None or not hasattr(self._grounding_engine, "get_runtime_status"):
            return {}
        try:
            status = self._grounding_engine.get_runtime_status()
        except Exception:
            return {}
        return status if isinstance(status, dict) else {}

    async def locate(self, target: StepTarget) -> LocatedElement | None:
        strategies = self._get_strategy_order(target)

        for strategy in strategies:
            result = await self._try_strategy(strategy, target)
            if result:
                self._strategy_confidence.record(strategy, True)
                self._record_location(target, strategy, result, success=True)
                logger.debug(f"Located element using {strategy.value} (confidence={result.confidence:.2f})")
                return result

        if self._enable_self_healing:
            healed = await self._self_healing_locate(target)
            if healed:
                self._record_location(target, healed.strategy_used, healed, success=True, healed=True)
                return healed

        self._strategy_confidence.record(strategies[0] if strategies else LocateStrategy.POSITION, False)
        self._record_location(target, strategies[0] if strategies else LocateStrategy.POSITION, None, success=False)
        logger.warning(f"Failed to locate element with any strategy for target: {target}")
        return None

    async def locate_with_candidates(self, target: StepTarget, top_k: int = 3) -> list[LocatedElement]:
        strategies = self._get_strategy_order(target)
        candidates: list[LocatedElement] = []

        for strategy in strategies:
            result = await self._try_strategy(strategy, target)
            if result:
                candidates.append(result)
                if len(candidates) >= top_k:
                    break

        candidates.sort(key=lambda c: c.confidence, reverse=True)
        return candidates

    async def verify_element(self, target: StepTarget, timeout_ms: int = 5000) -> bool:
        result = await self.wait_for_element(target, timeout_ms)
        return result is not None

    async def wait_for_element(self, target: StepTarget, timeout_ms: int = 10000) -> LocatedElement | None:
        result = await self._adaptive_poller.poll(
            check_fn=lambda: self.locate(target),
            timeout_ms=timeout_ms,
            stability_count=1,
        )
        if result:
            return await self.locate(target)
        return None

    async def wait_for_actionability(
        self,
        target: StepTarget,
        timeout_ms: int = 10000,
    ) -> LocatedElement | None:
        from src.executor.step_verifier import ElementStateChecker

        state_checker = ElementStateChecker(self._adapter)

        async def check():
            located = await self.locate(target)
            if located is None:
                return False
            report = await state_checker.check_actionability(located, target)
            return report.all_passed

        found = await self._adaptive_poller.poll(check, timeout_ms=timeout_ms, stability_count=2)
        if found:
            return await self.locate(target)
        return None

    async def _self_healing_locate(self, target: StepTarget) -> LocatedElement | None:
        if self._grounding_engine is not None:
            from src.executor.vision_locator import VisionLocator

            vision = VisionLocator(self._adapter, self._grounding_engine)
            vision_result = await vision.locate(target)
            if vision_result:
                vision_result.healed = True
                logger.info("VisionLocator healed location for target")
                return vision_result

            healed = await self._visual_grounding_heal(target)
            if healed:
                healed.healed = True
                return healed

        healing_chain = (
            HEALING_STRATEGY_CHAIN
            if _coord_fallback_allowed()
            else SAFE_HEALING_STRATEGY_CHAIN
        )
        for strategy in healing_chain:
            result = await self._try_strategy(strategy, target)
            if result:
                result.confidence *= 0.8
                result.healed = True
                logger.info(f"Self-healing located element via {strategy.value} (confidence={result.confidence:.2f})")
                self._strategy_confidence.record(strategy, True)
                return result

        return None

    async def _visual_grounding_heal(self, target: StepTarget) -> LocatedElement | None:
        try:
            screenshot_bytes = await self._adapter.capture_screen()
            if not screenshot_bytes:
                return None

            import base64

            screenshot_b64 = base64.b64encode(screenshot_bytes).decode("utf-8")
            action_desc = self._build_action_description(target)

            result = await self._grounding_engine.ground_action(
                screenshot_base64=screenshot_b64,
                action_description=action_desc,
                action_type="click",
            )

            if result and result.found and result.confidence >= 0.5:
                bbox = getattr(result, "bbox_pixel", None)
                center_x = int(bbox[0]) if bbox and len(bbox) >= 2 else 0
                center_y = int(bbox[1]) if bbox and len(bbox) >= 2 else 0

                if center_x > 0 and center_y > 0:
                    runtime_status = self._grounding_runtime_status()
                    element = UIElement(
                        role="grounding_match",
                        title=f"Visual grounding: {action_desc[:50]}",
                        bounds=None,
                    )
                    healed = LocatedElement(
                        element=element,
                        strategy_used=LocateStrategy.IMAGE_MATCH,
                        position=Point(x=center_x, y=center_y),
                        confidence=result.confidence * 0.85,
                        provider=getattr(result, "provider", None),
                        provider_chain=getattr(result, "provider_chain", None) or runtime_status.get("provider_chain"),
                        fallback_chain=getattr(result, "fallback_chain", None) or runtime_status.get("fallback_chain"),
                        attempted_providers=getattr(result, "attempted_providers", None),
                        healed=True,
                    )
                    logger.info(
                        f"Visual grounding healed location at ({center_x}, {center_y}) "
                        f"confidence={healed.confidence:.2f}"
                    )
                    return healed
        except Exception as e:
            logger.debug(f"Visual grounding heal failed: {e}")
        return None

    def _build_action_description(self, target: StepTarget) -> str:
        parts = []
        if target.title:
            parts.append(f"标题为'{target.title}'")
        if target.role:
            parts.append(f"角色为'{target.role}'")
        if target.accessibility_id:
            parts.append(f"ID为'{target.accessibility_id}'")
        if target.selector:
            parts.append(f"选择器'{target.selector}'")
        if target.text_contains:
            parts.append(f"包含文本'{target.text_contains}'")
        if target.position:
            parts.append(f"位置({target.position.x},{target.position.y})附近")
        return "点击".join(parts) if parts else "目标元素"

    def _get_strategy_order(self, target: StepTarget) -> list[LocateStrategy]:
        strategies: list[LocateStrategy] = []
        if target.strategy == LocateStrategy.ACCESSIBILITY_ID and target.accessibility_id:
            strategies.append(LocateStrategy.ACCESSIBILITY_ID)
        if target.strategy == LocateStrategy.TEXT_MATCH and (target.title or target.text_contains):
            strategies.append(LocateStrategy.TEXT_MATCH)
        if target.strategy == LocateStrategy.CSS_SELECTOR and target.selector:
            strategies.append(LocateStrategy.CSS_SELECTOR)
        if target.strategy == LocateStrategy.XPATH and target.xpath:
            strategies.append(LocateStrategy.XPATH)
        if target.strategy == LocateStrategy.IMAGE_MATCH and target.image_path:
            strategies.append(LocateStrategy.IMAGE_MATCH)
        if target.strategy == LocateStrategy.IMAGE_ANCHOR and target.image_path:
            strategies.append(LocateStrategy.IMAGE_ANCHOR)
        if target.strategy == LocateStrategy.POSITION and target.position:
            strategies.append(LocateStrategy.POSITION)

        if not strategies:
            if target.accessibility_id:
                strategies.append(LocateStrategy.ACCESSIBILITY_ID)
            if target.title or target.text_contains:
                strategies.append(LocateStrategy.TEXT_MATCH)
            if target.selector:
                strategies.append(LocateStrategy.CSS_SELECTOR)
            if target.xpath:
                strategies.append(LocateStrategy.XPATH)
            if target.image_path:
                strategies.append(LocateStrategy.IMAGE_MATCH)
            if target.position:
                strategies.append(LocateStrategy.POSITION)

        # Closed-loop guarantee: do NOT silently add raw POSITION as a
        # fallback after a stable strategy was explicitly chosen.  The
        # operator must either pick POSITION on purpose or opt in via
        # ``AUTO_AGENT_ALLOW_COORDINATE_FALLBACK=1``.
        explicit_position = target.strategy == LocateStrategy.POSITION
        healing_chain = (
            HEALING_STRATEGY_CHAIN
            if (_coord_fallback_allowed() or explicit_position)
            else SAFE_HEALING_STRATEGY_CHAIN
        )
        fallback_strategies = [s for s in healing_chain if s not in strategies]

        strategies.extend(fallback_strategies)
        return strategies

    async def _try_strategy(self, strategy: LocateStrategy, target: StepTarget) -> LocatedElement | None:
        try:
            if strategy == LocateStrategy.ACCESSIBILITY_ID:
                return await self._locate_by_accessibility(target)
            elif strategy == LocateStrategy.TEXT_MATCH:
                return await self._locate_by_text(target)
            elif strategy == LocateStrategy.CSS_SELECTOR:
                return await self._locate_by_css_selector(target)
            elif strategy == LocateStrategy.XPATH:
                return await self._locate_by_xpath(target)
            elif strategy == LocateStrategy.IMAGE_MATCH:
                return await self._locate_by_image(target)
            elif strategy == LocateStrategy.IMAGE_ANCHOR:
                return await self._locate_by_image_anchor(target)
            elif strategy == LocateStrategy.POSITION:
                return await self._locate_by_position(target)
        except Exception as e:
            logger.debug(f"Strategy {strategy.value} failed: {e}")
        return None

    async def _locate_by_accessibility(self, target: StepTarget) -> LocatedElement | None:
        if not target.accessibility_id:
            return None
        criteria = ElementCriteria(
            accessibility_id=target.accessibility_id,
            role=target.role,
            class_name=target.class_name,
        )
        element = await self._adapter.find_element(criteria)
        if element:
            confidence = self._strategy_confidence.get_confidence(LocateStrategy.ACCESSIBILITY_ID)
            return LocatedElement(element, LocateStrategy.ACCESSIBILITY_ID, confidence=confidence)
        return None

    async def _locate_by_text(self, target: StepTarget) -> LocatedElement | None:
        criteria = ElementCriteria(
            title=target.title,
            text_contains=target.text_contains,
            role=target.role,
            class_name=target.class_name,
            url=target.url,
            frame=target.frame,
        )
        element = await self._adapter.find_element(criteria)
        if element:
            confidence = self._strategy_confidence.get_confidence(LocateStrategy.TEXT_MATCH)
            if target.title and target.text_contains:
                confidence *= 1.05
            return LocatedElement(element, LocateStrategy.TEXT_MATCH, confidence=confidence)
        return None

    async def _locate_by_css_selector(self, target: StepTarget) -> LocatedElement | None:
        if not target.selector:
            return None
        criteria = ElementCriteria(
            selector=target.selector,
            url=target.url,
            frame=target.frame,
            title=target.title,
            text_contains=target.text_contains,
        )
        element = await self._adapter.find_element(criteria)
        if element:
            confidence = self._strategy_confidence.get_confidence(LocateStrategy.CSS_SELECTOR)
            return LocatedElement(element, LocateStrategy.CSS_SELECTOR, confidence=confidence)
        return None

    async def _locate_by_xpath(self, target: StepTarget) -> LocatedElement | None:
        if not target.xpath:
            return None
        criteria = ElementCriteria(
            xpath=target.xpath,
            url=target.url,
            frame=target.frame,
            title=target.title,
            text_contains=target.text_contains,
        )
        element = await self._adapter.find_element(criteria)
        if element:
            confidence = self._strategy_confidence.get_confidence(LocateStrategy.XPATH)
            return LocatedElement(element, LocateStrategy.XPATH, confidence=confidence)
        return None

    async def _locate_by_image(self, target: StepTarget) -> LocatedElement | None:
        if not target.image_path:
            return None
        try:
            import numpy as np
            from PIL import Image

            screenshot_bytes = await self._adapter.capture_screen()
            screen_w, screen_h = await self._adapter.get_screen_size()
            screenshot = Image.frombytes("RGB", (screen_w, screen_h), screenshot_bytes)

            template = Image.open(target.image_path).convert("RGB")

            screenshot_arr = np.array(screenshot)
            template_arr = np.array(template)

            best_result = None
            best_confidence = 0.0

            for scale in MULTI_SCALE_FACTORS:
                if scale == 1.0:
                    scaled_template = template_arr
                else:
                    new_w = int(template_arr.shape[1] * scale)
                    new_h = int(template_arr.shape[0] * scale)
                    if new_w < 8 or new_h < 8:
                        continue
                    if new_w > screenshot_arr.shape[1] or new_h > screenshot_arr.shape[0]:
                        continue
                    scaled_pil = template.resize((new_w, new_h), Image.LANCZOS)
                    scaled_template = np.array(scaled_pil)

                if (
                    scaled_template.shape[0] > screenshot_arr.shape[0]
                    or scaled_template.shape[1] > screenshot_arr.shape[1]
                ):
                    continue

                result = self._template_match(screenshot_arr, scaled_template)
                if result and result[2] > best_confidence:
                    best_confidence = result[2]
                    cx, cy = result[0], result[1]
                    best_result = (cx, cy, best_confidence, scale)

            if best_result and best_result[2] >= IMAGE_MATCH_CONFIDENCE:
                center_x, center_y = best_result[0], best_result[1]
                element = UIElement(
                    role="image_match",
                    title=f"Image match: {target.image_path}",
                    bounds=None,
                )
                confidence = best_result[2] * self._strategy_confidence.get_confidence(LocateStrategy.IMAGE_MATCH)
                return LocatedElement(
                    element,
                    LocateStrategy.IMAGE_MATCH,
                    position=Point(x=center_x, y=center_y),
                    confidence=confidence,
                )
        except ImportError:
            logger.debug("PIL/numpy not available for image matching")
        except Exception as e:
            logger.debug(f"Image matching failed: {e}")
        return None

    async def _locate_by_image_anchor(self, target: StepTarget) -> LocatedElement | None:
        if not target.image_path:
            return None
        try:
            from src.recorder.image_anchor import ImageAnchorCapture

            screenshot_bytes = await self._adapter.capture_screen()
            if not screenshot_bytes:
                return None

            match = await ImageAnchorCapture.match_anchor(
                screenshot_bytes, target.image_path, threshold=IMAGE_MATCH_CONFIDENCE
            )
            if match:
                center_x, center_y = match
                element = UIElement(
                    role="image_anchor",
                    title=f"Image anchor: {target.image_path}",
                    bounds=None,
                )
                confidence = self._strategy_confidence.get_confidence(LocateStrategy.IMAGE_ANCHOR)
                return LocatedElement(
                    element,
                    LocateStrategy.IMAGE_ANCHOR,
                    position=Point(x=center_x, y=center_y),
                    confidence=confidence,
                )
        except ImportError:
            logger.debug("PIL/numpy not available for image anchor matching")
        except Exception as e:
            logger.debug(f"Image anchor matching failed: {e}")
        return None

    async def _locate_by_position(self, target: StepTarget) -> LocatedElement | None:
        if not target.position:
            return None
        element = UIElement(
            role="position_target",
            title=f"Position target ({target.position.x}, {target.position.y})",
            bounds=None,
        )
        confidence = self._strategy_confidence.get_confidence(LocateStrategy.POSITION)
        return LocatedElement(element, LocateStrategy.POSITION, position=target.position, confidence=confidence)

    @staticmethod
    def _template_match(source: numpy.ndarray, template: numpy.ndarray) -> tuple[int, int, float] | None:  # noqa: F821
        try:
            import cv2

            result = cv2.matchTemplate(source, template, cv2.TM_CCOEFF_NORMED)
            min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
            if max_val > 0.6:
                h, w = template.shape[:2]
                center_x = max_loc[0] + w // 2
                center_y = max_loc[1] + h // 2
                return center_x, center_y, float(max_val)
        except ImportError:
            return ElementLocator._template_match_numpy(source, template)
        return None

    @staticmethod
    def _template_match_numpy(source: numpy.ndarray, template: numpy.ndarray) -> tuple[int, int, float] | None:  # noqa: F821
        import numpy as np

        s_h, s_w = source.shape[:2]
        t_h, t_w = template.shape[:2]
        if t_h > s_h or t_w > s_w:
            return None

        step = max(1, min(t_h, t_w) // 8)
        best_score = -1.0
        best_x, best_y = 0, 0

        src_gray = np.mean(source.astype(np.float32), axis=2)
        tpl_gray = np.mean(template.astype(np.float32), axis=2)
        tpl_norm = tpl_gray - np.mean(tpl_gray)
        tpl_std = np.std(tpl_gray)
        if tpl_std < 1e-6:
            return None
        tpl_norm /= tpl_std

        for y in range(0, s_h - t_h + 1, step):
            for x in range(0, s_w - t_w + 1, step):
                patch = src_gray[y : y + t_h, x : x + t_w]
                patch_mean = np.mean(patch)
                patch_std = np.std(patch)
                if patch_std < 1e-6:
                    continue
                patch_norm = (patch - patch_mean) / patch_std
                score = float(np.sum(patch_norm * tpl_norm) / (t_h * t_w))
                if score > best_score:
                    best_score = score
                    best_x, best_y = x, y

        if best_score > 0.5:
            center_x = best_x + t_w // 2
            center_y = best_y + t_h // 2
            return center_x, center_y, best_score
        return None

    def _record_location(
        self,
        target: StepTarget,
        strategy: LocateStrategy,
        result: LocatedElement | None,
        success: bool,
        healed: bool = False,
    ) -> None:
        record = {
            "timestamp": time.time(),
            "strategy": strategy.value,
            "success": success,
            "healed": healed,
            "confidence": result.confidence if result else 0.0,
        }
        self._location_history.append(record)
        if len(self._location_history) > 200:
            self._location_history = self._location_history[-200:]

    def get_location_stats(self) -> dict:
        if not self._location_history:
            return {"total": 0, "success_rate": 0.0, "heal_rate": 0.0}
        total = len(self._location_history)
        successes = sum(1 for r in self._location_history if r["success"])
        heals = sum(1 for r in self._location_history if r.get("healed", False))
        avg_conf = sum(r["confidence"] for r in self._location_history if r["success"]) / max(successes, 1)
        strategy_stats: dict[str, dict] = {}
        for r in self._location_history:
            s = r["strategy"]
            if s not in strategy_stats:
                strategy_stats[s] = {"attempts": 0, "successes": 0}
            strategy_stats[s]["attempts"] += 1
            if r["success"]:
                strategy_stats[s]["successes"] += 1
        return {
            "total": total,
            "success_rate": round(successes / total, 3),
            "heal_rate": round(heals / max(successes, 1), 3),
            "avg_confidence": round(avg_conf, 3),
            "strategy_breakdown": strategy_stats,
        }
