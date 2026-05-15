from types import SimpleNamespace

from src.config import Settings, SettingsProxy
from src.models.hotkey import OpenAICompatibleLLMConfig, UserSettings


def test_settings_proxy_uses_remote_llm_override_when_enabled(monkeypatch):
    monkeypatch.setattr(
        "src.config.load_settings",
        lambda: Settings(
            LLM_BASE_URL="https://env.example/v1",
            LLM_API_KEY="env-key",
            LLM_MODEL="env-model",
            LLM_TEMPERATURE=0.7,
            LLM_TOP_P=0.8,
            LLM_MAX_TOKENS=2048,
        ),
    )
    monkeypatch.setattr(
        "src.settings_store.SettingsStore.get_instance",
        lambda: SimpleNamespace(
            get_settings=lambda: UserSettings(
                remote_llm=OpenAICompatibleLLMConfig(
                    enabled=True,
                    base_url="https://override.example/v1",
                    api_key="override-key",
                    model="override-model",
                    temperature=0.2,
                    top_p=0.9,
                    max_tokens=8192,
                )
            )
        ),
    )

    proxy = SettingsProxy()

    assert proxy.LLM_BASE_URL == "https://override.example/v1"
    assert proxy.LLM_API_KEY == "override-key"
    assert proxy.LLM_MODEL == "override-model"
    assert proxy.LLM_TEMPERATURE == 0.2
    assert proxy.LLM_TOP_P == 0.9
    assert proxy.LLM_MAX_TOKENS == 8192
    assert proxy.llm_configured is True


def test_settings_proxy_falls_back_to_env_when_remote_llm_disabled(monkeypatch):
    monkeypatch.setattr(
        "src.config.load_settings",
        lambda: Settings(
            LLM_BASE_URL="https://env.example/v1",
            LLM_API_KEY="env-key",
            LLM_MODEL="env-model",
            LLM_TEMPERATURE=0.7,
            LLM_TOP_P=0.8,
            LLM_MAX_TOKENS=2048,
        ),
    )
    monkeypatch.setattr(
        "src.settings_store.SettingsStore.get_instance",
        lambda: SimpleNamespace(get_settings=lambda: UserSettings()),
    )

    proxy = SettingsProxy()

    assert proxy.LLM_BASE_URL == "https://env.example/v1"
    assert proxy.LLM_API_KEY == "env-key"
    assert proxy.LLM_MODEL == "env-model"
    assert proxy.LLM_TEMPERATURE == 0.7
    assert proxy.LLM_TOP_P == 0.8
    assert proxy.LLM_MAX_TOKENS == 2048
    assert proxy.llm_configured is True