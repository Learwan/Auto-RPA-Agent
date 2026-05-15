from src.models.agent import (
    Permission,
    SkillCategory,
    ToolDefinition,
    ToolParameter,
    ToolType,
)

TOOL_REGISTRY: dict[ToolType, ToolDefinition] = {
    ToolType.SESSION_READER: ToolDefinition(
        type=ToolType.SESSION_READER,
        name="Session Reader",
        description="Read and inspect recording sessions, including metadata, status, and operation counts",
        parameters=[
            ToolParameter(name="session_id", type="string", description="Specific session ID, or omit to list all"),
            ToolParameter(
                name="include_operations",
                type="boolean",
                description="Include full operation list",
                required=False,
                default=False,
            ),
        ],
        required_permissions=[Permission.READ_SESSIONS, Permission.READ_OPERATIONS],
        category=SkillCategory.PROCESS_MAPPING,
    ),
    ToolType.OPERATION_READER: ToolDefinition(
        type=ToolType.OPERATION_READER,
        name="Operation Reader",
        description="Read and analyze recorded operations within a session, with filtering and aggregation",
        parameters=[
            ToolParameter(name="session_id", type="string", description="Session ID to read operations from"),
            ToolParameter(
                name="operation_type",
                type="string",
                description="Filter by operation type (MOUSE_CLICK, KEY_INPUT, etc.)",
                required=False,
            ),
            ToolParameter(
                name="limit", type="integer", description="Max operations to return", required=False, default=100
            ),
        ],
        required_permissions=[Permission.READ_OPERATIONS],
        category=SkillCategory.PROCESS_MAPPING,
    ),
    ToolType.FLOW_READER: ToolDefinition(
        type=ToolType.FLOW_READER,
        name="Flow Reader",
        description="Read and inspect automation flows, including step details, confidence scores, and execution history",
        parameters=[
            ToolParameter(name="flow_id", type="string", description="Specific flow ID, or omit to list all"),
            ToolParameter(
                name="include_steps",
                type="boolean",
                description="Include full step details",
                required=False,
                default=True,
            ),
        ],
        required_permissions=[Permission.READ_FLOWS],
        category=SkillCategory.WORKFLOW_OPTIMIZATION,
    ),
    ToolType.EXECUTION_READER: ToolDefinition(
        type=ToolType.EXECUTION_READER,
        name="Execution Reader",
        description="Read execution records, step logs, verification results, and visual comparisons",
        parameters=[
            ToolParameter(
                name="execution_id", type="string", description="Specific execution ID, or omit to list recent"
            ),
            ToolParameter(
                name="include_logs", type="boolean", description="Include step-level logs", required=False, default=True
            ),
        ],
        required_permissions=[Permission.READ_EXECUTIONS],
        category=SkillCategory.DEBUGGING,
    ),
    ToolType.DESKTOP_INSPECTOR: ToolDefinition(
        type=ToolType.DESKTOP_INSPECTOR,
        name="Desktop Inspector",
        description="Inspect current desktop state: windows, processes, UI elements, and take screenshots",
        parameters=[
            ToolParameter(
                name="inspect_type",
                type="string",
                description="Type: 'windows', 'processes', 'active_element', 'state', 'screenshot'",
                required=False,
                default="state",
            ),
        ],
        required_permissions=[Permission.READ_DESKTOP],
        category=SkillCategory.DEBUGGING,
    ),
    ToolType.LOG_ANALYZER: ToolDefinition(
        type=ToolType.LOG_ANALYZER,
        name="Log Analyzer",
        description="Analyze application logs for errors, warnings, and patterns. Supports filtering and aggregation",
        parameters=[
            ToolParameter(
                name="log_type",
                type="string",
                description="Type: 'errors', 'warnings', 'execution', 'all'",
                required=False,
                default="errors",
            ),
            ToolParameter(
                name="time_range",
                type="string",
                description="Time range: '1h', '6h', '24h', '7d'",
                required=False,
                default="24h",
            ),
            ToolParameter(name="keyword", type="string", description="Filter by keyword", required=False),
        ],
        required_permissions=[Permission.READ_LOGS],
        category=SkillCategory.DEBUGGING,
    ),
    ToolType.ERROR_TRACKER: ToolDefinition(
        type=ToolType.ERROR_TRACKER,
        name="Error Tracker",
        description="Track and analyze execution errors, including failure patterns, error frequencies, and affected steps",
        parameters=[
            ToolParameter(
                name="execution_id", type="string", description="Specific execution to analyze", required=False
            ),
            ToolParameter(name="error_type", type="string", description="Filter by error type", required=False),
            ToolParameter(
                name="include_screenshots",
                type="boolean",
                description="Include screenshot paths",
                required=False,
                default=True,
            ),
        ],
        required_permissions=[Permission.DEBUG_EXECUTIONS, Permission.READ_EXECUTIONS],
        category=SkillCategory.ROOT_CAUSE_ANALYSIS,
    ),
    ToolType.CODE_INSPECTOR: ToolDefinition(
        type=ToolType.CODE_INSPECTOR,
        name="Code Inspector",
        description="Inspect automation flow definitions as code, review step configurations, and identify code-level issues",
        parameters=[
            ToolParameter(name="flow_id", type="string", description="Flow ID to inspect"),
            ToolParameter(
                name="format",
                type="string",
                description="Output format: 'json', 'python'",
                required=False,
                default="json",
            ),
        ],
        required_permissions=[Permission.ACCESS_CODEBASE, Permission.READ_FLOWS],
        category=SkillCategory.DEBUGGING,
    ),
    ToolType.FLOW_OPTIMIZER: ToolDefinition(
        type=ToolType.FLOW_OPTIMIZER,
        name="Flow Optimizer",
        description="Analyze and suggest optimizations for automation flows, including step reduction, reliability improvements, and error handling",
        parameters=[
            ToolParameter(name="flow_id", type="string", description="Flow ID to optimize"),
            ToolParameter(
                name="optimization_target",
                type="string",
                description="Target: 'reliability', 'speed', 'maintainability', 'all'",
                required=False,
                default="all",
            ),
        ],
        required_permissions=[Permission.ANALYZE_DATA, Permission.READ_FLOWS, Permission.MODIFY_WORKFLOWS],
        category=SkillCategory.WORKFLOW_OPTIMIZATION,
    ),
    ToolType.PATTERN_DETECTOR: ToolDefinition(
        type=ToolType.PATTERN_DETECTOR,
        name="Pattern Detector",
        description="Detect recurring patterns in operations, identify inefficiencies, and suggest automation opportunities",
        parameters=[
            ToolParameter(name="session_id", type="string", description="Session to analyze for patterns"),
            ToolParameter(
                name="min_occurrences",
                type="integer",
                description="Minimum pattern occurrences",
                required=False,
                default=2,
            ),
        ],
        required_permissions=[Permission.ANALYZE_DATA, Permission.READ_OPERATIONS],
        category=SkillCategory.CONTINUOUS_IMPROVEMENT,
    ),
    ToolType.METRICS_COLLECTOR: ToolDefinition(
        type=ToolType.METRICS_COLLECTOR,
        name="Metrics Collector",
        description="Collect and aggregate workflow metrics: execution times, success rates, step durations, and trend data",
        parameters=[
            ToolParameter(
                name="flow_id", type="string", description="Flow ID for metrics, or omit for global", required=False
            ),
            ToolParameter(
                name="metric_type",
                type="string",
                description="Type: 'execution', 'reliability', 'performance', 'all'",
                required=False,
                default="all",
            ),
        ],
        required_permissions=[Permission.ANALYZE_DATA, Permission.READ_EXECUTIONS],
        category=SkillCategory.CONTINUOUS_IMPROVEMENT,
    ),
    ToolType.DOCUMENT_GENERATOR: ToolDefinition(
        type=ToolType.DOCUMENT_GENERATOR,
        name="Document Generator",
        description="Generate workflow documentation, process descriptions, and change logs",
        parameters=[
            ToolParameter(name="flow_id", type="string", description="Flow ID to document"),
            ToolParameter(
                name="doc_type",
                type="string",
                description="Type: 'process', 'technical', 'changelog', 'full'",
                required=False,
                default="full",
            ),
        ],
        required_permissions=[Permission.READ_FLOWS, Permission.READ_EXECUTIONS],
        category=SkillCategory.DOCUMENTATION,
    ),
    ToolType.VISUALIZATION_ENGINE: ToolDefinition(
        type=ToolType.VISUALIZATION_ENGINE,
        name="Visualization Engine",
        description="Generate process maps, flow diagrams, and visual representations of workflows and execution data",
        parameters=[
            ToolParameter(name="target_id", type="string", description="Flow ID or execution ID to visualize"),
            ToolParameter(
                name="viz_type",
                type="string",
                description="Type: 'process_map', 'execution_timeline', 'error_heatmap', 'metrics_chart'",
                required=False,
                default="process_map",
            ),
        ],
        required_permissions=[Permission.ANALYZE_DATA, Permission.READ_FLOWS],
        category=SkillCategory.VISUALIZATION,
    ),
}

