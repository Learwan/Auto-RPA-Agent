from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)

DEGRADATION_CHAIN = [
    "accessibility_id",
    "css_selector",
    "xpath",
    "image_match",
    "ocr_locate",
    "coordinate_click",
]


class PlatformType(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    WEB = "web"


class SelectorType(StrEnum):
    ACCESSIBILITY_ID = "accessibility_id"
    CSS_SELECTOR = "css_selector"
    XPATH = "xpath"
    IMAGE_MATCH = "image_match"
    OCR_LOCATE = "ocr_locate"
    COORDINATE_CLICK = "coordinate_click"
    SEMANTIC = "semantic"


class CapabilityStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass
class ProbeResult:
    selector_type: SelectorType
    status: CapabilityStatus
    latency_ms: float = 0.0
    reliability: float = 0.0
    notes: str = ""


@dataclass
class PlatformCapabilities:
    platform_type: PlatformType
    os_version: str = ""
    ui_framework: str = ""
    browser: str = ""
    dpi_scale: float = 1.0
    accessibility_level: str = "unknown"
    selector_results: dict[str, ProbeResult] = field(default_factory=dict)
    fingerprint: str = ""

    def is_supported(self, selector_type: SelectorType) -> bool:
        result = self.selector_results.get(selector_type.value)
        return result is not None and result.status in (CapabilityStatus.SUPPORTED, CapabilityStatus.PARTIAL)

    def get_reliability(self, selector_type: SelectorType) -> float:
        result = self.selector_results.get(selector_type.value)
        return result.reliability if result else 0.0

    def compute_fingerprint(self) -> str:
        raw = f"{self.platform_type}|{self.os_version}|{self.ui_framework}|{self.browser}|{self.dpi_scale}"
        self.fingerprint = hashlib.md5(raw.encode()).hexdigest()[:12]
        return self.fingerprint


@dataclass
class AdaptationRule:
    condition: str
    overrides: dict = field(default_factory=dict)
    disabled: list[str] = field(default_factory=list)
    priority: int = 0


@dataclass
class DegradationResult:
    selected_strategy: SelectorType
    confidence: float
    fallback_chain: list[SelectorType]
    adaptation_applied: str = ""


class CapabilityProber:
    def __init__(self, platform_adapter=None):
        self._adapter = platform_adapter
        self._probe_cache: dict[str, PlatformCapabilities] = {}

    async def probe(self) -> PlatformCapabilities:
        caps = PlatformCapabilities(platform_type=PlatformType.UNKNOWN)

        if self._adapter:
            try:
                info = await self._adapter.get_platform_info()
                caps.platform_type = PlatformType(info.get("os", "unknown"))
                caps.os_version = info.get("os_version", "")
                caps.ui_framework = info.get("ui_framework", "")
                caps.browser = info.get("browser", "")
                caps.dpi_scale = info.get("dpi_scale", 1.0)
                caps.accessibility_level = info.get("accessibility_level", "unknown")
            except Exception as e:
                logger.debug(f"Platform info probe failed: {e}")

        for st in SelectorType:
            result = await self._probe_selector(st)
            caps.selector_results[st.value] = result

        caps.compute_fingerprint()
        self._probe_cache[caps.fingerprint] = caps
        return caps

    async def _probe_selector(self, selector_type: SelectorType) -> ProbeResult:
        if not self._adapter:
            return ProbeResult(
                selector_type=selector_type,
                status=CapabilityStatus.UNKNOWN,
            )

        try:
            start = time.monotonic()
            supported = await self._adapter.check_selector_support(selector_type.value)
            latency = (time.monotonic() - start) * 1000

            return ProbeResult(
                selector_type=selector_type,
                status=CapabilityStatus.SUPPORTED if supported else CapabilityStatus.UNSUPPORTED,
                latency_ms=latency,
                reliability=0.8 if supported else 0.0,
            )
        except Exception:
            return ProbeResult(
                selector_type=selector_type,
                status=CapabilityStatus.UNSUPPORTED,
            )

    def get_cached(self, fingerprint: str) -> PlatformCapabilities | None:
        return self._probe_cache.get(fingerprint)


class AdaptiveDegradation:
    def __init__(self, custom_chain: list[str] | None = None):
        self._chain = custom_chain or DEGRADATION_CHAIN
        self._reliability_thresholds: dict[str, float] = {
            SelectorType.ACCESSIBILITY_ID.value: 0.9,
            SelectorType.CSS_SELECTOR.value: 0.85,
            SelectorType.XPATH.value: 0.8,
            SelectorType.IMAGE_MATCH.value: 0.7,
            SelectorType.OCR_LOCATE.value: 0.6,
            SelectorType.COORDINATE_CLICK.value: 0.4,
        }
        self._adaptation_rules: list[AdaptationRule] = []
        self._degradation_stats: dict[str, dict[str, int]] = {}

    def add_rule(self, rule: AdaptationRule) -> None:
        self._adaptation_rules.append(rule)
        self._adaptation_rules.sort(key=lambda r: r.priority, reverse=True)

    def select_strategy(self, capabilities: PlatformCapabilities) -> DegradationResult:
        adapted_chain = self._apply_rules(capabilities)

        best_strategy = SelectorType.COORDINATE_CLICK
        best_confidence = 0.0

        for strategy_name in adapted_chain:
            st = SelectorType(strategy_name)
            if capabilities.is_supported(st):
                reliability = capabilities.get_reliability(st)
                threshold = self._reliability_thresholds.get(strategy_name, 0.5)

                if reliability >= threshold:
                    best_strategy = st
                    best_confidence = reliability
                    break

                if reliability > best_confidence:
                    best_strategy = st
                    best_confidence = reliability

        self._record_degradation(best_strategy.value)

        return DegradationResult(
            selected_strategy=best_strategy,
            confidence=best_confidence,
            fallback_chain=[SelectorType(s) for s in adapted_chain],
        )

    def get_fallback_chain(self, current: SelectorType, capabilities: PlatformCapabilities) -> list[SelectorType]:
        chain = self._apply_rules(capabilities)
        current_idx = -1
        for i, s in enumerate(chain):
            if s == current.value:
                current_idx = i
                break

        if current_idx < 0:
            return [SelectorType(s) for s in chain]

        fallbacks = []
        for s in chain[current_idx + 1 :]:
            st = SelectorType(s)
            if capabilities.is_supported(st):
                fallbacks.append(st)

        return fallbacks

    def get_stats(self) -> dict:
        return {
            "degradation_counts": {
                strategy: stats.get("count", 0) for strategy, stats in self._degradation_stats.items()
            },
            "rules_count": len(self._adaptation_rules),
        }

    def _apply_rules(self, capabilities: PlatformCapabilities) -> list[str]:
        chain = list(self._chain)

        for rule in self._adaptation_rules:
            if self._evaluate_condition(rule.condition, capabilities):
                if "selector_strategy_priority" in rule.overrides:
                    chain = rule.overrides["selector_strategy_priority"]
                for disabled in rule.disabled:
                    if disabled in chain:
                        chain.remove(disabled)

        return chain

    def _evaluate_condition(self, condition: str, capabilities: PlatformCapabilities) -> bool:
        ctx = {
            "platform_type": capabilities.platform_type.value,
            "os_version": capabilities.os_version,
            "ui_framework": capabilities.ui_framework,
            "browser": capabilities.browser,
            "dpi_scale": str(capabilities.dpi_scale),
            "accessibility_level": capabilities.accessibility_level,
        }

        for key, value in ctx.items():
            if f"{key}==" in condition:
                expected = condition.split("==")[1].strip().strip("'\"")
                if str(value) != expected:
                    return False
            elif f"{key}!=" in condition:
                expected = condition.split("!=")[1].strip().strip("'\"")
                if str(value) == expected:
                    return False

        return True

    def _record_degradation(self, strategy: str) -> None:
        if strategy not in self._degradation_stats:
            self._degradation_stats[strategy] = {"count": 0}
        self._degradation_stats[strategy]["count"] += 1


class CrossPlatformAdapter:
    def __init__(self, platform_adapter=None):
        self._prober = CapabilityProber(platform_adapter)
        self._degradation = AdaptiveDegradation()
        self._capabilities: PlatformCapabilities | None = None
        self._initialized = False

    async def initialize(self) -> PlatformCapabilities:
        self._capabilities = await self._prober.probe()
        self._initialized = True
        return self._capabilities

    def get_capabilities(self) -> PlatformCapabilities | None:
        return self._capabilities

    def select_strategy(self) -> DegradationResult:
        if not self._capabilities:
            return DegradationResult(
                selected_strategy=SelectorType.COORDINATE_CLICK,
                confidence=0.0,
                fallback_chain=[SelectorType(s) for s in DEGRADATION_CHAIN],
            )
        return self._degradation.select_strategy(self._capabilities)

    def get_fallback(self, current: SelectorType) -> list[SelectorType]:
        if not self._capabilities:
            return [SelectorType(s) for s in DEGRADATION_CHAIN]
        return self._degradation.get_fallback_chain(current, self._capabilities)

    def add_adaptation_rule(self, condition: str, **overrides) -> None:
        rule = AdaptationRule(condition=condition, overrides=overrides)
        self._degradation.add_rule(rule)

    def get_stats(self) -> dict:
        return {
            "initialized": self._initialized,
            "platform": self._capabilities.platform_type.value if self._capabilities else "unknown",
            "fingerprint": self._capabilities.fingerprint if self._capabilities else "",
            "degradation": self._degradation.get_stats(),
        }
