from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field

from src.config import settings
from src.llm.local_engine import LocalLLMEngine
from src.llm.service import LLMService
from src.models.operation import OperationEvent, OperationType
from src.recorder.event_bus import EventBus

logger = logging.getLogger(__name__)

ANALYSIS_DEBOUNCE_MS = 500
MAX_BUFFER_OPERATIONS = 200
MIN_OPS_FOR_ANALYSIS = 5


@dataclass
class AISuggestion:
    category: str
    message: str
    severity: str = "info"
    timestamp: float = field(default_factory=time.time)


@dataclass
class FlowNarrative:
    summary: str
    steps: list[str]
    risks: list[str]
    improvements: list[str]
    confidence: float
    generated_at: float


class RecordingLLMAnalyzer:
    def __init__(self):
        self._engine: LocalLLMEngine | None = None
        self._cloud_service: LLMService | None = None
        self._event_bus = EventBus.get_instance()
        self._operation_buffer: list[OperationEvent] = []
        self._suggestions: list[AISuggestion] = []
        self._flow_narratives: list[FlowNarrative] = []
        self._last_analysis_time: float = 0.0
        self._session_id: str | None = None
        self._running = False
        self._task: asyncio.Task | None = None
        self._analysis_count = 0
        self._last_screenshot: str | None = None

    def feed_screenshot(self, base64_data: str) -> None:
        self._last_screenshot = base64_data

    async def start(self, session_id: str) -> None:
        self._session_id = session_id
        self._running = True
        self._operation_buffer.clear()
        self._suggestions.clear()
        self._flow_narratives.clear()
        self._last_analysis_time = 0.0
        self._analysis_count = 0

        if settings.LOCAL_LLM_ENABLED:
            self._engine = await LocalLLMEngine.get_instance()
        self._cloud_service = LLMService()

        queue = self._event_bus.subscribe("operation_event")
        self._task = asyncio.create_task(self._process_events(queue))
        logger.info(f"Recording LLM analyzer started for session {session_id}")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        if self._operation_buffer:
            await self._run_final_analysis()

        logger.info(
            f"Recording LLM analyzer stopped for session {self._session_id}, generated {self._analysis_count} analyses"
        )

    async def _process_events(self, queue: asyncio.Queue) -> None:
        while self._running:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=5.0)
            except TimeoutError:
                continue

            if not self._running:
                break

            op_event = self._extract_operation(event)
            if op_event is None:
                continue

            self._operation_buffer.append(op_event)

            if len(self._operation_buffer) > MAX_BUFFER_OPERATIONS:
                self._operation_buffer = self._operation_buffer[-MAX_BUFFER_OPERATIONS:]

            now = time.monotonic()
            if now - self._last_analysis_time < ANALYSIS_DEBOUNCE_MS / 1000.0:
                continue

            if len(self._operation_buffer) >= MIN_OPS_FOR_ANALYSIS:
                await self._run_quick_analysis()
                self._last_analysis_time = now

    def _extract_operation(self, event) -> OperationEvent | None:
        payload = event.payload
        if payload.get("type") != "operation_event":
            return None
        raw_op = payload.get("event")
        if not raw_op:
            return None
        try:
            return OperationEvent.model_validate(raw_op)
        except Exception as e:
            logger.debug(f"Failed to parse operation event: {e}")
            return None

    async def _run_quick_analysis(self) -> None:
        recent = self._operation_buffer[-30:]
        summary = self._summarize_operations(recent)

        if self._engine and settings.LOCAL_LLM_ENABLED:
            await self._analyze_with_local_llm(recent, summary)
        elif self._cloud_service.is_configured:
            await self._analyze_with_cloud_llm(summary)

    async def _run_final_analysis(self) -> None:
        all_ops = self._operation_buffer
        if len(all_ops) < 5:
            return

        summary = self._summarize_operations(all_ops)

        try:
            if self._engine and settings.LOCAL_LLM_ENABLED:
                await self._generate_flow_narrative(summary)
        except Exception as e:
            logger.error(f"Final analysis failed: {e}")

    async def _analyze_with_local_llm(self, operations: list[OperationEvent], summary: str) -> None:
        try:
            if self._last_screenshot and self._engine:
                result = await self._engine.analyze_with_image(
                    prompt=f"最近的操作摘要:\n{summary}",
                    image_base64=self._last_screenshot,
                )
            else:
                result = await self._engine.generate(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是一个实时桌面操作分析助手，正在监控用户的录制操作。"
                                "分析最近的操作序列，识别以下内容：\n"
                                "1. 用户当前正在执行什么任务\n"
                                "2. 操作流程是否高效\n"
                                "3. 有什么可以改进的地方\n"
                                "4. 是否有潜在的错误风险\n"
                                "请用简洁的中文回答，限制在150字以内。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"最近的操作摘要:\n{summary}",
                        },
                    ],
                    max_tokens=300,
                )

            suggestion = AISuggestion(
                category="real_time_analysis",
                message=result.text.strip(),
                severity="info",
            )

            self._suggestions.append(suggestion)
            self._analysis_count += 1

            await self._event_bus.publish(
                "ai_suggestion",
                {
                    "type": "ai_suggestion",
                    "session_id": self._session_id,
                    "category": suggestion.category,
                    "message": suggestion.message,
                    "severity": suggestion.severity,
                    "timestamp": suggestion.timestamp,
                    "tokens_per_second": result.tokens_per_second,
                    "elapsed_ms": result.elapsed_ms,
                },
            )

        except Exception as e:
            logger.debug(f"Local LLM quick analysis failed: {e}")

    async def _analyze_with_cloud_llm(self, summary: str) -> None:
        try:
            result = await self._cloud_service.analyze_flow(
                flow_description=summary,
                operations_summary=f"共 {len(self._operation_buffer)} 个操作",
            )

            suggestion = AISuggestion(
                category="cloud_analysis",
                message=result,
                severity="info",
            )

            self._suggestions.append(suggestion)
            self._analysis_count += 1

            await self._event_bus.publish(
                "ai_suggestion",
                {
                    "type": "ai_suggestion",
                    "session_id": self._session_id,
                    "category": "cloud_analysis",
                    "message": result,
                    "severity": "info",
                    "timestamp": suggestion.timestamp,
                },
            )

        except Exception as e:
            logger.debug(f"Cloud LLM analysis failed: {e}")

    async def _generate_flow_narrative(self, summary: str) -> None:
        try:
            if self._engine:
                result = await self._engine.generate(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是一个自动化流程分析专家。分析以下录制操作，生成一个结构化的JSON报告。\n"
                                "格式如下：\n"
                                '{"summary": "流程概述(中文)", "steps": ["步骤1", "步骤2", ...], '
                                '"risks": ["风险1", "风险2", ...], '
                                '"improvements": ["改进建议1", "改进建议2", ...], '
                                '"is_automatable": true/false, "automation_difficulty": "easy/medium/hard"}\n'
                                "只输出 JSON，不要其他内容。"
                            ),
                        },
                        {"role": "user", "content": summary},
                    ],
                    max_tokens=1024,
                )

                try:
                    parsed = json.loads(result.text.strip())
                except json.JSONDecodeError:
                    text = result.text.strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1]
                        if text.endswith("```"):
                            text = text[:-3]
                    parsed = json.loads(text)

                narrative = FlowNarrative(
                    summary=parsed.get("summary", "无法识别流程"),
                    steps=parsed.get("steps", []),
                    risks=parsed.get("risks", []),
                    improvements=parsed.get("improvements", []),
                    confidence=min(0.95, max(0.3, len(self._operation_buffer) / 100)),
                    generated_at=time.time(),
                )
                self._flow_narratives.append(narrative)

                await self._event_bus.publish(
                    "flow_narrative",
                    {
                        "type": "flow_narrative",
                        "session_id": self._session_id,
                        "summary": narrative.summary,
                        "steps": narrative.steps,
                        "risks": narrative.risks,
                        "improvements": narrative.improvements,
                        "confidence": narrative.confidence,
                    },
                )

        except Exception as e:
            logger.error(f"Flow narrative generation failed: {e}")

    def _summarize_operations(self, operations: list[OperationEvent]) -> str:
        lines: list[str] = []
        lines.append(f"共 {len(operations)} 个操作:\n")

        type_counts: dict[str, int] = {}
        for op in operations:
            type_counts[op.type.value] = type_counts.get(op.type.value, 0) + 1

        lines.append("操作类型统计:")
        for op_type, count in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  - {op_type}: {count}次")

        lines.append("\n最近操作序列:")
        recent = operations[-15:]
        for i, op in enumerate(recent):
            op_desc = self._describe_operation(op)
            lines.append(f"  {i + 1}. [{op.type.value}] {op_desc}")

        return "\n".join(lines)

    def _describe_operation(self, op: OperationEvent) -> str:
        data = op.data
        try:
            if op.type == OperationType.MOUSE_CLICK:
                return f"鼠标点击 ({data.x}, {data.y}) 按钮:{data.button.value}"
            elif op.type == OperationType.MOUSE_SCROLL:
                return f"鼠标滚动 ({data.x},{data.y}) Δ=({data.scroll_dx},{data.scroll_dy})"
            elif op.type == OperationType.KEY_PRESS:
                text = getattr(data, "text", "") or ""
                key = data.key
                return f"按键 '{key}' {text[:30]}"
            elif op.type == OperationType.KEY_INPUT:
                text = getattr(data, "text", "") or ""
                return f"输入 '{text[:50]}'"
            elif op.type == OperationType.WINDOW_SWITCH:
                to_win = data.to_window if hasattr(data, "to_window") else None
                title = to_win.title if to_win and to_win.title else "未知"
                return f"窗口切换 → '{title[:60]}'"
            elif op.type == OperationType.NAVIGATION:
                url = data.url if hasattr(data, "url") else ""
                return f"导航 → {url[:80]}"
            else:
                return f"{op.type.value}"
        except Exception:
            return f"{op.type.value}"

    def get_suggestions(self) -> list[dict]:
        return [
            {
                "category": s.category,
                "message": s.message,
                "severity": s.severity,
                "timestamp": s.timestamp,
            }
            for s in self._suggestions[-20:]
        ]

    def get_narratives(self) -> list[dict]:
        return [
            {
                "summary": n.summary,
                "steps": n.steps,
                "risks": n.risks,
                "improvements": n.improvements,
                "confidence": n.confidence,
            }
            for n in self._flow_narratives
        ]