SKILL_DEFINITIONS: dict[SkillCategory, dict] = {
    SkillCategory.PROCESS_MAPPING: {
        "name": "Process Mapping",
        "description": "Map and visualize operational workflows, identify process steps and their relationships",
        "tools": [ToolType.SESSION_READER, ToolType.OPERATION_READER, ToolType.VISUALIZATION_ENGINE],
    },
    SkillCategory.ROOT_CAUSE_ANALYSIS: {
        "name": "Root Cause Analysis",
        "description": "Systematically identify root causes of failures using evidence-based analysis",
        "tools": [ToolType.ERROR_TRACKER, ToolType.EXECUTION_READER, ToolType.LOG_ANALYZER, ToolType.CODE_INSPECTOR],
    },
    SkillCategory.WORKFLOW_OPTIMIZATION: {
        "name": "Workflow Optimization",
        "description": "Analyze and improve workflow efficiency, reliability, and maintainability",
        "tools": [ToolType.FLOW_READER, ToolType.FLOW_OPTIMIZER, ToolType.METRICS_COLLECTOR],
    },
    SkillCategory.CONTINUOUS_IMPROVEMENT: {
        "name": "Continuous Improvement",
        "description": "Identify improvement opportunities through pattern analysis and metrics tracking",
        "tools": [ToolType.PATTERN_DETECTOR, ToolType.METRICS_COLLECTOR, ToolType.FLOW_OPTIMIZER],
    },
    SkillCategory.DEBUGGING: {
        "name": "Debugging & Diagnostics",
        "description": "Debug execution failures, inspect desktop state, and trace errors",
        "tools": [
            ToolType.EXECUTION_READER,
            ToolType.ERROR_TRACKER,
            ToolType.DESKTOP_INSPECTOR,
            ToolType.LOG_ANALYZER,
            ToolType.CODE_INSPECTOR,
        ],
    },
    SkillCategory.DOCUMENTATION: {
        "name": "Documentation",
        "description": "Generate comprehensive workflow documentation and process descriptions",
        "tools": [ToolType.FLOW_READER, ToolType.DOCUMENT_GENERATOR, ToolType.VISUALIZATION_ENGINE],
    },
    SkillCategory.VISUALIZATION: {
        "name": "Visualization",
        "description": "Create visual representations of workflows, execution data, and analysis results",
        "tools": [ToolType.VISUALIZATION_ENGINE, ToolType.METRICS_COLLECTOR],
    },
    SkillCategory.DATA_ORGANIZATION: {
        "name": "Data Organization",
        "description": "Organize and structure workflow data, sessions, and execution records",
        "tools": [ToolType.SESSION_READER, ToolType.OPERATION_READER, ToolType.FLOW_READER, ToolType.METRICS_COLLECTOR],
    },
}

DEFAULT_PERMISSIONS: list[Permission] = [
    Permission.READ_SESSIONS,
    Permission.READ_OPERATIONS,
    Permission.READ_FLOWS,
    Permission.READ_EXECUTIONS,
    Permission.READ_DESKTOP,
    Permission.READ_LOGS,
    Permission.ANALYZE_DATA,
]

ELEVATED_PERMISSIONS: list[Permission] = DEFAULT_PERMISSIONS + [
    Permission.DEBUG_EXECUTIONS,
    Permission.ACCESS_CODEBASE,
    Permission.ACCESS_FILESYSTEM,
]

ADMIN_PERMISSIONS: list[Permission] = ELEVATED_PERMISSIONS + [
    Permission.WRITE_SESSIONS,
    Permission.WRITE_FLOWS,
    Permission.EXECUTE_FLOWS,
    Permission.MODIFY_WORKFLOWS,
]
