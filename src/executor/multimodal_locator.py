from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field

from src.executor.element_locator import ElementLocator, LocatedElement
from src.models.automation import LocateStrategy, StepTarget
from src.models.desktop import Point, UIElement
from src.platform.base import BasePlatformAdapter

logger = logging.getLogger(__name__)

DEFAULT_SOURCE_WEIGHTS = {
    "dom": 0.40,
    "vision": 0.35,
    "ocr": 0.25,
}

SPATIAL_CLUSTER_THRESHOLD_PX = 30.0


@dataclass
class FusionCandidate:
    source: str
    element: UIElement
    position: Point
    confidence: float
    metadata: dict = field(default_factory=dict)


@dataclass
class OCRResult:
    text: str
    center_x: int
    center_y: int
    confidence: float
    bbox: list | None = None


class OCREngine:
    def __init__(self, backend: str = "auto"):
        self._backend = backend
        self._engine = None

    async def detect_text(self, screenshot_bytes: bytes) -> list[OCRResult]:
        if self._backend == "paddle" or (
            self._backend == "auto" and self._paddle_available()
        ):
            return self._detect_paddle(screenshot_bytes)
        if self._backend == "easyocr" or (
            self._backend == "auto" and self._easyocr_available()
        ):
            return self._detect_easyocr(screenshot_bytes)
        return []

    def _paddle_available(self) -> bool:
        try:
            from paddleocr import PaddleOCR  # noqa: F401

            return True
        except ImportError:
            return False

    def _easyocr_available(self) -> bool:
        try:
            import easyocr  # noqa: F401

            return True
        except ImportError:
            return False

    def _detect_paddle(self, screenshot_bytes: bytes) -> list[OCRResult]:
        try:
            import io

            import numpy as np
            from paddleocr import PaddleOCR
            from PIL import Image

            if self._engine is None:
                self._engine = PaddleOCR(
                    use_angle_cls=True, lang="ch", show_log=False
                )
            img = Image.open(io.BytesIO(screenshot_bytes))
            img_array = np.array(img)
            results = self._engine.ocr(img_array, cls=True)
            ocr_results = []
            if results and results[0]:
                for line in results[0]:
                    bbox = line[0]
                    text = line[1][0]
                    confidence = line[1][1]
                    center_x = int(sum(p[0] for p in bbox) / 4)
                    center_y = int(sum(p[1] for p in bbox) / 4)
                    ocr_results.append(
                        OCRResult(
                            text=text,
                            center_x=center_x,
                            center_y=center_y,
                            confidence=confidence,
                            bbox=bbox,
                        )
                    )
            return ocr_results
        except Exception as e:
            logger.debug(f"PaddleOCR detection failed: {e}")
            return []

    def _detect_easyocr(self, screenshot_bytes: bytes) -> list[OCRResult]:
        try:
            import io

            import easyocr
            import numpy as np
            from PIL import Image

            if self._engine is None:
                self._engine = easyocr.Reader(["ch_sim", "en"])
            img = Image.open(io.BytesIO(screenshot_bytes))
            img_array = np.array(img)
            results = self._engine.readtext(img_array)
            ocr_results = []
            for bbox, text, confidence in results:
                center_x = int(sum(p[0] for p in bbox) / 4)
                center_y = int(sum(p[1] for p in bbox) / 4)
                ocr_results.append(
                    OCRResult(
                        text=text,
                        center_x=center_x,
                        center_y=center_y,
                        confidence=confidence,
                        bbox=bbox,
                    )
                )
            return ocr_results
        except Exception as e:
            logger.debug(f"EasyOCR detection failed: {e}")
            return []


@dataclass
class ElementAnnotation:
    element_id: str
    semantic_label: str
    confidence: float
    source: str = "rule"
    action_intent: str = ""
    business_context: str = ""


ROLE_SEMANTICS = {
    "button": "可点击按钮",
    "textfield": "文本输入框",
    "checkbox": "复选框",
    "menuitem": "菜单项",
    "link": "导航链接",
    "tab": "标签页切换",
    "combobox": "下拉选择框",
    "radio": "单选按钮",
    "slider": "滑动条",
    "dialog": "对话框",
    "table": "数据表格",
    "cell": "表格单元格",
    "image": "图片",
    "heading": "标题",
}

