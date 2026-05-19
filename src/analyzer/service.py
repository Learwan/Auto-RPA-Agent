import asyncio
import json
import logging
import time

from src.analyzer.checkpoint_support import backfill_flow_checkpoints
from src.analyzer.closure_assessor import FlowClosureAssessor
from src.analyzer.confidence_scorer import ConfidenceScorer, ScoredFlow
from src.analyzer.intent_recognizer import BusinessSemanticExtractor
from src.analyzer.knowledge_graph import (
    OperationPattern,
)
from src.analyzer.operation_repair import OperationRepairService
from src.analyzer.pattern_detector import PatternDetector
from src.analyzer.preprocessor import OperationPreprocessor
from src.analyzer.script_generator import ScriptGenerator
from src.analyzer.semantic_segmenter import SemanticSegmenter
from src.db.database import get_session_factory
from src.db.repository import Repository
from src.models.automation import AutomationFlow, AutomationStep, DetectedPattern, StepCondition
from src.models.operation import OperationEvent, OperationType

logger = logging.getLogger(__name__)

MAX_OPERATIONS_FOR_ANALYSIS = 10000
MAX_AI_ENHANCED_FLOWS = 2
AI_ENHANCEMENT_MAX_ATTEMPTS = 2
AI_ENHANCEMENT_RETRY_DELAY_SECONDS = 0.5


