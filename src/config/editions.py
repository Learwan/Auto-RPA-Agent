from __future__ import annotations

from enum import StrEnum


class Edition(StrEnum):
    COMMUNITY = "community"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class Feature(StrEnum):
    RECORDING = "recording"
    BASIC_WORKFLOW = "basic_workflow"
    PATTERN_DETECTION = "pattern_detection"
    SCRIPT_GENERATION = "script_generation"
    LOCAL_EXECUTION = "local_execution"
    VISUAL_EDITOR = "visual_editor"
    UI_TARS_GROUNDING = "ui_tars_grounding"
    SEMANTIC_SEGMENTATION = "semantic_segmentation"
    BEHAVIOR_TREE = "behavior_tree"
    WEB_IDE = "web_ide"
    STEP_VERIFIER = "step_verifier"
    MARKETPLACE_ACCESS = "marketplace_access"
    COMMUNITY_TEMPLATES = "community_templates"
    MULTI_RECORDING_FUSION = "multi_recording_fusion"
    RL_OPTIMIZATION = "rl_optimization"
    LOCAL_LLM_INFERENCE = "local_llm_inference"
    CLOUD_LLM_INFERENCE = "cloud_llm_inference"
    RBAC = "rbac"
    AUDIT_LOG = "audit_log"
    CREDENTIAL_VAULT = "credential_vault"
    SCHEDULER = "scheduler"
    NOTIFICATION_WEBHOOK = "notification_webhook"
    NOTIFICATION_EMAIL = "notification_email"
    CUSTOM_PLUGINS = "custom_plugins"
    API_ACCESS = "api_access"
    PRIORITY_SUPPORT = "priority_support"
    SSO = "sso"
    CUSTOM_BRANDING = "custom_branding"
    ON_PREMISE_DEPLOY = "on_premise_deploy"
    UNLIMITED_EXECUTIONS = "unlimited_executions"
    TEAM_COLLABORATION = "team_collaboration"
    ADVANCED_ANALYTICS = "advanced_analytics"


EDITION_FEATURES: dict[Edition, set[Feature]] = {
    Edition.COMMUNITY: {
        Feature.RECORDING,
        Feature.BASIC_WORKFLOW,
        Feature.PATTERN_DETECTION,
        Feature.SCRIPT_GENERATION,
        Feature.LOCAL_EXECUTION,
        Feature.VISUAL_EDITOR,
        Feature.SEMANTIC_SEGMENTATION,
        Feature.BEHAVIOR_TREE,
        Feature.MARKETPLACE_ACCESS,
        Feature.COMMUNITY_TEMPLATES,
        Feature.LOCAL_LLM_INFERENCE,
        Feature.NOTIFICATION_WEBHOOK,
        Feature.API_ACCESS,
    },
    Edition.PRO: {
        Feature.RECORDING,
        Feature.BASIC_WORKFLOW,
        Feature.PATTERN_DETECTION,
        Feature.SCRIPT_GENERATION,
        Feature.LOCAL_EXECUTION,
        Feature.VISUAL_EDITOR,
        Feature.UI_TARS_GROUNDING,
        Feature.SEMANTIC_SEGMENTATION,
        Feature.BEHAVIOR_TREE,
        Feature.WEB_IDE,
        Feature.STEP_VERIFIER,
        Feature.MARKETPLACE_ACCESS,
        Feature.COMMUNITY_TEMPLATES,
        Feature.MULTI_RECORDING_FUSION,
        Feature.RL_OPTIMIZATION,
        Feature.LOCAL_LLM_INFERENCE,
        Feature.CLOUD_LLM_INFERENCE,
        Feature.CREDENTIAL_VAULT,
        Feature.SCHEDULER,
        Feature.NOTIFICATION_WEBHOOK,
        Feature.NOTIFICATION_EMAIL,
        Feature.API_ACCESS,
        Feature.UNLIMITED_EXECUTIONS,
        Feature.TEAM_COLLABORATION,
        Feature.ADVANCED_ANALYTICS,
    },
    Edition.ENTERPRISE: {f for f in Feature},
}


EDITION_LIMITS: dict[Edition, dict] = {
    Edition.COMMUNITY: {
        "max_workflows": 10,
        "max_steps_per_workflow": 50,
        "max_executions_per_day": 100,
        "max_team_members": 1,
        "max_recording_duration_min": 30,
        "cloud_llm_tokens_per_month": 0,
        "support_level": "community",
    },
    Edition.PRO: {
        "max_workflows": 100,
        "max_steps_per_workflow": 500,
        "max_executions_per_day": 1000,
        "max_team_members": 10,
        "max_recording_duration_min": 120,
        "cloud_llm_tokens_per_month": 500000,
        "support_level": "priority",
    },
    Edition.ENTERPRISE: {
        "max_workflows": -1,
        "max_steps_per_workflow": -1,
        "max_executions_per_day": -1,
        "max_team_members": -1,
        "max_recording_duration_min": -1,
        "cloud_llm_tokens_per_month": -1,
        "support_level": "dedicated",
    },
}

EDITION_PRICING: dict[Edition, dict] = {
    Edition.COMMUNITY: {"monthly_usd": 0, "annual_usd": 0, "label": "Free"},
    Edition.PRO: {"monthly_usd": 29, "annual_usd": 290, "label": "Pro"},
    Edition.ENTERPRISE: {"monthly_usd": None, "annual_usd": None, "label": "Enterprise (Contact Sales)"},
}


def has_feature(edition: Edition, feature: Feature) -> bool:
    return feature in EDITION_FEATURES.get(edition, set())


def get_limit(edition: Edition, limit_name: str) -> int | float | str:
    limits = EDITION_LIMITS.get(edition, {})
    return limits.get(limit_name, 0)


def check_execution_limit(edition: Edition, current_count: int) -> bool:
    limit = get_limit(edition, "max_executions_per_day")
    if limit == -1:
        return True
    return current_count < limit


def check_workflow_limit(edition: Edition, current_count: int) -> bool:
    limit = get_limit(edition, "max_workflows")
    if limit == -1:
        return True
    return current_count < limit
