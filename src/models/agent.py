from __future__ import annotations

from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Permission(str, Enum):
    READ_SESSIONS = "read_sessions"
    WRITE_SESSIONS = "write_sessions"
    READ_OPERATIONS = "read_operations"
    READ_FLOWS = "read_flows"
    WRITE_FLOWS = "write_flows"
    READ_EXECUTIONS = "read_executions"
    EXECUTE_FLOWS = "execute_flows"
    READ_DESKTOP = "read_desktop"
    READ_LOGS = "read_logs"
    ACCESS_CODEBASE = "access_codebase"
    ACCESS_FILESYSTEM = "access_filesystem"
    ANALYZE_DATA = "analyze_data"
    DEBUG_EXECUTIONS = "debug_executions"
    MODIFY_WORKFLOWS = "modify_workflows"


class SkillCategory(str, Enum):
    PROCESS_MAPPING = "process_mapping"
    ROOT_CAUSE_ANALYSIS = "root_cause_analysis"
    WORKFLOW_OPTIMIZATION = "workflow_optimization"
    CONTINUOUS_IMPROVEMENT = "continuous_improvement"
    DATA_ORGANIZATION = "data_organization"
    VISUALIZATION = "visualization"
    DOCUMENTATION = "documentation"
    DEBUGGING = "debugging"


class ToolType(str, Enum):
    SESSION_READER = "session_reader"
    OPERATION_READER = "operation_reader"
    FLOW_READER = "flow_reader"
    EXECUTION_READER = "execution_reader"
    DESKTOP_INSPECTOR = "desktop_inspector"
    LOG_ANALYZER = "log_analyzer"
    METRICS_COLLECTOR = "metrics_collector"
    PATTERN_DETECTOR = "pattern_detector"
    FLOW_OPTIMIZER = "flow_optimizer"
    CODE_INSPECTOR = "code_inspector"
    DOCUMENT_GENERATOR = "document_generator"
    VISUALIZATION_ENGINE = "visualization_engine"
    ERROR_TRACKER = "error_tracker"


class AgentMessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    SYSTEM = "system"


class ToolParameter(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    default: Any = None


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: ToolType
    name: str
    description: str = ""
    parameters: list[ToolParameter] = Field(default_factory=list)
    required_permissions: list[Permission] = Field(default_factory=list)
    category: SkillCategory = SkillCategory.PROCESS_MAPPING


class ToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    call_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    tool_type: ToolType
    parameters: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    call_id: str
    success: bool = True
    data: Any = None
    error: str | None = None
    execution_ms: float = 0.0


class AgentMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: AgentMessageRole = AgentMessageRole.USER
    content: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    timestamp: float = 0.0


class AgentSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex)
    granted_permissions: list[Permission] = Field(default_factory=list)
    active_skills: list[SkillCategory] = Field(default_factory=list)
    skills: list[SkillCategory] = Field(default_factory=list)
    messages: list[AgentMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProcessMapNode(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    label: str = ""
    type: str = "step"
    details: dict[str, Any] = Field(default_factory=dict)


class ProcessMapEdge(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: str
    target: str
    label: str = ""


class ProcessMap(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nodes: list[ProcessMapNode] = Field(default_factory=list)
    edges: list[ProcessMapEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RootCauseHypothesis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str = ""
    description: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class RootCauseAnalysis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    problem: str = ""
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list)
    root_cause: str | None = None
    contributing_factors: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    conclusion: str = ""


class ImprovementSuggestion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str
    description: str = ""
    category: str = "reliability"
    impact: str = "medium"


class WorkflowAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    flow_id: str
    flow_name: str = ""
    reliability_score: float = 0.0
    efficiency_score: float = 0.0
    process_map: ProcessMap | None = None
    recommendations: list[ImprovementSuggestion] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DebugTrace(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step_index: int = 0
    step_id: str = ""
    step_type: str = ""
    status: str = ""
    timestamp: float = 0.0
    message: str = ""
    evidence: list[str] = Field(default_factory=list)


class DebugSession(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(default_factory=lambda: uuid4().hex)
    execution_id: str
    traces: list[DebugTrace] = Field(default_factory=list)
    root_cause: RootCauseAnalysis | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
