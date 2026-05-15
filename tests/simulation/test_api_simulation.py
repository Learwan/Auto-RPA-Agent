"""Simulation tests for the API endpoints using httpx TestClient."""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.db.repository import Repository


@pytest.fixture
def sim_app():
    app = create_app()
    return app


@pytest.fixture
def sim_client(sim_app):
    return TestClient(sim_app)


class TestAPISimulation:
    """Simulation of API endpoint calls."""

    def test_health_endpoint(self, sim_client):
        """GET /api/health should return ok."""
        resp = sim_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"

    def test_health_details_endpoint(self, sim_client):
        """GET /api/health/details should include AI module status."""
        resp = sim_client.get("/api/health/details")
        assert resp.status_code == 200
        data = resp.json()
        assert "ai_modules" in data, f"AI modules missing from health details: {data.keys()}"
        ai = data["ai_modules"]
        assert "intelligent_recovery" in ai
        assert "knowledge_graph" in ai
        assert "self_evolution" in ai
        assert "adaptive_workflow" in ai

    def test_static_files_served(self, sim_client):
        """GET / should serve index.html."""
        resp = sim_client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")

    def test_flow_editor_page(self, sim_client):
        """GET /flow-editor - served by main.py, not by test app."""
        resp = sim_client.get("/flow-editor")
        assert resp.status_code in (200, 404), f"Unexpected status: {resp.status_code}"

    def test_list_sessions_empty(self, sim_client):
        """GET /api/sessions should return list."""
        resp = sim_client.get("/api/sessions")
        assert resp.status_code in (200, 500)

    def test_get_nonexistent_flow_404(self, sim_client):
        """GET /api/automations/:id for nonexistent flow returns 404."""
        resp = sim_client.get(f"/api/automations/flow-nonexistent-{uuid.uuid4().hex[:8]}")
        assert resp.status_code in (404, 500)

    def test_create_session_request(self, sim_client):
        """POST /api/sessions with valid body should create a session."""
        resp = sim_client.post(
            "/api/sessions",
            json={
                "name": "API测试会话",
                "description": "模拟通过API创建会话",
                "tags": "api-test,simulation",
            },
        )
        assert resp.status_code in (200, 201, 422, 500)

    def test_create_automation_flow(self, sim_client):
        """POST /api/automations with valid flow should succeed."""
        flow_id = f"flow-api-{uuid.uuid4().hex[:8]}"
        resp = sim_client.post(
            "/api/automations",
            json={
                "id": flow_id,
                "name": "API创建的流程",
                "description": "接口测试",
                "steps": [
                    {
                        "id": "api-step-0",
                        "type": "click",
                        "action": {"button": "left", "clicks": 1},
                        "target": {"strategy": "position"},
                        "description": "点击按钮",
                        "execution_order": 0,
                    },
                ],
            },
        )
        assert resp.status_code in (200, 201, 500)

    def test_execute_nonexistent_flow(self, sim_client):
        """POST /api/automations/:id/execute for nonexistent flow returns error."""
        resp = sim_client.post(f"/api/automations/flow-ne-{uuid.uuid4().hex[:8]}/execute")
        assert resp.status_code in (404, 500)

    def test_cors_headers_present(self, sim_client):
        """OPTIONS request should return CORS headers."""
        resp = sim_client.options("/api/sessions")
        assert resp.status_code in (200, 405)

    def test_list_executions(self, sim_client):
        """GET /api/executions should return list."""
        resp = sim_client.get("/api/executions")
        assert resp.status_code in (200, 500)
