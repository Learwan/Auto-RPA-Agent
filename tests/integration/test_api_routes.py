from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.analyzer.closure_assessor import FlowClosureAssessment, FlowClosureError
from src.analyzer.confidence_scorer import DimensionScore, ImprovementSuggestion, ScoredFlow
from src.api.app import create_app
from src.models.automation import (
    AutomationFlow,
    AutomationStep,
    LocateStrategy,
    StepCondition,
    StepTarget,
    StepType,
)
from src.models.hotkey import OpenAICompatibleLLMConfig, UserSettings
from src.models.session import Session, SessionStatus


def _client():
    app = create_app()
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_check(self):
        client = _client()
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_details(self):
        client = _client()
        resp = client.get("/api/health/details")
        assert resp.status_code == 200
        data = resp.json()
        assert "grounding_engine" in data
        assert "grounding_runtime" in data
        assert "recorders" in data

    def test_vision_status_includes_grounding_runtime(self):
        client = _client()
        resp = client.get("/api/vision/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "grounding" in data
        assert "preferred_backend" in data["grounding"]
        assert "status" in data["grounding"]

    def test_startup_check(self, monkeypatch):
        class _FakeStore:
            def initialize(self, _data_dir):
                return None

            def get_settings(self):
                return UserSettings(
                    remote_llm=OpenAICompatibleLLMConfig(
                        enabled=True,
                        base_url="https://llm.example/v1",
                        api_key="super-secret-key",
                        model="gpt-4o-mini",
                    )
                )

        monkeypatch.setattr("src.settings_store.SettingsStore.get_instance", lambda: _FakeStore())

        client = _client()
        resp = client.get("/api/health/startup-check")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["summary"]["total"] >= 1
        assert data["effective_llm"]["source"] == "settings"
        assert data["effective_llm"]["remote"]["configured"] is True
        assert data["effective_llm"]["remote"]["api_key_masked"].startswith("supe")
        assert "grounding" in data["effective_llm"]["vision"]
        assert any(section["id"] == "ai" for section in data["sections"])


class TestAutomationExecutionRoutes:
    def test_execute_route_accepts_ai_options(self, monkeypatch):
        captured = {}

        async def fake_start_execution_async(flow_id, variables=None, ai_options=None):
            captured["flow_id"] = flow_id
            captured["variables"] = variables
            captured["ai_options"] = ai_options
            return "exec-ai-options"

        monkeypatch.setattr(
            "src.executor.execution_service.ExecutionService.get_instance",
            lambda: SimpleNamespace(start_execution_async=fake_start_execution_async),
        )

        client = _client()
        resp = client.post(
            "/api/automations/flow-ai-options/execute?async_exec=true",
            json={
                "variables": {"query": "hello"},
                "ai_options": {
                    "enabled": True,
                    "check_enabled": True,
                    "assist_enabled": True,
                    "summary_enabled": True,
                    "mode": "smart",
                },
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["execution_id"] == "exec-ai-options"
        assert captured["flow_id"] == "flow-ai-options"
        assert captured["variables"] == {"query": "hello"}
        assert captured["ai_options"]["enabled"] is True

    def test_execute_route_blocks_uncertified_flow(self, monkeypatch):
        async def fake_start_execution_async(flow_id, variables=None, ai_options=None):
            raise FlowClosureError(
                FlowClosureAssessment(
                    ready=False,
                    status="needs_review",
                    score=0.42,
                    summary="流程仍有 2 个关键闭环风险，已禁止正式执行。",
                    metrics={"blocking_issue_count": 2},
                    issues=[
                        {
                            "severity": "critical",
                            "code": "position_only_target",
                            "message": "步骤仍主要依赖坐标定位，界面轻微变化就会失效。",
                            "step_id": "step-1",
                            "step_index": 0,
                        }
                    ],
                )
            )

        monkeypatch.setattr(
            "src.executor.execution_service.ExecutionService.get_instance",
            lambda: SimpleNamespace(start_execution_async=fake_start_execution_async),
        )

        client = _client()
        resp = client.post("/api/automations/flow-weak/execute?async_exec=true", json={})

        assert resp.status_code == 409
        data = resp.json()
        assert "assessment" in data["detail"]
        assert data["detail"]["assessment"]["ready"] is False
        assert data["detail"]["assessment"]["issues"][0]["code"] == "position_only_target"

    def test_list_automations_backfills_closure_assessment_for_legacy_flows(self, monkeypatch):
        legacy_flow = AutomationFlow(
            id="legacy-flow",
            name="Legacy flow",
            metadata={},
            steps=[
                AutomationStep(
                    id="step-1",
                    type=StepType.CLICK,
                    target=StepTarget(
                        strategy=LocateStrategy.POSITION,
                        position={"x": 12, "y": 34},
                    ),
                )
            ],
        )

        class _FakeRepo:
            async def list_automations(self, limit, offset):
                return [legacy_flow]

        monkeypatch.setattr("src.api.routes.automations._get_repo", lambda db=None: _FakeRepo())

        client = _client()
        resp = client.get("/api/automations")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload[0]["closed_loop_assessment"]["ready"] is False
        assert payload[0]["closed_loop_assessment"]["issues"][0]["code"] == "position_only_target"
        assert not any(
            issue["code"] == "missing_preconditions"
            for issue in payload[0]["closed_loop_assessment"]["issues"]
        )
        assert not any(
            issue["code"] == "missing_postconditions"
            for issue in payload[0]["closed_loop_assessment"]["issues"]
        )
        assert payload[0]["metadata"]["closed_loop_ready"] is False

    def test_get_automation_backfills_closure_assessment_for_legacy_flows(self, monkeypatch):
        legacy_flow = AutomationFlow(
            id="legacy-flow",
            name="Legacy flow",
            metadata={},
            steps=[
                AutomationStep(
                    id="step-1",
                    type=StepType.CLICK,
                    target=StepTarget(
                        strategy=LocateStrategy.POSITION,
                        position={"x": 12, "y": 34},
                    ),
                )
            ],
        )

        class _FakeRepo:
            async def get_automation_flow(self, flow_id):
                return legacy_flow if flow_id == "legacy-flow" else None

        monkeypatch.setattr("src.api.routes.automations._get_repo", lambda db=None: _FakeRepo())

        client = _client()
        resp = client.get("/api/automations/legacy-flow")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["closed_loop_assessment"]["ready"] is False
        assert payload["closed_loop_assessment"]["issues"][0]["code"] == "position_only_target"
        assert not any(
            issue["code"] == "missing_preconditions"
            for issue in payload["closed_loop_assessment"]["issues"]
        )
        assert not any(
            issue["code"] == "missing_postconditions"
            for issue in payload["closed_loop_assessment"]["issues"]
        )
        assert payload["metadata"]["closed_loop_ready"] is False


class TestSettingsRoutes:
    def test_update_settings_supports_remote_llm_config(self, monkeypatch):
        class _FakeStore:
            def __init__(self):
                self._settings = UserSettings()

            def initialize(self, _data_dir):
                return None

            def get_settings(self):
                return self._settings

            def update_settings(self, updates: dict):
                data = self._settings.model_dump()
                for key, value in updates.items():
                    if isinstance(value, dict) and isinstance(data.get(key), dict):
                        data[key].update(value)
                    else:
                        data[key] = value
                self._settings = UserSettings.model_validate(data)
                return self._settings

        monkeypatch.setattr("src.settings_store.SettingsStore.get_instance", lambda: _FakeStore())

        client = _client()
        resp = client.put(
            "/api/settings",
            json={
                "updates": {
                    "remote_llm": {
                        "enabled": True,
                        "base_url": "https://llm.example/v1",
                        "api_key": "sk-test-123",
                        "model": "gpt-4o-mini",
                        "temperature": 0.2,
                        "top_p": 0.9,
                        "max_tokens": 4096,
                    }
                }
            },
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["remote_llm"]["enabled"] is True
        assert data["remote_llm"]["base_url"] == "https://llm.example/v1"
        assert data["remote_llm"]["model"] == "gpt-4o-mini"


class TestAuthRoutes:
    def test_create_user(self):
        client = _client()
        resp = client.post("/api/auth/users", params={"username": "testuser", "role": "editor"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "testuser"
        assert data["role"] == "editor"

    def test_list_users(self):
        client = _client()
        client.post("/api/auth/users", params={"username": "listuser", "role": "viewer"})
        resp = client.get("/api/auth/users")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_check_permission(self):
        client = _client()
        create_resp = client.post("/api/auth/users", params={"username": "permuser", "role": "admin"})
        user_id = create_resp.json()["user_id"]
        resp = client.get(f"/api/auth/permissions/{user_id}/manage_users")
        assert resp.status_code == 200
        assert resp.json()["granted"] is True


class TestSchedulerRoutes:
    def test_create_schedule(self):
        client = _client()
        resp = client.post(
            "/api/scheduler/schedules",
            params={
                "automation_id": "auto-1",
                "name": "Test Schedule",
                "trigger_type": "cron",
                "cron_expression": "0 9 * * *",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Schedule"

    def test_list_schedules(self):
        client = _client()
        client.post(
            "/api/scheduler/schedules",
            params={
                "automation_id": "auto-2",
                "name": "List Test",
            },
        )
        resp = client.get("/api/scheduler/schedules")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestMarketplaceRoutes:
    def test_search_templates(self):
        client = _client()
        resp = client.get("/api/marketplace/templates")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_categories(self):
        client = _client()
        resp = client.get("/api/marketplace/categories")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestAuditRoutes:
    def test_query_audit_log(self):
        client = _client()
        resp = client.get("/api/auth/audit")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


class TestSessionStartRoute:
    def test_start_recording_passes_auto_mode_for_hybrid_recording(self, monkeypatch):
        captured = {}

        class FakeRecordingService:
            async def start_session(self, session_id: str, mode: str = "desktop", options: dict | None = None):
                captured["session_id"] = session_id
                captured["mode"] = mode
                captured["options"] = options
                return Session(
                    id=session_id,
                    name="Auto Session",
                    status=SessionStatus.RECORDING,
                )

        monkeypatch.setattr(
            "src.recorder.service.RecordingService.get_instance",
            lambda: FakeRecordingService(),
        )

        client = _client()
        resp = client.post("/api/sessions/session-auto-1/start?mode=auto&hud=false&hud_ai_assist=true")

        assert resp.status_code == 200
        assert captured == {
            "session_id": "session-auto-1",
            "mode": "auto",
            "options": {
                "hud": False,
                "hud_ai_assist": True,
            },
        }

    def test_start_recording_passes_hud_options_for_desktop(self, monkeypatch):
        captured = {}

        class FakeRecordingService:
            async def start_session(self, session_id: str, mode: str = "desktop", options: dict | None = None):
                captured["session_id"] = session_id
                captured["mode"] = mode
                captured["options"] = options
                return Session(
                    id=session_id,
                    name="HUD Session",
                    status=SessionStatus.RECORDING,
                )

        monkeypatch.setattr(
            "src.recorder.service.RecordingService.get_instance",
            lambda: FakeRecordingService(),
        )

        client = _client()
        resp = client.post("/api/sessions/session-hud-1/start?mode=desktop&hud=false&hud_ai_assist=true")

        assert resp.status_code == 200
        assert captured == {
            "session_id": "session-hud-1",
            "mode": "desktop",
            "options": {
                "hud": False,
                "hud_ai_assist": True,
            },
        }


class TestSessionCopilotRoute:
    def test_session_copilot_returns_personal_workflow_summary(self, monkeypatch):
        async def fake_build_session_brief(self, session_id: str, min_confidence: float = 0.3):
            assert session_id == "session-copilot-1"
            assert min_confidence == 0.0
            return {
                "session": {
                    "id": session_id,
                    "name": "个人报销录制",
                    "status": "stopped",
                    "operation_count": 18,
                    "duration_ms": 42000,
                    "tags": [],
                },
                "operation_summary": {
                    "total_operations": 18,
                    "primary_app": "Google Chrome",
                    "type_distribution": {"mouse_click": 8, "key_input": 5},
                },
                "candidate_flows": [],
                "top_flow": {
                    "id": "flow-copilot-1",
                    "name": "填写报销并提交",
                    "confidence": 0.88,
                    "steps_count": 6,
                    "variables_count": 2,
                    "parameter_candidates": [
                        {"name": "amount", "type": "string", "required": True, "description": "报销金额"},
                    ],
                    "risk_steps": [
                        {
                            "step_id": "step-risk-1",
                            "step_type": "click",
                            "description": "点击提交按钮",
                            "locator_strategy": "position",
                            "issues": ["依赖坐标定位"],
                            "recommendations": ["改成可复用的语义定位"],
                        }
                    ],
                    "semantic_intent": {"intent": {"overall": "填写报销并提交审批"}},
                    "checkpoint_summary": {"steps_with_checkpoints": 1},
                    "ai_analysis": {"summary": "已有 AI 分析"},
                    "steps_preview": [],
                },
                "copilot_summary": {
                    "mode": "rule_based",
                    "summary": "该会话已经提炼出主流程，建议先处理提交按钮的脆弱定位。",
                    "recommended_next_actions": ["把金额和事由参数化", "为提交结果增加校验"],
                    "stabilization_targets": [
                        {
                            "step_id": "step-risk-1",
                            "step_type": "click",
                            "description": "点击提交按钮",
                            "locator_strategy": "position",
                            "issues": ["依赖坐标定位"],
                            "recommendations": ["改成可复用的语义定位"],
                        }
                    ],
                    "parameter_candidates": [
                        {"name": "amount", "type": "string", "required": True, "description": "报销金额"},
                    ],
                },
            }

        monkeypatch.setattr(
            "src.analyzer.personal_workflow_copilot.PersonalWorkflowCopilotService.build_session_brief",
            fake_build_session_brief,
        )

        client = _client()
        resp = client.post("/api/sessions/session-copilot-1/copilot?min_confidence=0.0")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session"]["name"] == "个人报销录制"
        assert data["operation_summary"]["primary_app"] == "Google Chrome"
        assert data["top_flow"]["name"] == "填写报销并提交"
        assert data["copilot_summary"]["mode"] == "rule_based"
        assert data["copilot_summary"]["recommended_next_actions"] == ["把金额和事由参数化", "为提交结果增加校验"]
        assert data["copilot_summary"]["stabilization_targets"][0]["locator_strategy"] == "position"

    def test_start_recording_preserves_browser_options(self, monkeypatch):
        captured = {}

        class FakeRecordingService:
            async def start_session(self, session_id: str, mode: str = "desktop", options: dict | None = None):
                captured["session_id"] = session_id
                captured["mode"] = mode
                captured["options"] = options
                return Session(
                    id=session_id,
                    name="Browser Session",
                    status=SessionStatus.RECORDING,
                )

        monkeypatch.setattr(
            "src.recorder.service.RecordingService.get_instance",
            lambda: FakeRecordingService(),
        )

        client = _client()
        resp = client.post(
            "/api/sessions/session-web-1/start?mode=browser&url=https://example.com&browser=chrome&headless=true&hud=false&hud_ai_assist=true"
        )

        assert resp.status_code == 200
        assert captured == {
            "session_id": "session-web-1",
            "mode": "web",
            "options": {
                "url": "https://example.com",
                "browser": "chromium",
                "headless": True,
            },
        }

    def test_toggle_hud_interaction_route(self, monkeypatch):
        captured = {}

        class FakeRecordingService:
            async def toggle_hud_interaction(self, session_id: str):
                captured["session_id"] = session_id
                return True

        monkeypatch.setattr(
            "src.recorder.service.RecordingService.get_instance",
            lambda: FakeRecordingService(),
        )

        client = _client()
        resp = client.post("/api/sessions/session-hud-2/hud/toggle")

        assert resp.status_code == 200
        assert resp.json() == {"session_id": "session-hud-2", "toggled": True}
        assert captured == {"session_id": "session-hud-2"}

    def test_capture_focus_context_route(self, monkeypatch):
        captured = {}

        class FakeRecordingService:
            async def capture_focus_context(
                self,
                session_id: str,
                include_vision: bool = True,
                include_llm: bool = True,
            ):
                captured["session_id"] = session_id
                captured["include_vision"] = include_vision
                captured["include_llm"] = include_llm
                return {
                    "session_id": session_id,
                    "snapshot_id": "snap-focus-1",
                    "focus_summary": "当前窗口: Chrome · 编辑商品",
                }

        monkeypatch.setattr(
            "src.recorder.service.RecordingService.get_instance",
            lambda: FakeRecordingService(),
        )

        client = _client()
        resp = client.post("/api/sessions/session-hud-3/focus-context/capture?include_vision=false&include_llm=true")

        assert resp.status_code == 200
        assert resp.json() == {
            "session_id": "session-hud-3",
            "snapshot_id": "snap-focus-1",
            "focus_summary": "当前窗口: Chrome · 编辑商品",
        }
        assert captured == {
            "session_id": "session-hud-3",
            "include_vision": False,
            "include_llm": True,
        }


class TestSessionAnalyzeRoute:
    def test_analyze_session_skips_llm_by_default(self, monkeypatch):
        async def fake_analyze_session(self, session_id: str, min_confidence: float = 0.5):
            assert session_id == "session-fast-1"
            assert min_confidence == 0.0
            return []

        def fail_if_called():
            raise AssertionError("LLMService should not be constructed for default analyze route")

        monkeypatch.setattr("src.analyzer.service.AnalysisService.analyze_session", fake_analyze_session)
        monkeypatch.setattr("src.llm.service.LLMService", fail_if_called)
        monkeypatch.setattr(
            "src.executor.execution_service.ExecutionService.get_instance",
            lambda: SimpleNamespace(knowledge_graph=None),
        )

        client = _client()
        resp = client.post("/api/sessions/session-fast-1/analyze?min_confidence=0.0")

        assert resp.status_code == 200
        assert resp.json()["flows_found"] == 0

    def test_analyze_session_returns_ai_enhancement_fields(self, monkeypatch):
        flow = AutomationFlow(
            id="flow-ai-1",
            name="AI Enhanced Flow",
            description="AI refined flow",
            source_session_id="session-ai-1",
            metadata={
                "ai_analysis": {
                    "participated": True,
                    "summary": "AI 总结",
                    "confidence": 0.88,
                },
                "ai_enhancement_status": {
                    "enabled": True,
                    "attempts": 2,
                    "status": "success",
                    "reason": None,
                    "last_error": None,
                },
                "semantic_intent": {
                    "intent": {
                        "overall": "信息查询与录入",
                        "business_context": "general",
                    },
                    "variables": [{"name": "text_1", "type": "string"}],
                },
                "checkpoint_summary": {
                    "steps_with_checkpoints": 1,
                    "preconditions_added": 1,
                    "postconditions_added": 1,
                },
            },
            steps=[
                AutomationStep(
                    id="step-ai-1",
                    type=StepType.CLICK,
                    description="等待按钮可见后点击",
                    delay=1800,
                    retry_count=3,
                    target=StepTarget(
                        strategy=LocateStrategy.TEXT_MATCH,
                        title="Open",
                        text_contains="Open",
                        window_title="web",
                    ),
                    preconditions=[
                        StepCondition(field="element_exists", operator="eq", value=True, timeout_ms=6500)
                    ],
                    metadata={
                        "ai_analysis": {
                            "intent": "点击 Open 按钮进入下一步",
                            "ui_state": "界面上应可见 Open 按钮",
                            "risk": "按钮文案变化会导致失配",
                            "confidence": 0.78,
                        },
                        "semantic_annotation": {
                            "step_index": 0,
                            "business_term": "信息检索",
                            "technical_op": "mouse_click",
                            "confidence": 0.8,
                        },
                        "checkpoints": {
                            "pre": [{"field": "element_exists", "operator": "eq", "value": True}],
                            "post": [{"field": "page_stable", "operator": "truthy", "value": True}],
                        },
                        "postconditions": [
                            {
                                "field": "page_stable",
                                "operator": "truthy",
                                "value": True,
                                "timeout_ms": 2500,
                            }
                        ],
                    },
                )
            ],
        )
        scored = ScoredFlow(
            flow=flow,
            overall_confidence=0.91,
            dimensions=[
                DimensionScore(name="stability", score=0.9, details="stable"),
            ],
            suggestions=[
                ImprovementSuggestion(category="reliability", message="增加等待步骤", impact="medium"),
            ],
            is_reliable=True,
        )

        async def fake_analyze_session(self, session_id: str, min_confidence: float = 0.5):
            assert session_id == "session-ai-1"
            assert min_confidence == 0.2
            return [scored]

        monkeypatch.setattr("src.analyzer.service.AnalysisService.analyze_session", fake_analyze_session)
        monkeypatch.setattr(
            "src.executor.execution_service.ExecutionService.get_instance",
            lambda: SimpleNamespace(knowledge_graph=None),
        )

        client = _client()
        resp = client.post("/api/sessions/session-ai-1/analyze?min_confidence=0.2")

        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "session-ai-1"
        assert data["flows_found"] == 1
        assert data["llm_enhanced"] is True
        assert data["ai_enhancement"] == {
            "attempted_flows": 1,
            "successful_flows": 1,
            "failed_flows": 0,
        }

        returned_flow = data["flows"][0]
        assert returned_flow["id"] == "flow-ai-1"
        assert returned_flow["ai_analysis"]["summary"] == "AI 总结"
        assert returned_flow["ai_enhancement_status"]["status"] == "success"
        assert returned_flow["ai_enhancement_status"]["attempts"] == 2
        assert returned_flow["semantic_intent"]["intent"]["overall"] == "信息查询与录入"
        assert returned_flow["checkpoint_summary"]["steps_with_checkpoints"] == 1
        assert returned_flow["steps_count"] == 1

        returned_step = returned_flow["steps"][0]
        assert returned_step["type"] == "click"
        assert returned_step["description"] == "等待按钮可见后点击"
        assert returned_step["delay"] == 1800
        assert returned_step["retry_count"] == 3
        assert returned_step["target"]["window_title"] == "web"
        assert returned_step["preconditions"][0]["field"] == "element_exists"
        assert returned_step["preconditions"][0]["timeout_ms"] == 6500
        assert returned_step["ai_analysis"]["intent"] == "点击 Open 按钮进入下一步"
        assert returned_step["semantic_annotation"]["business_term"] == "信息检索"
        assert returned_step["checkpoints"]["post"][0]["field"] == "page_stable"
        assert returned_step["postconditions"][0]["field"] == "page_stable"
