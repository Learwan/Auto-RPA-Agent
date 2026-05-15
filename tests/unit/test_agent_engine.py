import pytest

from src.agent.engine import AgentEngine


class FakeRepository:
    async def get_execution(self, execution_id):
        return None

    async def get_automation(self, flow_id):
        return None

    async def get_operations(self, session_id):
        return []

    async def get_session(self, session_id):
        return None


class FakeLLMService:
    async def chat(self, messages, temperature=0.3, max_tokens=2048):
        return "这是一个直接回复，不需要工具。"


@pytest.mark.asyncio
async def test_agent_engine_process_message_does_not_crash_on_prompt_formatting():
    engine = AgentEngine(FakeRepository(), FakeLLMService())
    session = await engine.create_session()

    response = await engine.process_message(session.id, "调试执行失败", 1)

    assert "直接回复" in response.message