TEXT_SEMANTICS = {
    "保存": "提交当前更改",
    "取消": "放弃当前操作",
    "删除": "移除选中项目",
    "搜索": "输入查询条件",
    "登录": "提交身份认证",
    "下载": "获取文件到本地",
    "上传": "提交本地文件",
    "提交": "确认并提交表单",
    "确认": "确认操作",
    "关闭": "关闭当前视图",
    "新建": "创建新项目",
    "编辑": "修改现有内容",
    "刷新": "重新加载数据",
    "导出": "导出数据到文件",
    "导入": "从文件导入数据",
    "复制": "复制选中内容",
    "粘贴": "粘贴内容",
    "剪切": "剪切选中内容",
    "全选": "选择所有项目",
    "撤销": "撤销上一步操作",
    "重做": "重做已撤销操作",
}


class SemanticElementAnnotator:
    def __init__(self, llm_service=None):
        self._llm = llm_service
        self._cache: dict[str, ElementAnnotation] = {}

    def annotate(self, element: UIElement) -> ElementAnnotation:
        cache_key = (
            f"{element.identifier or ''}:{element.role or ''}:{element.title or ''}"
        )
        if cache_key in self._cache:
            return self._cache[cache_key]

        annotation = self._rule_based_annotate(element)
        self._cache[cache_key] = annotation
        return annotation

    def _rule_based_annotate(self, element: UIElement) -> ElementAnnotation:
        role = (element.role or "").lower()
        title = (element.title or "").lower()

        semantic = ROLE_SEMANTICS.get(role, role or "未知元素")
        action_intent = ""
        for keyword, meaning in TEXT_SEMANTICS.items():
            if keyword in title:
                semantic = f"{meaning}({keyword})"
                action_intent = meaning
                break

        confidence = 0.7 if ROLE_SEMANTICS.get(role) else 0.4
        if action_intent:
            confidence = min(confidence + 0.15, 0.95)

        return ElementAnnotation(
            element_id=element.identifier or "",
            semantic_label=semantic,
            confidence=confidence,
            source="rule",
            action_intent=action_intent,
        )


