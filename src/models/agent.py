from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Permission(str, Enum):
    VIEW_SESSIONS = "view_sessions"
    CREATE_SESSION = "create_session"
    EDIT_SESSION = "edit_session"
    DELETE_SESSION = "delete_session"
    VIEW_AUTOMATIONS = "view_automations"
    CREATE_AUTOMATION = "create_automation"
    EXECUTE_AUTOMATION = "execute_automation"
    DELETE_AUTOMATION = "delete_automation"
    VIEW_EXECUTIONS = "view_executions"
    MANAGE_SETTINGS = "manage_settings"
    MANAGE_USERS = "manage_users"
    ADMIN = "admin"
    READ_SESSIONS = "read_sessions"
    READ_OPERATIONS = "read_operations"
    READ_FLOWS = "read_flows"
    READ_EXECUTIONS = "read_executions"
    READ_DESKTOP = "read_desktop"
    READ_LOGS = "read_logs"
    ANALYZE_DATA = "analyze_data"
    DEBUG_EXECUTIONS = "debug_executions"
    ACCESS_CODEBASE = "access_codebase"
    ACCESS_FILESYSTEM = "access_filesystem"
    WRITE_SESSIONS = "write_sessions"
    WRITE_FLOWS = "write_flows"
    EXECUTE_FLOWS = "execute_flows"
    MODIFY_WORKFLOWS = "modify_workflows"


class SkillCategory(str, Enum):
    PROCESS_MAPPING = "process_mapping"
    WORKFLOW_OPTIMIZATION = "workflow_optimization"
    DEBUGGING = "debugging"
    DATA_ANALYSIS = "data_analysis"
    AUTOMATION_DESIGN = "automation_design"
    ERROR_DIAGNOSIS = "error_diagnosis"
    PERFORMANCE_TUNING = "performance_tuning"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    CONTINUOUS_IMPROVEMENT = "continuous_improvement"
    DOCUMENTATION = "documentation"
    VISUALIZATION = "visualization"
    DATA_ORGANIZATION = "data_organization"


class ToolType(str, Enum):
    SESSION_READER = "session_reader"
    OPERATION_READER = "operation_reader"
    FLOW_READER = "flow_reader"
    EXECUTION_READER = "execution_reader"
    DESKTOP_INSPECTOR = "desktop_inspector"
    LOG_ANALYZER = "log_analyzer"
    ERROR_TRACKER = "error_tracker"
    CODE_INSPECTOR = "code_inspector"
    FLOW_OPTIMIZER = "flow_optimizer"
    PATTERN_DETECTOR = "pattern_detector"
    METRICS_COLLECTOR = "metrics_collector"
    DOCUMENT_GENERATOR = "document_generator"
    VISUALIZATION_ENGINE = "visualization_engine"


class ToolParameter(BaseModel):
    name: str = ""
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None

    model_config = {"extra": "allow"}


class ToolDefinition(BaseModel):
    type: ToolType = ToolType.SESSION_READER
    name: str = ""
    description: str = ""
    parameters: list[ToolParameter] = Field(default_factory=list)
    required_permissions: list[Permission] = Field(default_factory=list)
    category: SkillCategory = SkillCategory.PROCESS_MAPPING

    model_config = {"extra": "allow"}


class AgentMessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class AgentMessage(BaseModel):
    role: AgentMessageRole = AgentMessageRole.USER
    content: str = ""
    timestamp: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ToolCall(BaseModel):
    call_id: str = ""
    tool_type: ToolType = ToolType.SESSION_READER
    parameters: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ToolResult(BaseModel):
    call_id: str = ""
    success: bool = False
    data: Any = None
    error: str | None = None
    execution_ms: float = 0.0

    model_config = {"extra": "allow"}


class ImprovementSuggestion(BaseModel):
    category: str = ""
    title: str = ""
    description: str = ""
    priority: str = "medium"
    impact: str = ""
    effort: str = ""

    model_config = {"extra": "allow"}


class RootCauseHypothesis(BaseModel):
    hypothesis: str = ""
    confidence: float = 0.0
    evidence: list[str] = Field(default_factory=list)
    recommendation: str = ""

    model_config = {"extra": "allow"}


class RootCauseAnalysis(BaseModel):
    summary: str = ""
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    timeline: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class ProcessMapNode(BaseModel):
    id: str = ""
    label: str = ""
    node_type: str = "step"
    description: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProcessMapEdge(BaseModel):
    source: str = ""
    target: str = ""
    label: str = ""
    edge_type: str = "sequence"
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class ProcessMap(BaseModel):
    name: str = ""
    description: str = ""
    nodes: list[ProcessMapNode] = Field(default_factory=list)
    edges: list[ProcessMapEdge] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)
    connections: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}


class WorkflowAssessment(BaseModel):
    flow_id: str = ""
    score: float = 0.0
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    suggestions: list[ImprovementSuggestion] = Field(default_factory=list)

    model_config = {"extra": "allow"}


class DebugTrace(BaseModel):
    step_index: int = 0
    step_id: str = ""
    action: str = ""
    result: str = ""
    error: str | None = None
    screenshot: str | None = None
    timestamp: float = 0.0

    model_config = {"extra": "allow"}


class DebugSession(BaseModel):
    execution_id: str = ""
    traces: list[DebugTrace] = Field(default_factory=list)
    root_cause: RootCauseAnalysis | None = None
    fix_applied: bool = False
    fix_description: str = ""

    model_config = {"extra": "allow"}


class AgentSession(BaseModel):
    id: str = ""
    permissions: list[Permission] = Field(default_factory=list)
    skills: list[SkillCategory] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: float = Field(default_factory=time.time)
    status: str = "active"

    model_config = {"extra": "allow"}


class AgentResponse(BaseModel):
    message: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    suggestions: list[ImprovementSuggestion] = Field(default_factory=list)
    process_map: ProcessMap | None = None
    root_cause_analysis: RootCauseAnalysis | None = None
    workflow_assessment: WorkflowAssessment | None = None
    debug_session: DebugSession | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
