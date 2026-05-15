from __future__ import annotations

import json
import logging
import time
import uuid

from src.agent.tools import TOOL_REGISTRY
from src.models.agent import (
    Permission,
    ProcessMap,
    ProcessMapEdge,
    ProcessMapNode,
    ToolCall,
    ToolResult,
    ToolType,
)

logger = logging.getLogger(__name__)


class ToolExecutor:
    def __init__(self, repository, llm_service):
        self._repo = repository
        self._llm = llm_service

    def check_permissions(self, tool_type: ToolType, granted: list[Permission]) -> bool:
        tool_def = TOOL_REGISTRY.get(tool_type)
        if not tool_def:
            return False
        return all(p in granted for p in tool_def.required_permissions)

    async def execute(self, call: ToolCall, granted: list[Permission]) -> ToolResult:
        start = time.time()
        call_id = call.call_id or str(uuid.uuid4())[:8]

        if not self.check_permissions(call.tool_type, granted):
            return ToolResult(
                call_id=call_id,
                success=False,
                error=f"Permission denied for tool {call.tool_type.value}",
                execution_ms=(time.time() - start) * 1000,
            )

        handler = getattr(self, f"_exec_{call.tool_type.value}", None)
        if not handler:
            return ToolResult(
                call_id=call_id,
                success=False,
                error=f"Unknown tool: {call.tool_type.value}",
                execution_ms=(time.time() - start) * 1000,
            )

        try:
            data = await handler(call.parameters)
            return ToolResult(
                call_id=call_id,
                success=True,
                data=data,
                execution_ms=(time.time() - start) * 1000,
            )
        except Exception as e:
            logger.error(f"Tool execution error: {e}", exc_info=True)
            return ToolResult(
                call_id=call_id,
                success=False,
                error=str(e),
                execution_ms=(time.time() - start) * 1000,
            )

    async def _exec_session_reader(self, params: dict) -> dict:
        session_id = params.get("session_id")
        include_ops = params.get("include_operations", False)

        if session_id:
            session = await self._repo.get_session(session_id)
            if not session:
                return {"error": f"Session {session_id} not found"}
            result = session.model_dump()
            if include_ops:
                ops = await self._repo.get_operations(session_id)
                result["operations"] = [op.model_dump() for op in ops]
            return result

        sessions = await self._repo.list_sessions()
        return {
            "total": len(sessions),
            "sessions": [
                {"id": s.id, "name": s.name, "status": s.status, "operation_count": s.operation_count} for s in sessions
            ],
        }

    async def _exec_operation_reader(self, params: dict) -> dict:
        session_id = params.get("session_id")
        if not session_id:
            return {"error": "session_id is required"}

        ops = await self._repo.get_operations(session_id)
        op_type_filter = params.get("operation_type")
        limit = params.get("limit", 100)

        if op_type_filter:
            ops = [op for op in ops if op.type.value == op_type_filter]

        ops = ops[:limit]

        type_counts = {}
        for op in ops:
            t = op.type.value
            type_counts[t] = type_counts.get(t, 0) + 1

        return {
            "session_id": session_id,
            "total_operations": len(ops),
            "type_distribution": type_counts,
            "operations": [op.model_dump() for op in ops[:50]],
        }

    async def _exec_flow_reader(self, params: dict) -> dict:
        flow_id = params.get("flow_id")
        include_steps = params.get("include_steps", True)

        if flow_id:
            flow = await self._repo.get_automation(flow_id)
            if not flow:
                return {"error": f"Flow {flow_id} not found"}
            result = flow.model_dump()
            if not include_steps:
                result.pop("steps", None)
            return result

        flows = await self._repo.list_automations()
        return {
            "total": len(flows),
            "flows": [
                {
                    "id": f.id,
                    "name": f.name,
                    "confidence": f.confidence,
                    "step_count": len(f.steps),
                    "execution_count": f.execution_count,
                    "success_count": f.success_count,
                }
                for f in flows
            ],
        }

    async def _exec_execution_reader(self, params: dict) -> dict:
        execution_id = params.get("execution_id")
        include_logs = params.get("include_logs", True)

        if execution_id:
            exec_rec = await self._repo.get_execution(execution_id)
            if not exec_rec:
                return {"error": f"Execution {execution_id} not found"}
            result = exec_rec.model_dump()
            if not include_logs:
                result.pop("step_logs", None)
            return result

        executions = await self._repo.list_executions(limit=30)
        return {
            "total": len(executions),
            "executions": [
                {
                    "id": e.id,
                    "automation_id": e.automation_id,
                    "status": e.status,
                    "total_steps": e.total_steps,
                    "completed_steps": e.completed_steps,
                    "failed_steps": e.failed_steps,
                }
                for e in executions
            ],
        }

    async def _exec_desktop_inspector(self, params: dict) -> dict:
        inspect_type = params.get("inspect_type", "state")
        try:
            from src.collector.service import DesktopCollector

            collector = DesktopCollector()
            if inspect_type == "windows":
                return {"windows": [w.model_dump() for w in collector.get_windows()]}
            elif inspect_type == "processes":
                return {"processes": [p.model_dump() for p in collector.get_processes()]}
            elif inspect_type == "active_element":
                elem = collector.get_focused_element()
                return {"element": elem.model_dump() if elem else None}
            elif inspect_type == "screenshot":
                path = collector.capture_screenshot()
                return {"screenshot_path": path}
            else:
                state = collector.get_desktop_state()
                return {"state": state.model_dump()}
        except Exception as e:
            return {
                "error": f"Desktop inspection failed: {e}",
                "hint": "Desktop access may not be available in this environment",
            }

    async def _exec_log_analyzer(self, params: dict) -> dict:
        log_type = params.get("log_type", "errors")
        keyword = params.get("keyword")

        executions = await self._repo.list_executions(limit=50)
        error_logs = []
        warning_logs = []

        for exec_rec in executions:
            if exec_rec.step_logs:
                for log in exec_rec.step_logs:
                    entry = {
                        "execution_id": exec_rec.id,
                        "step_index": log.step_index,
                        "step_type": log.step_type,
                        "status": log.status,
                        "timestamp": log.started_at,
                    }
                    if log.error_message:
                        entry["error_message"] = log.error_message
                        if keyword is None or keyword.lower() in log.error_message.lower():
                            error_logs.append(entry)
                    if log.verification_result:
                        vr = log.verification_result
                        if vr.get("overall_level") == "warning":
                            entry["warning"] = vr.get("visual_details", "")
                            warning_logs.append(entry)

        if log_type == "errors":
            return {"total_errors": len(error_logs), "errors": error_logs[:50]}
        elif log_type == "warnings":
            return {"total_warnings": len(warning_logs), "warnings": warning_logs[:50]}
        else:
            return {
                "total_errors": len(error_logs),
                "total_warnings": len(warning_logs),
                "errors": error_logs[:30],
                "warnings": warning_logs[:20],
            }

    async def _exec_error_tracker(self, params: dict) -> dict:
        execution_id = params.get("execution_id")

        if execution_id:
            exec_rec = await self._repo.get_execution(execution_id)
            if not exec_rec:
                return {"error": f"Execution {execution_id} not found"}
            failed_steps = [log for log in (exec_rec.step_logs or []) if log.status == "failed"]
            return {
                "execution_id": execution_id,
                "status": exec_rec.status,
                "total_steps": exec_rec.total_steps,
                "failed_steps": len(failed_steps),
                "failures": [
                    {
                        "step_index": s.step_index,
                        "step_type": s.step_type,
                        "error_message": s.error_message,
                        "screenshot_before": s.screenshot_before,
                        "screenshot_after": s.screenshot_after,
                        "verification_result": s.verification_result,
                    }
                    for s in failed_steps
                ],
            }

        executions = await self._repo.list_executions(limit=50)
        failed_execs = [e for e in executions if e.status == "failed"]
        error_patterns = {}
        for e in failed_execs:
            if e.step_logs:
                for log in e.step_logs:
                    if log.status == "failed" and log.error_message:
                        key = log.error_message[:80]
                        error_patterns[key] = error_patterns.get(key, 0) + 1

        return {
            "total_failed_executions": len(failed_execs),
            "error_patterns": dict(sorted(error_patterns.items(), key=lambda x: -x[1])[:10]),
            "recent_failures": [
                {"execution_id": e.id, "failed_steps": e.failed_steps, "error_summary": e.error_summary}
                for e in failed_execs[:10]
            ],
        }

    async def _exec_code_inspector(self, params: dict) -> dict:
        flow_id = params.get("flow_id")
        if not flow_id:
            return {"error": "flow_id is required"}

        flow = await self._repo.get_automation(flow_id)
        if not flow:
            return {"error": f"Flow {flow_id} not found"}

        output_format = params.get("format", "json")
        issues = []

        for i, step in enumerate(flow.steps):
            target = step.target or {}
            if target.get("strategy") == "position" or (not target.get("strategy") and target.get("position")):
                issues.append(
                    {
                        "step": i + 1,
                        "severity": "warning",
                        "issue": "Uses coordinate-based positioning which is fragile",
                        "suggestion": "Consider using accessibility_id, text_match, or css_selector for more reliable targeting",
                    }
                )
            if (
                step.type == "click"
                and not target.get("accessibility_id")
                and not target.get("title")
                and not target.get("selector")
            ):
                issues.append(
                    {
                        "step": i + 1,
                        "severity": "info",
                        "issue": "Click step lacks semantic target information",
                        "suggestion": "Add accessibility_id or title for better reliability",
                    }
                )
            if step.delay and step.delay < 200:
                issues.append(
                    {
                        "step": i + 1,
                        "severity": "warning",
                        "issue": f"Very short delay ({step.delay}ms) may cause timing issues",
                        "suggestion": "Consider increasing delay to at least 300ms or adding a condition check",
                    }
                )
            if not step.on_error:
                issues.append(
                    {
                        "step": i + 1,
                        "severity": "info",
                        "issue": "No error handling defined",
                        "suggestion": "Add on_error policy (retry, skip, or abort) for robustness",
                    }
                )

        if output_format == "python":
            try:
                result = await self._repo.export_automation(flow_id, "python")
                return {"flow_id": flow_id, "code": result.get("content", ""), "issues": issues}
            except Exception:
                pass

        return {"flow_id": flow_id, "flow_definition": flow.model_dump(), "issues": issues, "total_issues": len(issues)}

    async def _exec_flow_optimizer(self, params: dict) -> dict:
        flow_id = params.get("flow_id")
        if not flow_id:
            return {"error": "flow_id is required"}

        flow = await self._repo.get_automation(flow_id)
        if not flow:
            return {"error": f"Flow {flow_id} not found"}

        target = params.get("optimization_target", "all")
        suggestions = []
        steps = flow.steps or []

        consecutive_waits = 0
        for i, step in enumerate(steps):
            if step.type == "wait":
                consecutive_waits += 1
                if consecutive_waits > 1:
                    suggestions.append(
                        {
                            "step": i + 1,
                            "category": "speed",
                            "title": "Consolidate consecutive waits",
                            "description": f"Multiple consecutive wait steps detected (steps {i}-{i + consecutive_waits - 1})",
                            "impact": "medium",
                        }
                    )
            else:
                consecutive_waits = 0

            tgt = step.target or {}
            if tgt.get("strategy") == "position" and (target in ("reliability", "all")):
                suggestions.append(
                    {
                        "step": i + 1,
                        "category": "reliability",
                        "title": "Replace coordinate targeting",
                        "description": f"Step {i + 1} uses position-based targeting which breaks with layout changes",
                        "impact": "high",
                    }
                )

            if not step.on_error and (target in ("reliability", "maintainability", "all")):
                suggestions.append(
                    {
                        "step": i + 1,
                        "category": "maintainability",
                        "title": "Add error handling",
                        "description": f"Step {i + 1} has no error handling policy",
                        "impact": "medium",
                    }
                )

        executions = await self._repo.list_executions(limit=20)
        flow_execs = [e for e in executions if e.automation_id == flow_id]
        success_rate = 0.0
        if flow_execs:
            successes = sum(1 for e in flow_execs if e.status == "completed")
            success_rate = successes / len(flow_execs)

        return {
            "flow_id": flow_id,
            "flow_name": flow.name,
            "optimization_target": target,
            "current_stats": {
                "step_count": len(steps),
                "execution_count": flow.execution_count,
                "success_rate": f"{success_rate:.1%}",
                "confidence": f"{flow.confidence:.0%}",
            },
            "suggestions": suggestions,
            "total_suggestions": len(suggestions),
        }

    async def _exec_pattern_detector(self, params: dict) -> dict:
        session_id = params.get("session_id")
        if not session_id:
            return {"error": "session_id is required"}

        ops = await self._repo.get_operations(session_id)
        if not ops:
            return {"session_id": session_id, "patterns": [], "message": "No operations found"}

        min_occ = params.get("min_occurrences", 2)
        type_sequences = []
        window_seq_len = min(5, len(ops))

        for i in range(len(ops) - window_seq_len + 1):
            seq = tuple(op.type.value for op in ops[i : i + window_seq_len])
            type_sequences.append(seq)

        pattern_counts = {}
        for seq in type_sequences:
            pattern_counts[seq] = pattern_counts.get(seq, 0) + 1

        recurring = {k: v for k, v in pattern_counts.items() if v >= min_occ}

        return {
            "session_id": session_id,
            "total_operations": len(ops),
            "window_size": window_seq_len,
            "recurring_patterns": [
                {"sequence": list(seq), "occurrences": count}
                for seq, count in sorted(recurring.items(), key=lambda x: -x[1])[:10]
            ],
            "automation_opportunity": len(recurring) > 0,
        }

    async def _exec_metrics_collector(self, params: dict) -> dict:
        flow_id = params.get("flow_id")
        params.get("metric_type", "all")

        if flow_id:
            executions = await self._repo.list_executions(limit=100)
            flow_execs = [e for e in executions if e.automation_id == flow_id]
            if not flow_execs:
                return {"flow_id": flow_id, "message": "No execution data available"}

            completed = [e for e in flow_execs if e.status == "completed"]
            failed = [e for e in flow_execs if e.status == "failed"]

            avg_steps = 0.0
            if completed:
                avg_steps = sum(e.completed_steps for e in completed) / len(completed)

            return {
                "flow_id": flow_id,
                "execution_metrics": {
                    "total_executions": len(flow_execs),
                    "successful": len(completed),
                    "failed": len(failed),
                    "success_rate": f"{len(completed) / len(flow_execs):.1%}" if flow_execs else "N/A",
                    "avg_completed_steps": f"{avg_steps:.1f}",
                },
            }

        executions = await self._repo.list_executions(limit=100)
        flows = await self._repo.list_automations()

        total_execs = len(executions)
        completed = sum(1 for e in executions if e.status == "completed")
        failed = sum(1 for e in executions if e.status == "failed")

        flow_metrics = []
        for f in flows:
            f_execs = [e for e in executions if e.automation_id == f.id]
            f_success = sum(1 for e in f_execs if e.status == "completed")
            flow_metrics.append(
                {
                    "flow_id": f.id,
                    "name": f.name,
                    "executions": len(f_execs),
                    "success_rate": f"{f_success / len(f_execs):.1%}" if f_execs else "N/A",
                }
            )

        return {
            "global_metrics": {
                "total_flows": len(flows),
                "total_executions": total_execs,
                "overall_success_rate": f"{completed / total_execs:.1%}" if total_execs else "N/A",
                "total_failures": failed,
            },
            "flow_metrics": flow_metrics[:20],
        }

    async def _exec_document_generator(self, params: dict) -> dict:
        flow_id = params.get("flow_id")
        if not flow_id:
            return {"error": "flow_id is required"}

        flow = await self._repo.get_automation(flow_id)
        if not flow:
            return {"error": f"Flow {flow_id} not found"}

        doc_type = params.get("doc_type", "full")
        steps = flow.steps or []

        process_doc = f"# {flow.name}\n\n"
        process_doc += f"**Description:** {flow.description or 'N/A'}\n\n"
        process_doc += f"**Confidence:** {flow.confidence:.0%}\n"
        process_doc += f"**Total Steps:** {len(steps)}\n"
        process_doc += f"**Executions:** {flow.execution_count} (Success: {flow.success_count})\n\n"

        if doc_type in ("process", "full"):
            process_doc += "## Process Steps\n\n"
            for i, step in enumerate(steps):
                target = step.target or {}
                action = step.action or {}
                process_doc += f"### Step {i + 1}: {step.type.value.upper()}\n\n"
                if target.get("accessibility_id"):
                    process_doc += f"- **Target:** [{target['accessibility_id']}]\n"
                if target.get("title"):
                    process_doc += f'- **Element:** "{target["title"]}"\n'
                if target.get("position"):
                    process_doc += f"- **Position:** ({target['position'].x}, {target['position'].y})\n"
                if action.get("text"):
                    process_doc += f'- **Input:** "{action["text"][:50]}"\n'
                process_doc += f"- **Delay:** {step.delay or 500}ms\n"
                process_doc += f"- **Error Handling:** {step.on_error or 'None'}\n\n"

        if doc_type in ("technical", "full"):
            process_doc += "## Technical Details\n\n"
            strategies = {}
            for step in steps:
                s = (step.target or {}).get("strategy", "position")
                strategies[s] = strategies.get(s, 0) + 1
            process_doc += f"- **Location Strategies:** {json.dumps(strategies)}\n"
            process_doc += (
                f"- **Error Handling Coverage:** {sum(1 for s in steps if s.on_error)}/{len(steps)} steps\n\n"
            )

        return {"flow_id": flow_id, "document": process_doc, "doc_type": doc_type}

    async def _exec_visualization_engine(self, params: dict) -> dict:
        target_id = params.get("target_id")
        viz_type = params.get("viz_type", "process_map")

        if not target_id:
            return {"error": "target_id is required"}

        flow = await self._repo.get_automation(target_id)
        if flow:
            steps = flow.steps or []
            nodes = []
            edges = []

            nodes.append(ProcessMapNode(id="start", label="Start", type="start"))
            for i, step in enumerate(steps):
                node_id = f"step_{i}"
                target = step.target or {}
                label = step.type.value
                if target.get("accessibility_id"):
                    label = f"{step.type.value}: [{target['accessibility_id']}]"
                elif target.get("title"):
                    label = f'{step.type.value}: "{target["title"][:20]}"'
                nodes.append(
                    ProcessMapNode(
                        id=node_id,
                        label=label,
                        type=step.type.value,
                        details={"delay": step.delay, "strategy": (step.target or {}).get("strategy", "position")},
                    )
                )
                prev_id = f"step_{i - 1}" if i > 0 else "start"
                edge_label = ""
                if step.condition:
                    edge_label = str(step.condition)
                edges.append(ProcessMapEdge(source=prev_id, target=node_id, label=edge_label))

            nodes.append(ProcessMapNode(id="end", label="End", type="end"))
            if steps:
                edges.append(ProcessMapEdge(source=f"step_{len(steps) - 1}", target="end"))

            process_map = ProcessMap(
                nodes=nodes,
                edges=edges,
                metadata={"flow_id": flow.id, "flow_name": flow.name, "step_count": len(steps)},
            )
            return {"visualization_type": viz_type, "process_map": process_map.model_dump()}

        exec_rec = await self._repo.get_execution(target_id)
        if exec_rec:
            timeline = []
            for log in exec_rec.step_logs or []:
                timeline.append(
                    {
                        "step_index": log.step_index,
                        "step_type": log.step_type,
                        "status": log.status,
                        "started_at": log.started_at,
                        "completed_at": log.completed_at,
                        "error": log.error_message,
                    }
                )
            return {"visualization_type": "execution_timeline", "timeline": timeline, "execution_id": target_id}

        return {"error": f"Target {target_id} not found as flow or execution"}