class TriModalElementLocator:
    def __init__(
        self,
        platform_adapter: BasePlatformAdapter,
        grounding_engine=None,
        ocr_engine: OCREngine | None = None,
        weights: dict[str, float] | None = None,
    ):
        self._adapter = platform_adapter
        self._grounding = grounding_engine
        self._ocr = ocr_engine
        self._weights = weights or DEFAULT_SOURCE_WEIGHTS
        self._annotator = SemanticElementAnnotator()
        self._fusion_history: list[dict] = []

    async def locate_fusion(
        self, target: StepTarget
    ) -> LocatedElement | None:
        candidates = await self._parallel_locate(target)

        if not candidates:
            return None

        clusters = self._spatial_cluster(candidates)
        best_cluster = max(clusters, key=lambda c: self._cluster_score(c))
        best = max(
            best_cluster,
            key=lambda c: c.confidence * self._weights.get(c.source, 0.25),
        )
        fusion_confidence = self._compute_fusion_confidence(best_cluster)

        annotation = self._annotator.annotate(best.element)
        if annotation.confidence > 0.7:
            fusion_confidence = min(fusion_confidence + 0.05, 1.0)

        strategy = (
            LocateStrategy.IMAGE_MATCH
            if best.source == "vision"
            else LocateStrategy.TEXT_MATCH
        )

        self._record_fusion(target, best_cluster, fusion_confidence)

        return LocatedElement(
            element=best.element,
            strategy_used=strategy,
            position=best.position,
            confidence=fusion_confidence,
        )

    async def _parallel_locate(
        self, target: StepTarget
    ) -> list[FusionCandidate]:
        candidates: list[FusionCandidate] = []

        dom_task = self._locate_via_dom(target)
        vision_task = self._locate_via_vision(target)
        ocr_task = self._locate_via_ocr(target)

        results = await asyncio.gather(
            dom_task, vision_task, ocr_task, return_exceptions=True
        )

        for result in results:
            if isinstance(result, FusionCandidate):
                candidates.append(result)
            elif isinstance(result, list):
                candidates.extend(
                    r for r in result if isinstance(r, FusionCandidate)
                )

        return candidates

    async def _locate_via_dom(
        self, target: StepTarget
    ) -> FusionCandidate | None:
        try:
            locator = ElementLocator(self._adapter, enable_self_healing=False)
            located = await locator.locate(target)
            if located:
                return FusionCandidate(
                    source="dom",
                    element=located.element,
                    position=located.center or Point(x=0, y=0),
                    confidence=located.confidence,
                    metadata={"strategy": located.strategy_used.value},
                )
        except Exception as e:
            logger.debug(f"DOM location failed: {e}")
        return None

    async def _locate_via_vision(
        self, target: StepTarget
    ) -> FusionCandidate | None:
        if not self._grounding:
            return None
        try:
            import base64

            screenshot = await self._adapter.capture_screen()
            if not screenshot:
                return None
            screenshot_b64 = base64.b64encode(screenshot).decode("utf-8")
            action_desc = self._build_action_description(target)
            result = await self._grounding.ground_action(
                screenshot_base64=screenshot_b64,
                action_description=action_desc,
                action_type="click",
            )
            if result and result.found:
                cx = 0
                cy = 0
                if hasattr(result, "center_x") and result.center_x:
                    cx = int(result.center_x)
                if hasattr(result, "center_y") and result.center_y:
                    cy = int(result.center_y)
                return FusionCandidate(
                    source="vision",
                    element=UIElement(
                        role=result.element_type or "grounding_match",
                        title=result.element_label or "",
                        bounds=None,
                    ),
                    position=Point(x=cx, y=cy),
                    confidence=result.confidence,
                    metadata={"reasoning": getattr(result, "reasoning", "")},
                )
        except Exception as e:
            logger.debug(f"Vision location failed: {e}")
        return None

    async def _locate_via_ocr(
        self, target: StepTarget
    ) -> list[FusionCandidate]:
        search_text = target.title or target.text_contains
        if not search_text or not self._ocr:
            return []
        try:
            screenshot = await self._adapter.capture_screen()
            if not screenshot:
                return []
            ocr_results = await self._ocr.detect_text(screenshot)
            candidates = []
            for ocr_item in ocr_results:
                if search_text.lower() in ocr_item.text.lower():
                    candidates.append(
                        FusionCandidate(
                            source="ocr",
                            element=UIElement(
                                role="text",
                                title=ocr_item.text,
                                bounds=None,
                            ),
                            position=Point(
                                x=ocr_item.center_x, y=ocr_item.center_y
                            ),
                            confidence=ocr_item.confidence * 0.85,
                            metadata={"ocr_text": ocr_item.text},
                        )
                    )
            return candidates
        except Exception as e:
            logger.debug(f"OCR location failed: {e}")
            return []

    def _spatial_cluster(
        self,
        candidates: list[FusionCandidate],
        threshold: float = SPATIAL_CLUSTER_THRESHOLD_PX,
    ) -> list[list[FusionCandidate]]:
        if not candidates:
            return []
        clusters: list[list[FusionCandidate]] = []
        assigned: set[int] = set()

        for i, c1 in enumerate(candidates):
            if i in assigned:
                continue
            cluster = [c1]
            assigned.add(i)
            for j, c2 in enumerate(candidates):
                if j in assigned:
                    continue
                dist = math.sqrt(
                    (c1.position.x - c2.position.x) ** 2
                    + (c1.position.y - c2.position.y) ** 2
                )
                if dist <= threshold:
                    cluster.append(c2)
                    assigned.add(j)
            clusters.append(cluster)

        return clusters

    def _cluster_score(self, cluster: list[FusionCandidate]) -> float:
        sources = set(c.source for c in cluster)
        source_bonus = len(sources) * 0.1
        weighted_sum = sum(
            c.confidence * self._weights.get(c.source, 0.25) for c in cluster
        )
        return weighted_sum + source_bonus

    def _compute_fusion_confidence(
        self, cluster: list[FusionCandidate]
    ) -> float:
        sources = set(c.source for c in cluster)
        base_conf = max(c.confidence for c in cluster)
        consistency_bonus = min(len(sources) * 0.1, 0.2)
        return min(base_conf + consistency_bonus, 1.0)

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
        return "点击".join(parts) if parts else "目标元素"

    def _record_fusion(
        self,
        target: StepTarget,
        cluster: list[FusionCandidate],
        confidence: float,
    ) -> None:
        record = {
            "sources": [c.source for c in cluster],
            "confidence": confidence,
            "cluster_size": len(cluster),
        }
        self._fusion_history.append(record)
        if len(self._fusion_history) > 200:
            self._fusion_history = self._fusion_history[-200:]

    def get_fusion_stats(self) -> dict:
        if not self._fusion_history:
            return {"total": 0}
        total = len(self._fusion_history)
        multi_source = sum(
            1 for r in self._fusion_history if len(set(r["sources"])) > 1
        )
        avg_conf = sum(r["confidence"] for r in self._fusion_history) / total
        return {
            "total": total,
            "multi_source_rate": round(multi_source / total, 3),
            "avg_confidence": round(avg_conf, 3),
        }
