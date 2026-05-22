from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from src.llm.ui_tars_adapter import UITarsAdapter


class TestUITarsAdapter:
    @pytest.mark.asyncio
    async def test_uses_remote_server_when_configured(self):
        adapter = UITarsAdapter(model_path="ui-tars-model", server_url="http://ui-tars.example/v1")

        fake_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="Thought: remote match\nAction: click(500, 300)"
                    )
                )
            ]
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=fake_response))
            )
        )

        with patch.object(UITarsAdapter, "client", new_callable=PropertyMock) as client_prop, \
             patch("src.llm.local_engine.LocalLLMEngine.get_instance", new=AsyncMock()) as local_get_instance:
            client_prop.return_value = fake_client
            result = await adapter.ground_action("abcd", "Click save", "click")

        assert result.found is True
        assert result.bbox_pixel == (500, 300, 1, 1)
        fake_client.chat.completions.create.assert_awaited_once()
        local_get_instance.assert_not_called()

    @pytest.mark.asyncio
    async def test_falls_back_to_local_when_remote_server_fails(self):
        adapter = UITarsAdapter(model_path="ui-tars-model", server_url="http://ui-tars.example/v1")
        fake_driver = SimpleNamespace(
            analyze_with_image=AsyncMock(
                return_value=SimpleNamespace(text='click(100, 200)')
            )
        )
        fake_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(side_effect=RuntimeError("remote unavailable")))
            )
        )

        with patch.object(UITarsAdapter, "client", new_callable=PropertyMock) as client_prop, \
             patch("src.llm.local_engine.LocalLLMEngine.get_instance", new=AsyncMock(return_value=fake_driver)) as local_get_instance:
            client_prop.return_value = fake_client
            result = await adapter.ground_action("abcd", "Click save", "click")

        assert result.found is True
        assert result.bbox_pixel == (100, 200, 1, 1)
        fake_client.chat.completions.create.assert_awaited_once()
        local_get_instance.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_local_only_mode_uses_local_runtime(self):
        adapter = UITarsAdapter(model_path="ui-tars-model", server_url="")
        fake_driver = SimpleNamespace(
            analyze_with_image=AsyncMock(
                return_value=SimpleNamespace(text='click(100, 200)')
            )
        )

        with patch("src.llm.local_engine.LocalLLMEngine.get_instance", new=AsyncMock(return_value=fake_driver)) as local_get_instance:
            result = await adapter.ground_action("abcd", "Click save", "click")

        assert result.found is True
        local_get_instance.assert_called_once()