class AnalysisService:
    def __init__(
        self,
        repository: Repository | None = None,
        llm_service=None,
        knowledge_graph=None,
        operation_repair_service: OperationRepairService | None = None,
    ):
        self._repo = repository
        self._llm = llm_service
        self._knowledge_graph = knowledge_graph
        self._preprocessor = OperationPreprocessor()
        self._detector = PatternDetector()
        self._generator = ScriptGenerator()
        self._scorer = ConfidenceScorer()
        self._segmenter = SemanticSegmenter()
        self._semantic_extractor = BusinessSemanticExtractor()
        self._closure_assessor = FlowClosureAssessor()
        self._operation_repair = operation_repair_service

    async def _with_repo(self, operation):
        if self._repo is not None:
            return await operation(self._repo)
        factory = get_session_factory()
        async with factory() as session:
            repo = Repository(session)
            return await operation(repo)

    async def analyze_session(self, session_id: str, min_confidence: float = 0.5) -> list[ScoredFlow]:
        operations = await self._load_operations(session_id)
        if not operations:
            logger.warning(f"No operations found for session {session_id}")
            return []

        if len(operations) > MAX_OPERATIONS_FOR_ANALYSIS:
            logger.warning(
                f"Session {session_id} has {len(operations)} operations, truncating to {MAX_OPERATIONS_FOR_ANALYSIS}"
            )
            operations = operations[:MAX_OPERATIONS_FOR_ANALYSIS]

        operations = await self._repair_operations_for_analysis(operations)
        normalized = self._preprocessor.preprocess(operations)
        if not normalized:
            logger.warning(f"No normalized operations for session {session_id}")
            return []

        segments = self._segmenter.segment(normalized)
        logger.info(f"Semantic segmentation: {len(segments)} segments from {len(normalized)} operations")

        patterns = self._detector.detect_patterns(normalized)

        scored_flows: list[ScoredFlow] = []

        if patterns:
            flows = self._generator.generate(patterns, normalized)
            for flow, pattern in zip(flows, patterns, strict=False):
                flow.source_session_id = session_id
                scored = self._scorer.score_flow(flow, normalized, pattern)
                if scored.overall_confidence >= min_confidence:
                    scored_flows.append(scored)

        pattern_covers_all = False
        if scored_flows:
            max_steps = max(len(sf.flow.steps) for sf in scored_flows)
            click_ops = sum(
                1 for n in normalized if n.op_type in ("mouse_click", "mouse_double_click", "mouse_right_click")
            )
            if max_steps >= click_ops and max_steps >= len(normalized) * 0.5:
                pattern_covers_all = True

        if not pattern_covers_all and len(normalized) >= 2:
            direct_flow = self._generator.generate_direct(normalized)
            if direct_flow:
                direct_flow.source_session_id = session_id
                seq_types = [self._op_type_to_step_type(n.op_type) for n in normalized]
                fallback_pattern = DetectedPattern(
                    id="direct",
                    pattern_sequence=seq_types,
                    instances=[{"start_idx": 0, "end_idx": len(normalized) - 1}],
                    support=1,
                    confidence=0.5,
                )
                scored = self._scorer.score_flow(direct_flow, normalized, fallback_pattern)
                if scored.overall_confidence >= min_confidence:
                    scored_flows.append(scored)
        scored_flows = self._deduplicate_scored_flows(scored_flows)
        for scored in scored_flows:
            self._attach_semantics_and_checkpoints(scored.flow, normalized)
        scored_flows.sort(key=lambda sf: sf.overall_confidence, reverse=True)
        enhancement_targets = scored_flows[:MAX_AI_ENHANCED_FLOWS]
        for scored in scored_flows[MAX_AI_ENHANCED_FLOWS:]:
            self._set_default_ai_enhancement_status(scored.flow)
        if enhancement_targets:
            await asyncio.gather(
                *(self._enhance_with_ai(scored, normalized, session_id) for scored in enhancement_targets),
                return_exceptions=True,
            )
        for scored in scored_flows:
            self._attach_closure_assessment(scored.flow)
        await self._sync_session_flows(session_id, scored_flows)

        scored_flows.sort(key=lambda sf: sf.overall_confidence, reverse=True)
        logger.info(
            f"Analysis complete for session {session_id}: {len(scored_flows)} flows (min_confidence={min_confidence})"
        )
        return scored_flows

    async def get_flow_detail(self, flow_id: str) -> ScoredFlow | None:
        flow = await self._with_repo(lambda r: r.get_automation_flow(flow_id))
        if not flow:
            return None

        normalized = []
        if flow.source_session_id:
            operations = await self._load_operations(flow.source_session_id)
            operations = await self._repair_operations_for_analysis(operations)
            normalized = self._preprocessor.preprocess(operations)

        pattern = (
            DetectedPattern(
                id="reanalysis",
                pattern_sequence=[s.type for s in flow.steps],
                instances=flow.source_pattern.get("instances", []) if flow.source_pattern else [],
                support=1,
                confidence=flow.confidence,
            )
            if flow.steps
            else DetectedPattern(id="empty", pattern_sequence=[], support=0)
        )

        return self._scorer.score_flow(flow, normalized, pattern)

    async def reanalyze_flow(self, flow_id: str) -> ScoredFlow | None:
        flow = await self._with_repo(lambda r: r.get_automation_flow(flow_id))
        if not flow:
            return None
        if not flow.source_session_id:
            return await self.get_flow_detail(flow_id)

        operations = await self._load_operations(flow.source_session_id)
        operations = await self._repair_operations_for_analysis(operations)
        normalized = self._preprocessor.preprocess(operations)
        patterns = self._detector.detect_patterns(normalized)

        best_pattern = None
        for pattern in patterns:
            if pattern.pattern_sequence == [s.type for s in flow.steps]:
                best_pattern = pattern
                break

        if not best_pattern:
            best_pattern = DetectedPattern(
                id="reanalysis",
                pattern_sequence=[s.type for s in flow.steps],
                instances=flow.source_pattern.get("instances", []) if flow.source_pattern else [],
                support=1,
                confidence=flow.confidence,
            )

        scored = self._scorer.score_flow(flow, normalized, best_pattern)
        flow.confidence = scored.overall_confidence
        await self._save_flow(flow)
        return scored

    async def _load_operations(self, session_id: str) -> list[OperationEvent]:
        return await self._with_repo(lambda r: r.get_operations_by_session(session_id))

    async def _repair_operations_for_analysis(self, operations: list[OperationEvent]) -> list[OperationEvent]:
        if not self._operations_need_repair(operations):
            return operations
        if self._operation_repair is None:
            self._operation_repair = OperationRepairService()
        return await self._operation_repair.repair_operations(operations)

    @staticmethod
    def _operations_need_repair(operations: list[OperationEvent]) -> bool:
        for operation in operations:
            if not isinstance(operation, OperationEvent):
                continue
            if operation.type != OperationType.MOUSE_CLICK:
                continue
            return True
        return False

    async def _enhance_with_ai(
        self, scored: ScoredFlow, normalized: list, session_id: str
    ) -> ScoredFlow:
        flow = scored.flow
        status_payload = {
            "enabled": bool(self._llm and getattr(self._llm, 'is_configured', False)),
            "attempts": 0,
            "status": "not_started",
            "reason": None,
            "last_error": None,
        }

        if not status_payload["enabled"]:
            status_payload["status"] = "skipped_no_llm"
            self._set_ai_enhancement_status(flow, status_payload)
            return scored

        if not flow.steps:
            status_payload["status"] = "skipped_empty_flow"
            self._set_ai_enhancement_status(flow, status_payload)
            return scored

        semantic_context = (flow.metadata or {}).get("semantic_intent")
        op_summary = self._build_operations_summary(normalized, semantic_context)
        flow_desc = self._build_flow_description(flow)

        kg_task = asyncio.create_task(self._safe_kg_add(flow, normalized))

        analysis_result = None
        for attempt in range(1, AI_ENHANCEMENT_MAX_ATTEMPTS + 1):
            status_payload["attempts"] = attempt
            try:
                analysis_result = await self._safe_ai_artifacts(flow_desc, op_summary, flow)
                if isinstance(analysis_result, dict):
                    status_payload["status"] = "success"
                    status_payload["reason"] = None
                    status_payload["last_error"] = None
                    break
                status_payload["status"] = "invalid_response"
                status_payload["reason"] = "empty_artifacts"
                status_payload["last_error"] = "LLM returned no structured artifacts"
            except TimeoutError as e:
                status_payload["status"] = "timeout"
                status_payload["reason"] = "llm_timeout"
                status_payload["last_error"] = str(e)
            except ValueError as e:
                status_payload["status"] = "parse_failed"
                status_payload["reason"] = "invalid_json"
                status_payload["last_error"] = str(e)
            except Exception as e:
                status_payload["status"] = "request_failed"
                status_payload["reason"] = "llm_request_error"
                status_payload["last_error"] = str(e)

            if attempt < AI_ENHANCEMENT_MAX_ATTEMPTS:
                await asyncio.sleep(AI_ENHANCEMENT_RETRY_DELAY_SECONDS)

        await self._try_suggest_flow_name(flow, flow_desc)

        if isinstance(analysis_result, dict):
            self._apply_ai_artifacts(flow, scored, analysis_result)
            logger.info(f"AI analyzed flow {flow.id}: {len(json.dumps(analysis_result, ensure_ascii=False))} chars")

        self._set_ai_enhancement_status(flow, status_payload)

        kg_result = await kg_task

        if kg_result and not isinstance(kg_result, Exception):
            logger.debug(f"Knowledge graph updated for flow {flow.id}")

        return scored

    async def _try_suggest_flow_name(self, flow: AutomationFlow, flow_desc: str) -> None:
        if not self._llm or not getattr(self._llm, "is_configured", False):
            return
        if not hasattr(self._llm, "suggest_flow_name"):
            return
        try:
            suggested = await asyncio.wait_for(
                self._llm.suggest_flow_name(flow_desc),
                timeout=30.0,
            )
            suggested = str(suggested or "").strip()
            if suggested:
                flow.name = suggested[:100]
                logger.info(f"LLM suggested name for flow {flow.id}: {flow.name}")
        except Exception as e:
            logger.debug(f"Flow name suggestion skipped: {e}")

    async def _safe_ai_artifacts(self, flow_desc: str, op_summary: str, flow: AutomationFlow) -> dict | None:
        if not self._llm or not getattr(self._llm, 'is_configured', False):
            return None
        return await asyncio.wait_for(
            self._llm.analyze_flow_artifacts(
                flow_desc,
                op_summary,
                [self._step_prompt_payload(step, index) for index, step in enumerate(flow.steps)],
            ),
            timeout=240.0,
        )

    async def _safe_kg_add(self, flow: AutomationFlow, normalized: list) -> None:
        if not self._knowledge_graph:
            return
        try:
            op_sequence = [step.type for step in flow.steps]
            app_name = ""
            for op in normalized:
                ctx = getattr(op, 'context', {}) or {}
                aw = ctx.get("active_window", {}) or {}
                if isinstance(aw, dict):
                    app_name = aw.get("app_name", "")
                elif hasattr(aw, 'app_name'):
                    app_name = aw.app_name
                if app_name:
                    break

            total_duration = 0.0
            for op in normalized:
                total_duration += getattr(op, 'duration_ms', 0) or 0
            avg_duration = total_duration / max(len(normalized), 1)

            pattern = OperationPattern(
                pattern_id=flow.id,
                op_sequence=op_sequence,
                app_name=app_name,
                window_context=flow.window_context or "",
                avg_duration_ms=avg_duration,
                success_rate=0.8,
                occurrence_count=1,
            )
            self._knowledge_graph.add_operation_pattern(pattern)
        except Exception as e:
            logger.debug(f"Knowledge graph update skipped: {e}")

    def _apply_ai_enhancements(self, analysis_text: str, scored: ScoredFlow) -> None:
        risk_keywords = ["risk", "failure", "dangerous", "unstable", "风险", "失败", "不稳定"]
        confidence_boost = 0.0
        for kw in risk_keywords:
            if kw.lower() in analysis_text.lower():
                confidence_boost = max(confidence_boost, -0.1)

        quality_keywords = ["well-structured", "reliable", "robust", "结构良好", "可靠", "稳健"]
        for kw in quality_keywords:
            if kw.lower() in analysis_text.lower():
                confidence_boost = max(confidence_boost, 0.05)

        if confidence_boost != 0.0:
            old_conf = scored.overall_confidence
            scored.overall_confidence = min(1.0, max(0.0, old_conf + confidence_boost))

    def _apply_ai_artifacts(
        self,
        flow: AutomationFlow,
        scored: ScoredFlow,
        artifacts: dict,
    ) -> None:
        suggested_name = str(artifacts.get("suggested_name") or "").strip()
        summary = str(artifacts.get("summary") or "").strip()
        risks = self._normalize_text_list(artifacts.get("risks"))
        improvements = self._normalize_text_list(artifacts.get("improvements"))
        reliability = str(artifacts.get("reliability_assessment") or "").strip()
        ai_confidence = self._normalize_confidence(artifacts.get("confidence"))

        if suggested_name:
            flow.name = suggested_name[:100]
            logger.info(f"AI generated name for flow {flow.id}: {flow.name}")

        ai_payload = {
            "participated": True,
            "model": getattr(self._llm, "_model", None),
            "generated_at": time.time(),
            "generated_name": flow.name if suggested_name else None,
            "summary": summary,
            "risks": risks,
            "improvements": improvements,
            "reliability_assessment": reliability,
            "confidence": ai_confidence,
            "step_analyses": [],
        }

        step_analyses_by_index: dict[int, dict] = {}
        for item in artifacts.get("step_analyses") or []:
            if not isinstance(item, dict):
                continue
            try:
                step_index = int(item.get("step_index"))
            except (TypeError, ValueError):
                continue

            if step_index < 0 or step_index >= len(flow.steps):
                continue

            step_payload = {
                "intent": str(item.get("intent") or "").strip(),
                "ui_state": str(item.get("ui_state") or "").strip(),
                "risk": str(item.get("risk") or "").strip(),
                "suggested_next_actions": self._normalize_text_list(item.get("suggested_next_actions")),
                "refined_description": str(item.get("refined_description") or "").strip(),
                "recommended_wait_ms": self._normalize_wait_ms(item.get("recommended_wait_ms")),
                "verification_condition": self._normalize_condition_name(item.get("verification_condition")),
                "verification_timeout_ms": self._normalize_timeout_ms(item.get("verification_timeout_ms")),
                "target_text_hint": str(item.get("target_text_hint") or "").strip(),
                "window_title_hint": str(item.get("window_title_hint") or "").strip(),
                "confidence": self._normalize_confidence(item.get("confidence")),
                "source": "llm",
            }
            step_analyses_by_index[step_index] = step_payload
            ai_payload["step_analyses"].append({
                "step_index": step_index,
                **step_payload,
            })

        flow.metadata = {
            **(flow.metadata or {}),
            "ai_analysis": ai_payload,
        }

        for index, step in enumerate(flow.steps):
            step_ai = step_analyses_by_index.get(index)
            if step_ai is None:
                continue
            step.metadata = {
                **(step.metadata or {}),
                "ai_analysis": step_ai,
            }
            self._apply_ai_step_refinement(step, step_ai)

        if summary:
            flow.description = (flow.description or "").strip()

        heuristic_text = "\n".join([summary, reliability, *risks, *improvements]).strip()
        if heuristic_text:
            self._apply_ai_enhancements(heuristic_text, scored)

        if ai_confidence is not None:
            scored.overall_confidence = min(
                1.0,
                max(0.0, scored.overall_confidence * 0.7 + ai_confidence * 0.3),
            )
        flow.confidence = scored.overall_confidence

    @staticmethod
    def _normalize_text_list(value) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    @staticmethod
    def _normalize_confidence(value) -> float | None:
        if value is None or value == "":
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return min(1.0, max(0.0, numeric))

    @staticmethod
    def _step_prompt_payload(step: AutomationStep, step_index: int) -> dict[str, object]:
        target = step.target
        metadata = step.metadata or {}
        return {
            "step_index": step_index,
            "id": step.id,
            "type": str(step.type),
            "description": step.description or "",
            "strategy": target.strategy.value if target else "unknown",
            "window_title": (target.window_title or target.title) if target else None,
            "text_hint": (target.text_contains or target.title) if target else None,
            "url": target.url if target else None,
            "semantic_annotation": metadata.get("semantic_annotation"),
            "checkpoints": metadata.get("checkpoints"),
        }

    @staticmethod
    def _normalize_wait_ms(value) -> int | None:
        if value in (None, ""):
            return None
        try:
            numeric = int(float(value))
        except (TypeError, ValueError):
            return None
        return min(10000, max(0, numeric))

    @staticmethod
    def _normalize_timeout_ms(value) -> int | None:
        if value in (None, ""):
            return None
        try:
            numeric = int(float(value))
        except (TypeError, ValueError):
            return None
        return min(30000, max(500, numeric))

    @staticmethod
    def _normalize_condition_name(value) -> str | None:
        text = str(value or "").strip().lower()
        if text in {"element_exists", "window_exists", "page_stable"}:
            return text
        return None

    def _apply_ai_step_refinement(self, step: AutomationStep, step_ai: dict) -> None:
        refined_description = str(step_ai.get("refined_description") or "").strip()
        if refined_description:
            step.description = refined_description[:200]

        recommended_wait_ms = step_ai.get("recommended_wait_ms")
        if isinstance(recommended_wait_ms, int) and recommended_wait_ms > step.delay:
            step.delay = recommended_wait_ms

        verification_condition = step_ai.get("verification_condition")
        verification_timeout_ms = step_ai.get("verification_timeout_ms") or 5000
        if verification_condition and not step.locator_requires_confirmation():
            condition = StepCondition(
                field=verification_condition,
                operator="truthy",
                value=True,
                timeout_ms=verification_timeout_ms,
            )
            signature = (condition.field, condition.operator, condition.value)
            existing_signatures = {(item.field, item.operator, item.value) for item in step.preconditions}
            if signature not in existing_signatures:
                step.preconditions.append(condition)

        target_text_hint = str(step_ai.get("target_text_hint") or "").strip()
        if target_text_hint:
            if not step.target.text_contains:
                step.target.text_contains = target_text_hint[:120]
            if not step.target.title:
                step.target.title = target_text_hint[:120]

        window_title_hint = str(step_ai.get("window_title_hint") or "").strip()
        if window_title_hint and not step.target.window_title:
            step.target.window_title = window_title_hint[:160]

    def _set_default_ai_enhancement_status(self, flow: AutomationFlow) -> None:
        if (flow.metadata or {}).get("ai_enhancement_status"):
            return

        llm_enabled = bool(self._llm and getattr(self._llm, "is_configured", False))
        status_payload = {
            "enabled": llm_enabled,
            "attempts": 0,
            "status": "skipped_rank_limit" if llm_enabled and flow.steps else "skipped_no_llm",
            "reason": "rank_limit" if llm_enabled and flow.steps else "llm_not_configured",
            "last_error": None,
        }

        if llm_enabled and not flow.steps:
            status_payload["status"] = "skipped_empty_flow"
            status_payload["reason"] = "no_steps"

        self._set_ai_enhancement_status(flow, status_payload)

    @staticmethod
    def _set_ai_enhancement_status(flow: AutomationFlow, status_payload: dict) -> None:
        flow.metadata = {
            **(flow.metadata or {}),
            "ai_enhancement_status": status_payload,
        }

    @staticmethod
    def _build_operations_summary(normalized: list, semantic_context: dict | None = None) -> str:
        lines: list[str] = []

        if isinstance(semantic_context, dict):
            intent = semantic_context.get("intent") or {}
            overall_intent = str(intent.get("overall") or "").strip()
            business_context = str(intent.get("business_context") or "").strip()
            variables = semantic_context.get("variables") or []

            if overall_intent or business_context or variables:
                lines.append("Semantic Context:")
                if overall_intent:
                    lines.append(f"  overall_intent: {overall_intent}")
                if business_context:
                    lines.append(f"  business_context: {business_context}")
                if variables:
                    variable_names = [
                        str(item.get("name"))
                        for item in variables
                        if isinstance(item, dict) and item.get("name")
                    ]
                    if variable_names:
                        lines.append(f"  variables: {', '.join(variable_names[:8])}")

        lines.append("Operations:")
        for i, op in enumerate(normalized[:50]):
            op_type = getattr(op, 'op_type', 'unknown')
            lines.append(f"  {i}: {op_type}")
        if len(normalized) > 50:
            lines.append(f"  ... ({len(normalized) - 50} more operations)")
        return "\n".join(lines)

    def _attach_semantics_and_checkpoints(self, flow: AutomationFlow, normalized: list) -> None:
        flow_operations, step_to_local_index = self._collect_flow_operations(flow, normalized)
        semantic_context = self._extract_semantic_context(flow_operations)

        annotations = {
            int(item.get("step_index")): item
            for item in semantic_context.get("annotations", [])
            if isinstance(item, dict) and isinstance(item.get("step_index"), int)
        }
        for step in flow.ordered_steps():
            local_index = step_to_local_index.get(step.id)
            if local_index is None:
                continue
            annotation = annotations.get(local_index)
            if not annotation:
                continue
            step.metadata = {
                **(step.metadata or {}),
                "semantic_annotation": annotation,
            }

        flow.metadata = {
            **(flow.metadata or {}),
            "semantic_intent": semantic_context,
        }
        backfill_flow_checkpoints(flow, semantic_context)

    def _attach_closure_assessment(self, flow: AutomationFlow) -> None:
        assessment = self._closure_assessor.assess(flow)
        flow.metadata = {
            **(flow.metadata or {}),
            "closed_loop_assessment": assessment.model_dump(mode="json"),
            "closed_loop_ready": assessment.ready,
        }
        if flow.status in {"", "draft", "ready", "needs_review", "incomplete"}:
            flow.status = "ready" if assessment.ready else assessment.status

    def _collect_flow_operations(self, flow: AutomationFlow, normalized: list) -> tuple[list, dict[str, int]]:
        if not normalized:
            return [], {}

        ordered_steps = flow.ordered_steps()
        selected_operations: list = []
        seq_to_local_index: dict[int, int] = {}
        step_to_local_index: dict[str, int] = {}

        for step in ordered_steps:
            source_seq_num = (step.metadata or {}).get("source_seq_num")
            if not isinstance(source_seq_num, int):
                continue
            if source_seq_num < 0 or source_seq_num >= len(normalized):
                continue

            local_index = seq_to_local_index.get(source_seq_num)
            if local_index is None:
                local_index = len(selected_operations)
                seq_to_local_index[source_seq_num] = local_index
                selected_operations.append(normalized[source_seq_num])

            step_to_local_index[step.id] = local_index

        if not selected_operations:
            fallback_count = min(len(normalized), max(1, len(ordered_steps)))
            selected_operations = normalized[:fallback_count]
            for index, step in enumerate(ordered_steps[:fallback_count]):
                step_to_local_index[step.id] = index
            for step in ordered_steps[fallback_count:]:
                if selected_operations:
                    step_to_local_index[step.id] = len(selected_operations) - 1
            return selected_operations, step_to_local_index

        last_index = len(selected_operations) - 1
        if last_index >= 0:
            for step in ordered_steps:
                if step.id not in step_to_local_index:
                    step_to_local_index[step.id] = last_index

        return selected_operations, step_to_local_index

    def _extract_semantic_context(self, operations: list) -> dict:
        default_payload = {
            "intent": {
                "overall": "",
                "sub_goals": [],
                "business_context": "unknown",
                "confidence": 0.0,
            },
            "data_flows": [],
            "variables": [],
            "annotations": [],
        }

        if not operations:
            return default_payload

        try:
            extracted = self._semantic_extractor.extract(operations)
            if isinstance(extracted, dict):
                return {
                    "intent": extracted.get("intent") or default_payload["intent"],
                    "data_flows": extracted.get("data_flows") or [],
                    "variables": extracted.get("variables") or [],
                    "annotations": extracted.get("annotations") or [],
                }
        except Exception as e:
            logger.debug(f"Semantic extraction skipped: {e}")

        return default_payload

    @staticmethod
    def _build_flow_description(flow: AutomationFlow) -> str:
        lines = [f"Flow: {flow.name or 'Unnamed'}"]
        lines.append(f"Steps: {len(flow.steps)}")
        for step in flow.steps:
            lines.append(
                f"  [{step.type}] {step.description or 'No description'} "
                f"(strategy={step.target.strategy.value if step.target else 'unknown'})"
            )
        return "\n".join(lines)

    @staticmethod
    def _op_type_to_step_type(op_type: str) -> str:
        mapping = {
            "mouse_click": "click",
            "mouse_double_click": "click",
            "mouse_right_click": "click",
            "mouse_scroll": "scroll",
            "mouse_drag": "drag",
            "type_text": "type",
            "hotkey": "hotkey",
            "switch_window": "switch_window",
            "navigation": "navigate",
            "upload_file": "upload_file",
            "download_file": "download_file",
            "file_op": "file_op",
            "clipboard": "type",
        }
        return mapping.get(op_type, "click")

    async def _save_flow(self, flow: AutomationFlow) -> None:
        await self._with_repo(lambda r: r.save_automation_flow(flow))

    async def _sync_session_flows(self, session_id: str, scored_flows: list[ScoredFlow]) -> None:
        if not scored_flows:
            return

        existing_flows = await self._with_repo(lambda r: r.list_automation_flows_by_session(session_id))
        existing_by_signature: dict[str, AutomationFlow] = {}
        duplicate_existing_ids: list[str] = []
        current_signatures: set[str] = set()

        for existing_flow in existing_flows:
            signature = self._flow_signature(existing_flow)
            if signature in existing_by_signature:
                duplicate_existing_ids.append(existing_flow.id)
                continue
            existing_by_signature[signature] = existing_flow

        for scored in scored_flows:
            signature = self._flow_signature(scored.flow)
            current_signatures.add(signature)
            existing_flow = existing_by_signature.get(signature)
            if existing_flow is None:
                await self._save_flow(scored.flow)
                continue

            scored.flow.id = existing_flow.id
            scored.flow.execution_count = existing_flow.execution_count
            scored.flow.success_count = existing_flow.success_count
            scored.flow.status = existing_flow.status
            await self._save_flow(scored.flow)

        stale_existing_ids = [
            flow.id for signature, flow in existing_by_signature.items() if signature not in current_signatures
        ]
        obsolete_ids = duplicate_existing_ids + stale_existing_ids
        if obsolete_ids:
            await self._with_repo(lambda r: r.delete_automation_flows(obsolete_ids))

    def _deduplicate_scored_flows(self, scored_flows: list[ScoredFlow]) -> list[ScoredFlow]:
        by_signature: dict[str, ScoredFlow] = {}
        for scored in scored_flows:
            signature = self._flow_signature(scored.flow)
            existing = by_signature.get(signature)
            if existing is None or scored.overall_confidence > existing.overall_confidence:
                by_signature[signature] = scored
        return list(by_signature.values())

    def _flow_signature(self, flow: AutomationFlow) -> str:
        ordered_steps = flow.ordered_steps()
        step_index = {step.id: index for index, step in enumerate(ordered_steps)}
        entry_step_ids = sorted(step_index[step_id] for step_id in flow.entry_step_ids if step_id in step_index)
        if not entry_step_ids and ordered_steps:
            inferred_entry_ids = [index for index, step in enumerate(ordered_steps) if not step.depends_on]
            entry_step_ids = inferred_entry_ids or [0]

        payload = {
            "entry_step_ids": entry_step_ids,
            "steps": [self._step_signature(step, step_index) for step in ordered_steps],
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)

    def _step_signature(self, step, step_index: dict[str, int]) -> dict:
        return {
            "type": step.type.value,
            "action": step.action,
            "target": step.target.model_dump(mode="json", exclude_none=True),
            "atomic": step.atomic,
            "depends_on": sorted(step_index[step_id] for step_id in step.depends_on if step_id in step_index),
            "preconditions": [condition.model_dump(mode="json", exclude_none=True) for condition in step.preconditions],
            "branches": [
                {
                    "target": step_index[branch.target_step_id],
                    "outcome": branch.outcome,
                    "condition": branch.condition.model_dump(mode="json", exclude_none=True)
                    if branch.condition
                    else None,
                    "description": branch.description,
                    "metadata": branch.metadata,
                }
                for branch in sorted(
                    step.branches,
                    key=lambda branch: (step_index.get(branch.target_step_id, -1), branch.outcome),
                )
                if branch.target_step_id in step_index
            ],
            "execution_order": step.execution_order,
            "metadata": step.metadata,
            "delay": step.delay,
            "condition": step.condition.model_dump(mode="json", exclude_none=True) if step.condition else None,
            "on_error": step.on_error.value,
            "retry_count": step.retry_count,
            "description": step.description,
            "verification_enabled": step.verification_enabled,
            "verification_strict": step.verification_strict,
        }
