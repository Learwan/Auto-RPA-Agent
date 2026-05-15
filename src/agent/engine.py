from __future__ import annotations

import contextlib
import json
import logging
import time
import uuid

from src.agent.executor import ToolExecutor
from src.agent.tools import DEFAULT_PERMISSIONS, SKILL_DEFINITIONS, TOOL_REGISTRY
from src.models.agent import (
    AgentMessage,
    AgentMessageRole,
    AgentResponse,
    AgentSession,
    DebugSession,
    DebugTrace,
    ImprovementSuggestion,
    Permission,
    ProcessMap,
    RootCauseAnalysis,
    RootCauseHypothesis,
    SkillCategory,
    ToolCall,
    ToolResult,
    ToolType,
    WorkflowAssessment,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a specialized AI assistant agent focused on analyzing and organizing workflows. You have professional-grade analytical tools for process mapping, workflow optimization, and data organization.

## CRITICAL RULES — You MUST follow these:

### Anti-Hallucination Rules
1. **NEVER fabricate or invent data.** You must use tools to get real data before presenting any analysis.
2. **If a tool call fails or returns no data**, you MUST state clearly: "I was unable to retrieve the data needed to analyze this workflow. The tool returned: [error/empty]." Do NOT make up substitute data.
3. **Every claim in your analysis MUST be traceable to actual tool results.** If you say "the flow has 5 steps", you must have gotten that number from a tool result.
4. **If you cannot get real data via tools, you MUST refuse to analyze** rather than fabricate an analysis.

### Tool Calling Rules
5. **ALWAYS use tools to read data before analyzing.** Never assume flow names, step counts, or any metadata.
6. **To call a tool, write parameters as a JSON code block:**
```
```json
{{"parameter_name": "value", "another_param": true}}
```
```
The system will automatically infer which tool you want based on the parameter names. Always wrap your JSON in triple backticks with the `json` language tag.

### Response Rules
7. **Cite specific tool results** in your analysis: "According to the flow_reader tool, this flow has X steps..."
8. When using tools, briefly explain your reasoning before each tool call.
9. Structure analysis with: **Assessment** → **Findings** → **Recommendations** → **Next Steps**.
10. Respond in the same language as the user's input.

## Your Core Capabilities

### Process Mapping & Visualization
- Map operational workflows from recorded sessions
- Identify process steps, dependencies, and relationships
- Generate visual process maps and flow diagrams

### Root Cause Analysis
- Systematically investigate execution failures
- Trace errors through execution logs and step histories
- Form evidence-based hypotheses with confidence scores
- Recommend remediation strategies

### Workflow Optimization
- Analyze automation flows for efficiency improvements
- Identify fragile steps (coordinate-based targeting, missing error handling)
- Suggest reliability, speed, and maintainability enhancements
- Score workflows on multiple quality dimensions

### Continuous Improvement
- Detect recurring patterns in operations
- Track metrics and trends across executions
- Identify automation opportunities
- Prioritize improvement suggestions by impact and effort

### Debugging & Diagnostics
- Inspect execution failures with full context
- Analyze error patterns and frequencies
- Review desktop state and UI elements
- Inspect flow definitions as code

### Documentation
- Generate comprehensive workflow documentation
- Create process descriptions and technical specs
- Produce change logs and modification records

## Available Tools

{tool_descriptions}"""


def _build_tool_descriptions() -> str:
    lines = []
    for tool_type, tool_def in TOOL_REGISTRY.items():
        params = ", ".join(f"{p.name}{'?' if not p.required else ''}: {p.type}" for p in tool_def.parameters)
        lines.append(f"- **{tool_def.name}** (`{tool_type.value}`): {tool_def.description}. Parameters: [{params}]")
    return "\n".join(lines)


class AgentEngine:
    def __init__(self, repository, llm_service):
        self._repo = repository
        self._llm = llm_service
        self._tool_executor = ToolExecutor(repository, llm_service)
        self._sessions: dict[str, AgentSession] = {}

    async def create_session(
        self,
        permissions: list[Permission] | None = None,
        skills: list[SkillCategory] | None = None,
        context: dict | None = None,
    ) -> AgentSession:
        session_id = str(uuid.uuid4())
        now = time.time()
        session = AgentSession(
            id=session_id,
            created_at=now,
            updated_at=now,
            granted_permissions=permissions or list(DEFAULT_PERMISSIONS),
            active_skills=skills
            or [SkillCategory.PROCESS_MAPPING, SkillCategory.WORKFLOW_OPTIMIZATION, SkillCategory.DEBUGGING],
            context=context or {},
        )
        self._sessions[session_id] = session
        return session

    async def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    async def list_sessions(self) -> list[AgentSession]:
        return list(self._sessions.values())

    async def process_message(
        self,
        session_id: str,
        user_message: str,
        max_iterations: int = 5,
    ) -> AgentResponse:
        session = self._sessions.get(session_id)
        if not session:
            return AgentResponse(message="Session not found. Please create a new session.")

        session.messages.append(
            AgentMessage(
                role=AgentMessageRole.USER,
                content=user_message,
                timestamp=time.time(),
            )
        )

        tool_descriptions = _build_tool_descriptions()
        system_prompt = SYSTEM_PROMPT.format(tool_descriptions=tool_descriptions)

        skill_context = ""
        if session.active_skills:
            skill_names = [SKILL_DEFINITIONS.get(s, {}).get("name", s.value) for s in session.active_skills]
            skill_context = f"\n\nActive skills: {', '.join(skill_names)}"

        if session.context:
            ctx_parts = [f"- {k}: {v}" for k, v in session.context.items() if v]
            if ctx_parts:
                skill_context += "\n\nContext:\n" + "\n".join(ctx_parts)

        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        final_response = ""
        process_map = None
        root_cause = None
        assessment = None
        debug_session_result = None
        suggestions: list[ImprovementSuggestion] = []

        for _iteration in range(max_iterations):
            messages = [{"role": "system", "content": system_prompt + skill_context}]

            for msg in session.messages[-20:]:
                if msg.role == AgentMessageRole.USER:
                    messages.append({"role": "user", "content": msg.content})
                elif msg.role == AgentMessageRole.ASSISTANT or msg.role == AgentMessageRole.TOOL_CALL:
                    messages.append({"role": "assistant", "content": msg.content})
                elif msg.role == AgentMessageRole.TOOL_RESULT:
                    messages.append({"role": "user", "content": msg.content})

            try:
                response_text = await self._llm.chat(messages, temperature=0.3, max_tokens=2048)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                final_response = f"I encountered an error processing your request: {e}"
                break

            tool_calls = self._extract_tool_calls(response_text)

            if not tool_calls:
                final_response = response_text
                break

            for tc in tool_calls:
                tc.call_id = str(uuid.uuid4())[:8]
                all_tool_calls.append(tc)

                session.messages.append(
                    AgentMessage(
                        role=AgentMessageRole.TOOL_CALL,
                        content=f"Calling tool: {tc.tool_type.value} with params: {json.dumps(tc.parameters, ensure_ascii=False)}",
                        tool_call=tc,
                        timestamp=time.time(),
                    )
                )

                result = await self._tool_executor.execute(tc, session.granted_permissions)
                all_tool_results.append(result)

                result_content = f"Tool {tc.tool_type.value} result: "
                if result.success:
                    result_data = result.data
                    if isinstance(result_data, dict):
                        if "process_map" in result_data:
                            with contextlib.suppress(Exception):
                                process_map = ProcessMap(**result_data["process_map"])
                        if "root_cause" in result_data:
                            with contextlib.suppress(Exception):
                                root_cause = RootCauseAnalysis(**result_data["root_cause"])
                        result_content += json.dumps(result_data, ensure_ascii=False, default=str)[:2000]
                    else:
                        result_content += str(result_data)[:2000]
                else:
                    result_content += f"Error: {result.error}"

                session.messages.append(
                    AgentMessage(
                        role=AgentMessageRole.TOOL_RESULT,
                        content=result_content,
                        tool_result=result,
                        timestamp=time.time(),
                    )
                )

        if not final_response:
            final_response = await self._generate_summary(session, all_tool_results)

        validation_warning = self._validate_response(final_response, all_tool_results)
        if validation_warning:
            final_response = validation_warning + "\n\n---\n\n" + final_response
            logger.warning(f"Response validation warning for session {session_id}: {validation_warning[:200]}")

        suggestions = self._extract_suggestions(all_tool_results, session.active_skills)

        if session.context.get("flow_id") and not assessment:
            assessment = self._build_assessment(all_tool_results, session)

        if session.context.get("execution_id") and not debug_session_result:
            debug_session_result = self._build_debug_session(all_tool_results, session)

        session.messages.append(
            AgentMessage(
                role=AgentMessageRole.ASSISTANT,
                content=final_response,
                timestamp=time.time(),
            )
        )
        session.updated_at = time.time()

        return AgentResponse(
            message=final_response,
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
            process_map=process_map,
            root_cause_analysis=root_cause,
            workflow_assessment=assessment,
            debug_session=debug_session_result,
            suggestions=suggestions,
        )

    def _extract_tool_calls(self, text: str) -> list[ToolCall]:
        calls = []
        import re

        json_block_calls = self._extract_json_block_tool_calls(text)
        calls.extend(json_block_calls)

        patterns = [
            r"```tool\n(.*?)```",
            r"\[TOOL_CALL:\s*(\w+)(?:\((.*?)\))?\]",
            r"(?:use|call|invoke)\s+(?:the\s+)?(\w+)\s+(?:tool|function)(?:\s+with\s+(.*?))?(?:\n|$)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            for match in matches:
                try:
                    if isinstance(match, tuple):
                        tool_name = match[0].strip()
                        params_str = match[1].strip() if len(match) > 1 and match[1] else "{}"
                    else:
                        tool_name = match.strip()
                        params_str = "{}"

                    tool_type = None
                    for tt in ToolType:
                        if tt.value.lower() == tool_name.lower().replace("-", "_").replace(" ", "_"):
                            tool_type = tt
                            break

                    if not tool_type:
                        name_lower = tool_name.lower()
                        for tt in ToolType:
                            if name_lower in tt.value or tt.value in name_lower:
                                tool_type = tt
                                break

                    if tool_type:
                        try:
                            params = json.loads(params_str) if params_str and params_str != "{}" else {}
                        except json.JSONDecodeError:
                            params = {}

                        calls.append(ToolCall(tool_type=tool_type, parameters=params))
                except Exception:
                    continue

        return calls

    def _extract_json_block_tool_calls(self, text: str) -> list[ToolCall]:
        import re

        from src.agent.tools import TOOL_REGISTRY

        calls = []
        json_block_pattern = r"```json\s*\n(.*?)\n\s*```"
        json_blocks = re.findall(json_block_pattern, text, re.DOTALL)

        if not json_blocks:
            inline_pattern = r"```\s*\n?\s*(\{[^`]*?\})\s*\n?\s*```"
            json_blocks = re.findall(inline_pattern, text, re.DOTALL)

        for block in json_blocks:
            try:
                params = json.loads(block.strip())
                if not isinstance(params, dict):
                    continue

                tool_type = self._infer_tool_from_params(params, TOOL_REGISTRY)
                if tool_type:
                    calls.append(ToolCall(tool_type=tool_type, parameters=params))
                    logger.info(f"Inferred tool call from JSON block: {tool_type.value} with params {params}")
            except json.JSONDecodeError:
                continue

        return calls

    def _infer_tool_from_params(self, params: dict, registry: dict) -> ToolType | None:
        param_keys = set(params.keys())
        if not param_keys:
            return None

        best_tool = None
        best_score = 0

        for tool_type, tool_def in registry.items():
            tool_param_names = {p.name for p in tool_def.parameters if p.required}
            optional_names = {p.name for p in tool_def.parameters if not p.required}
            all_names = tool_param_names | optional_names

            required_matches = param_keys & tool_param_names
            optional_matches = param_keys & optional_names
            required_matches | optional_matches
            extra_keys = param_keys - all_names

            if not required_matches and not optional_matches:
                continue

            score = len(required_matches) * 3 + len(optional_matches) - len(extra_keys) * 2
            if score > best_score:
                best_score = score
                best_tool = tool_type

        return best_tool

    async def _generate_summary(self, session: AgentSession, results: list[ToolResult]) -> str:
        if not results:
            return "I've analyzed your request but didn't need to use any tools. How can I help further?"

        summary_parts = ["Based on my analysis:\n"]
        for r in results[-3:]:
            if r.success and r.data and isinstance(r.data, dict):
                key_info = {
                    k: v
                    for k, v in r.data.items()
                    if k
                    in (
                        "total",
                        "total_errors",
                        "total_suggestions",
                        "flow_name",
                        "success_rate",
                        "automation_opportunity",
                    )
                }
                if key_info:
                    summary_parts.append(f"- {json.dumps(key_info, ensure_ascii=False)}")

        summary_parts.append("\nWould you like me to dive deeper into any specific area?")
        return "\n".join(summary_parts)

    def _extract_suggestions(
        self, results: list[ToolResult], skills: list[SkillCategory]
    ) -> list[ImprovementSuggestion]:
        suggestions = []
        idx = 0
        for r in results:
            if not r.success or not isinstance(r.data, dict):
                continue
            raw_suggestions = r.data.get("suggestions", [])
            for s in raw_suggestions:
                if isinstance(s, dict):
                    idx += 1
                    suggestions.append(
                        ImprovementSuggestion(
                            id=f"imp_{idx}",
                            category=SkillCategory.WORKFLOW_OPTIMIZATION,
                            title=s.get("title", "Improvement"),
                            description=s.get("description", ""),
                            impact=s.get("impact", "medium"),
                            effort=s.get("effort", "medium"),
                            priority=idx,
                        )
                    )
        return suggestions[:10]

    def _validate_response(self, response: str, results: list[ToolResult]) -> str | None:
        if not results:
            import re

            specific_claims = re.findall(
                r"(?:步骤数量|step count|步骤|steps?|流程名称|flow name|评分|score|创建时间|created|最后修改|last modified)",
                response,
                re.IGNORECASE,
            )
            if len(specific_claims) >= 2:
                return (
                    "⚠️ **DATA INTEGRITY WARNING**: This analysis contains specific claims "
                    "(step counts, flow names, scores, timestamps) but NO tool results were obtained. "
                    "The analysis below may be fabricated. Please verify with actual data sources."
                )
            return None

        has_real_data = any(r.success and r.data for r in results)
        if not has_real_data:
            import re

            specific_claims = re.findall(
                r"(?:步骤数量|step count|步骤|steps?|流程名称|flow name|评分|score)", response, re.IGNORECASE
            )
            if len(specific_claims) >= 2:
                return (
                    "⚠️ **DATA INTEGRITY WARNING**: All tool calls failed, yet this analysis "
                    "contains specific claims. The data below may not be accurate."
                )
            return None

        return None

    def _build_assessment(self, results: list[ToolResult], session: AgentSession) -> WorkflowAssessment | None:
        flow_id = session.context.get("flow_id", "")
        flow_name = session.context.get("flow_name", "")
        efficiency = 0.0
        reliability = 0.0
        maintainability = 0.0

        for r in results:
            if not r.success or not isinstance(r.data, dict):
                continue
            stats = r.data.get("current_stats", {})
            if stats:
                sr = stats.get("success_rate", "0%")
                if isinstance(sr, str):
                    sr = float(sr.replace("%", "")) / 100
                reliability = max(reliability, sr)
                confidence = stats.get("confidence", "0%")
                if isinstance(confidence, str):
                    confidence = float(confidence.replace("%", "")) / 100
                efficiency = max(efficiency, confidence)

            issues = r.data.get("issues", [])
            if issues:
                coverage = 1.0 - (len(issues) * 0.05)
                maintainability = max(maintainability, coverage)

        if efficiency == 0 and reliability == 0:
            return None

        return WorkflowAssessment(
            flow_id=flow_id,
            flow_name=flow_name,
            efficiency_score=efficiency,
            reliability_score=reliability,
            maintainability_score=maintainability,
        )

    def _build_debug_session(self, results: list[ToolResult], session: AgentSession) -> DebugSession | None:
        execution_id = session.context.get("execution_id", "")
        traces = []
        root_cause = None

        for r in results:
            if not r.success or not isinstance(r.data, dict):
                continue
            failures = r.data.get("failures", [])
            for f in failures:
                traces.append(
                    DebugTrace(
                        execution_id=execution_id,
                        step_index=f.get("step_index", 0),
                        step_type=f.get("step_type", ""),
                        status="failed",
                        error_message=f.get("error_message"),
                        verification_result=f.get("verification_result"),
                        timestamp=time.time(),
                    )
                )

        if not traces and not any(r.data.get("failures") for r in results if r.success and isinstance(r.data, dict)):
            return None

        hypotheses = []
        for trace in traces:
            hypotheses.append(
                RootCauseHypothesis(
                    id=f"hyp_{len(hypotheses) + 1}",
                    description=f"Step {trace.step_index} ({trace.step_type}) failed: {trace.error_message or 'Unknown error'}",
                    confidence=0.6,
                    evidence=[trace.error_message or "No error message"],
                    remediation="Review step configuration and target element availability",
                )
            )

        if hypotheses:
            root_cause = RootCauseAnalysis(
                problem=f"Execution {execution_id} failed at {len(traces)} step(s)",
                hypotheses=hypotheses,
                conclusion="Multiple step failures detected, likely due to element targeting or timing issues",
                recommendations=[
                    "Verify target elements are accessible",
                    "Add appropriate wait conditions",
                    "Consider using more robust targeting strategies",
                ],
            )

        return DebugSession(
            id=str(uuid.uuid4())[:8],
            execution_id=execution_id,
            traces=traces,
            root_cause=root_cause,
        )

    async def analyze_workflow(self, flow_id: str) -> AgentResponse:
        flow = await self._repo.get_automation(flow_id)
        if not flow:
            return AgentResponse(message=f"Flow {flow_id} not found")

        session = await self.create_session(
            skills=[SkillCategory.WORKFLOW_OPTIMIZATION, SkillCategory.PROCESS_MAPPING],
            context={"flow_id": flow_id, "flow_name": flow.name},
        )

        return await self.process_message(
            session.id,
            f"Perform a comprehensive analysis of workflow '{flow.name}' (ID: {flow_id}). "
            f"Assess its efficiency, reliability, and maintainability. "
            f"Use the flow_reader tool to get details, then the flow_optimizer and visualization_engine tools. "
            f"Provide a complete assessment with improvement suggestions.",
        )

    async def debug_execution(self, execution_id: str) -> AgentResponse:
        exec_rec = await self._repo.get_execution(execution_id)
        if not exec_rec:
            return AgentResponse(message=f"Execution {execution_id} not found")

        session = await self.create_session(
            skills=[SkillCategory.ROOT_CAUSE_ANALYSIS, SkillCategory.DEBUGGING],
            context={"execution_id": execution_id, "flow_id": exec_rec.automation_id},
        )

        return await self.process_message(
            session.id,
            f"Debug execution {execution_id} which has status '{exec_rec.status}'. "
            f"Use execution_reader and error_tracker tools to investigate failures. "
            f"Perform root cause analysis and provide remediation recommendations.",
        )

    async def map_process(self, session_id: str) -> AgentResponse:
        ops = await self._repo.get_operations(session_id)
        rec_session = await self._repo.get_session(session_id)
        session_name = rec_session.name if rec_session else session_id

        agent_session = await self.create_session(
            skills=[SkillCategory.PROCESS_MAPPING, SkillCategory.VISUALIZATION],
            context={"recording_session_id": session_id, "session_name": session_name, "operation_count": len(ops)},
        )

        return await self.process_message(
            agent_session.id,
            f"Map the process from recording session '{session_name}' (ID: {session_id}, {len(ops)} operations). "
            f"Use session_reader and operation_reader to understand the workflow, "
            f"then use pattern_detector and visualization_engine to create a process map.",
        )

    async def get_skills(self) -> list[dict]:
        return [
            {
                "category": cat.value,
                "name": info["name"],
                "description": info["description"],
                "tools": [t.value for t in info["tools"]],
            }
            for cat, info in SKILL_DEFINITIONS.items()
        ]

    async def get_tools(self) -> list[dict]:
        return [
            {
                "type": td.type.value,
                "name": td.name,
                "description": td.description,
                "parameters": [p.model_dump() for p in td.parameters],
                "required_permissions": [p.value for p in td.required_permissions],
                "category": td.category.value,
            }
            for td in TOOL_REGISTRY.values()
        ]
