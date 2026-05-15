from __future__ import annotations

from collections import Counter
from typing import Any

from src.analyzer.service import AnalysisService
from src.models.automation import AutomationFlow, LocateStrategy, StepType
from src.models.operation import OperationEvent, OperationType
from src.models.session import Session


class PersonalWorkflowCopilotService:
    def __init__(self, repository=None, llm_service=None):
        self._repo = repository
        self._llm = llm_service

    async def build_session_brief(self, session_id: str, min_confidence: float = 0.3) -> dict[str, Any]:
        async def _operation(repo) -> dict[str, Any]:
            session = await repo.get_session(session_id)
            if session is None:
                raise LookupError(f"Session {session_id} not found")

            operations = await repo.get_operations_by_session(session_id)
            operation_summary = self._summarize_operations(operations)

            scored_flows = []
            if operations:
                analysis_service = AnalysisService(repository=repo, llm_service=self._llm)
                scored_flows = await analysis_service.analyze_session(session_id, min_confidence=min_confidence)

            top_flow = scored_flows[0].flow if scored_flows else None
            top_flow_confidence = scored_flows[0].overall_confidence if scored_flows else None
            flow_summary = self._summarize_flow(top_flow, top_flow_confidence)

            return {
                "session": self._serialize_session(session),
                "operation_summary": operation_summary,
                "candidate_flows": [
                    self._summarize_flow(item.flow, item.overall_confidence) for item in scored_flows[:3]
                ],
                "top_flow": flow_summary,
                "copilot_summary": await self._build_copilot_summary(
                    session=session,
                    operation_summary=operation_summary,
                    flow_summary=flow_summary,
                ),
            }

        if self._repo is not None:
            return await _operation(self._repo)

        from src.db.database import get_session_factory
        from src.db.repository import Repository

        factory = get_session_factory()
        async with factory() as db_session:
            return await _operation(Repository(db_session))

    @staticmethod
    def _serialize_session(session: Session) -> dict[str, Any]:
        return {
            "id": session.id,
            "name": session.name,
            "description": session.description,
            "status": session.status.value,
            "operation_count": session.operation_count,
            "duration_ms": session.duration_ms,
            "tags": session.tags,
        }

    def _summarize_operations(self, operations: list[OperationEvent]) -> dict[str, Any]:
        type_distribution = Counter(op.type.value for op in operations)
        app_distribution = Counter()
        window_titles: list[str] = []

        for op in operations:
            app_name = self._extract_app_name(op)
            if app_name:
                app_distribution[app_name] += 1

            window_title = self._extract_window_title(op)
            if window_title and window_title not in window_titles:
                window_titles.append(window_title)

        return {
            "total_operations": len(operations),
            "type_distribution": dict(type_distribution),
            "primary_app": app_distribution.most_common(1)[0][0] if app_distribution else None,
            "apps": [{"name": name, "count": count} for name, count in app_distribution.most_common(5)],
            "window_titles": window_titles[:5],
            "has_navigation": type_distribution.get(OperationType.NAVIGATION.value, 0) > 0,
            "has_text_input": type_distribution.get(OperationType.KEY_INPUT.value, 0) > 0,
            "has_window_switch": type_distribution.get(OperationType.WINDOW_SWITCH.value, 0) > 0,
        }

    def _summarize_flow(self, flow: AutomationFlow | None, confidence: float | None) -> dict[str, Any] | None:
        if flow is None:
            return None

        risk_steps: list[dict[str, Any]] = []
        parameter_candidates = [
            {
                "name": item.name,
                "type": item.var_type,
                "required": item.required,
                "description": item.description,
            }
            for item in flow.variables
        ]

        ordered_steps = flow.ordered_steps()
        for step in ordered_steps:
            issues: list[str] = []
            recommendations: list[str] = []
            locator_strategy = step.target.strategy.value if step.target else LocateStrategy.POSITION.value

            if step.locator_requires_confirmation():
                issues.append("定位方式需要人工确认")
                recommendations.append("在流程编辑器中确认元素定位器")

            if step.target and step.target.strategy == LocateStrategy.POSITION:
                issues.append("依赖坐标定位")
                recommendations.append("优先改成可复用的语义定位")

            if step.type in {
                StepType.CLICK,
                StepType.TYPE,
                StepType.NAVIGATE,
                StepType.SWITCH_WINDOW,
                StepType.DRAG,
            } and not step.preconditions and step.condition is None:
                issues.append("缺少前置等待或校验")
                recommendations.append("为关键动作补充元素存在或页面稳定检查")

            if step.on_error.value == "abort" and issues:
                issues.append("失败后会直接中止")
                recommendations.append("为高风险步骤增加重试或备用定位策略")

            if issues:
                risk_steps.append(
                    {
                        "step_id": step.id,
                        "step_type": step.type.value,
                        "description": step.description or step.type.value,
                        "locator_strategy": locator_strategy,
                        "issues": issues,
                        "recommendations": recommendations,
                    }
                )

        semantic_intent = (flow.metadata or {}).get("semantic_intent") or {}
        checkpoint_summary = (flow.metadata or {}).get("checkpoint_summary") or {}
        ai_analysis = (flow.metadata or {}).get("ai_analysis") or {}

        return {
            "id": flow.id,
            "name": flow.name,
            "description": flow.description,
            "confidence": confidence if confidence is not None else flow.confidence,
            "steps_count": len(flow.steps),
            "variables_count": len(flow.variables),
            "parameter_candidates": parameter_candidates,
            "risk_steps": risk_steps,
            "semantic_intent": semantic_intent,
            "checkpoint_summary": checkpoint_summary,
            "ai_analysis": ai_analysis,
            "steps_preview": [
                {
                    "id": step.id,
                    "type": step.type.value,
                    "description": step.description,
                    "locator_strategy": step.target.strategy.value if step.target else LocateStrategy.POSITION.value,
                }
                for step in ordered_steps[:6]
            ],
        }

    async def _build_copilot_summary(
        self,
        *,
        session: Session,
        operation_summary: dict[str, Any],
        flow_summary: dict[str, Any] | None,
    ) -> dict[str, Any]:
        summary_text = self._build_rule_based_summary(session, operation_summary, flow_summary)
        summary_mode = "rule_based"

        if self._llm is not None and getattr(self._llm, "is_configured", False) and flow_summary is not None:
            llm_summary = await self._try_llm_summary(operation_summary, flow_summary)
            if llm_summary:
                summary_text = llm_summary
                summary_mode = "llm"
        elif flow_summary and isinstance(flow_summary.get("ai_analysis"), dict):
            ai_analysis = flow_summary.get("ai_analysis") or {}
            if ai_analysis.get("summary"):
                summary_text = str(ai_analysis["summary"])
                summary_mode = "existing_ai"

        return {
            "mode": summary_mode,
            "summary": summary_text,
            "recommended_next_actions": self._recommended_next_actions(session, operation_summary, flow_summary),
            "stabilization_targets": (flow_summary or {}).get("risk_steps", [])[:5],
            "parameter_candidates": (flow_summary or {}).get("parameter_candidates", [])[:8],
        }

    async def _try_llm_summary(
        self,
        operation_summary: dict[str, Any],
        flow_summary: dict[str, Any],
    ) -> str | None:
        try:
            operations_text = self._format_operations_summary_text(operation_summary)
            flow_text = self._format_flow_summary_text(flow_summary)
            return await self._llm.analyze_flow(flow_text, operations_text)
        except Exception:
            return None

    def _build_rule_based_summary(
        self,
        session: Session,
        operation_summary: dict[str, Any],
        flow_summary: dict[str, Any] | None,
    ) -> str:
        total_operations = operation_summary.get("total_operations", 0)
        primary_app = operation_summary.get("primary_app") or "目标应用"

        if flow_summary is None:
            return (
                f"会话“{session.name}”当前共记录 {total_operations} 个操作，主要发生在 {primary_app}。"
                "系统还没有提炼出稳定主流程，建议继续录制一个更完整的闭环，"
                "并在关键页面使用“焦点上下文”辅助 AI 理解当前步骤。"
            )

        semantic_intent = flow_summary.get("semantic_intent") or {}
        intent = ((semantic_intent.get("intent") or {}).get("overall")) or flow_summary.get("name") or "当前流程"
        risk_count = len(flow_summary.get("risk_steps") or [])
        variables_count = int(flow_summary.get("variables_count") or 0)
        steps_count = int(flow_summary.get("steps_count") or 0)

        summary = (
            f"会话“{session.name}”主要在 {primary_app} 中完成“{intent}”，"
            f"已提炼出 1 条 {steps_count} 步的主流程。"
        )
        if variables_count > 0:
            summary += f" 其中识别出 {variables_count} 个可参数化字段。"
        if risk_count > 0:
            summary += f" 当前有 {risk_count} 个步骤需要优先加固后再投入执行。"
        else:
            summary += " 当前主流程已经具备较好的自动化基础，可以先做一次干跑验证。"
        return summary

    def _recommended_next_actions(
        self,
        session: Session,
        operation_summary: dict[str, Any],
        flow_summary: dict[str, Any] | None,
    ) -> list[str]:
        actions: list[str] = []

        if session.status.value != "stopped":
            actions.append("先完成一次完整录制，再让 AI 提炼稳定主流程")

        if flow_summary is None:
            if operation_summary.get("has_navigation"):
                actions.append("如果目标是 Web 流程，优先使用浏览器录制模式以获得稳定 selector")
            actions.append("在关键步骤使用焦点上下文，把当前元素和页面状态送入流程编辑器")
            return actions

        if flow_summary.get("parameter_candidates"):
            actions.append("把识别出的参数位抽成变量，避免把个人输入硬编码进流程")

        if flow_summary.get("risk_steps"):
            actions.append("优先处理高风险步骤：把坐标定位改成语义定位，并补等待与校验")

        checkpoint_summary = flow_summary.get("checkpoint_summary") or {}
        if int(checkpoint_summary.get("steps_with_checkpoints") or 0) == 0:
            actions.append("为关键动作添加结果校验，避免流程成功点击但页面状态未变化")

        if operation_summary.get("has_navigation"):
            actions.append("对涉及跳转的页面优先保留 URL、标题和 DOM 线索，减少回放漂移")

        actions.append("在流程编辑器里先干跑一次，再根据失败点做定向加固")
        return actions[:5]

    def _format_operations_summary_text(self, summary: dict[str, Any]) -> str:
        type_distribution = summary.get("type_distribution") or {}
        apps = summary.get("apps") or []
        app_names = ", ".join(f"{item['name']}({item['count']})" for item in apps[:3] if item.get("name"))
        op_types = ", ".join(f"{name}={count}" for name, count in type_distribution.items())
        return (
            f"total_operations={summary.get('total_operations', 0)}; "
            f"primary_app={summary.get('primary_app') or 'unknown'}; "
            f"apps={app_names or 'unknown'}; "
            f"operation_types={op_types or 'none'}"
        )

    def _format_flow_summary_text(self, flow_summary: dict[str, Any]) -> str:
        risk_count = len(flow_summary.get("risk_steps") or [])
        return (
            f"name={flow_summary.get('name') or 'unnamed'}; "
            f"steps={flow_summary.get('steps_count', 0)}; "
            f"confidence={flow_summary.get('confidence', 0)}; "
            f"variables={flow_summary.get('variables_count', 0)}; "
            f"risk_steps={risk_count}"
        )

    @staticmethod
    def _extract_app_name(operation: OperationEvent) -> str | None:
        if operation.context is None:
            return None
        if operation.context.process_name:
            return operation.context.process_name
        if operation.context.active_window and operation.context.active_window.app_name:
            return operation.context.active_window.app_name
        return None

    @staticmethod
    def _extract_window_title(operation: OperationEvent) -> str | None:
        if operation.context and operation.context.active_window and operation.context.active_window.title:
            return operation.context.active_window.title

        data = operation.data
        if operation.type == OperationType.NAVIGATION and getattr(data, "title", None):
            return str(data.title)
        if operation.type == OperationType.WINDOW_SWITCH and getattr(data, "to_window", None) is not None:
            return getattr(data.to_window, "title", None)
        return None
