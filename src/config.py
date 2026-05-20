from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATA_DIR: str = Field(default="data", validation_alias=AliasChoices("AUTO_AGENT_DATA_DIR", "DATA_DIR"))
    DATABASE_URL: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AUTO_AGENT_DB_URL", "AUTO_AGENT_DATABASE_URL", "DATABASE_URL"),
    )
    AUTO_MIGRATE: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_AUTO_MIGRATE", "AUTO_MIGRATE"),
    )
    SCREENSHOT_DIR: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AUTO_AGENT_SCREENSHOT_DIR", "SCREENSHOT_DIR"),
    )
    SCRIPT_DIR: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AUTO_AGENT_SCRIPT_DIR", "SCRIPT_DIR"),
    )
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000
    CORS_ALLOW_ORIGINS: str = Field(
        default="*",
        validation_alias=AliasChoices("AUTO_AGENT_CORS_ALLOW_ORIGINS", "CORS_ALLOW_ORIGINS"),
    )
    CORS_ALLOW_CREDENTIALS: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTO_AGENT_CORS_ALLOW_CREDENTIALS", "CORS_ALLOW_CREDENTIALS"),
    )

    RECORD_CAPTURE_INTERVAL_MS: int = 500
    RECORD_SCREENSHOT_ENABLED: bool = True
    RECORD_AUTO_FOCUS_CONTEXT_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AUTO_AGENT_RECORD_AUTO_FOCUS_CONTEXT_ENABLED",
            "RECORD_AUTO_FOCUS_CONTEXT_ENABLED",
        ),
    )
    RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS: int = Field(
        default=400,
        validation_alias=AliasChoices(
            "AUTO_AGENT_RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS",
            "RECORD_AUTO_FOCUS_CONTEXT_INTERVAL_MS",
        ),
    )
    RECORD_SENSITIVE_FILTER: bool = True
    RECORD_MOUSE_MOVE_ENABLED: bool = False
    RECORD_CLIPBOARD_INTERVAL_MS: int = 200
    RECORD_WINDOW_CHECK_INTERVAL_MS: int = 100
    RECORD_ENABLE_MOUSE: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_ENABLE_MOUSE", "RECORD_ENABLE_MOUSE"),
    )
    RECORD_ENABLE_KEYBOARD: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_ENABLE_KEYBOARD", "RECORD_ENABLE_KEYBOARD"),
    )
    RECORD_ENABLE_WINDOW: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_ENABLE_WINDOW", "RECORD_ENABLE_WINDOW"),
    )
    RECORD_ENABLE_CLIPBOARD: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_ENABLE_CLIPBOARD", "RECORD_ENABLE_CLIPBOARD"),
    )
    RECORD_ENABLE_FILESYSTEM: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_ENABLE_FILESYSTEM", "RECORD_ENABLE_FILESYSTEM"),
    )
    RECORD_HOTKEY_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_HOTKEY_ENABLED", "RECORD_HOTKEY_ENABLED"),
    )
    RECORD_AUDIO_FEEDBACK_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_AUDIO_FEEDBACK_ENABLED", "RECORD_AUDIO_FEEDBACK_ENABLED"),
    )
    RECORD_SYSTEM_NOTIFICATIONS_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "AUTO_AGENT_RECORD_SYSTEM_NOTIFICATIONS_ENABLED", "RECORD_SYSTEM_NOTIFICATIONS_ENABLED"
        ),
    )
    RECORD_FILESYSTEM_PATHS: str = Field(
        default="",
        validation_alias=AliasChoices("AUTO_AGENT_RECORD_FILESYSTEM_PATHS", "RECORD_FILESYSTEM_PATHS"),
    )

    EXEC_DEFAULT_DELAY_MS: int = 500
    EXEC_RETRY_COUNT: int = 3
    EXEC_RETRY_DELAY_MS: int = 1000
    EXEC_TIMEOUT_MS: int = 30000
    EXEC_SCREENSHOT_EACH_STEP: bool = True
    WEB_BROWSER: str = Field(
        default="chromium",
        validation_alias=AliasChoices("AUTO_AGENT_WEB_BROWSER", "WEB_BROWSER"),
    )
    WEB_HEADLESS: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_WEB_HEADLESS", "WEB_HEADLESS"),
    )
    WEB_TIMEOUT_MS: int = Field(
        default=10000,
        validation_alias=AliasChoices("AUTO_AGENT_WEB_TIMEOUT_MS", "WEB_TIMEOUT_MS"),
    )
    WEB_VIEWPORT_WIDTH: int = Field(
        default=1440,
        validation_alias=AliasChoices("AUTO_AGENT_WEB_VIEWPORT_WIDTH", "WEB_VIEWPORT_WIDTH"),
    )
    WEB_VIEWPORT_HEIGHT: int = Field(
        default=900,
        validation_alias=AliasChoices("AUTO_AGENT_WEB_VIEWPORT_HEIGHT", "WEB_VIEWPORT_HEIGHT"),
    )
    WEB_USE_MCP: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTO_AGENT_WEB_USE_MCP", "WEB_USE_MCP"),
    )

    ANALYZE_MIN_SUPPORT: int = 2
    ANALYZE_MIN_PATTERN_LENGTH: int = 3
    ANALYZE_MAX_GAP_MS: int = 30000
    ANALYZE_SIMILARITY_THRESHOLD: float = 0.8
    ANALYZE_SPATIAL_EPS: int = 20
    ANALYZE_SPATIAL_MIN_SAMPLES: int = 2

    LLM_BASE_URL: str = "https://integrate.api.nvidia.com/v1"
    LLM_API_KEY: str = ""
    LLM_MODEL: str = "minimaxai/minimax-m2.7"
    LLM_TEMPERATURE: float = 1.0
    LLM_TOP_P: float = 0.95
    LLM_MAX_TOKENS: int = 16384

    LOCAL_LLM_ENABLED: bool = Field(
        default=False,
        validation_alias=AliasChoices("AUTO_AGENT_LOCAL_LLM_ENABLED", "LOCAL_LLM_ENABLED"),
    )
    LOCAL_LLM_ENGINE: str = Field(
        default="mlx",
        validation_alias=AliasChoices("AUTO_AGENT_LOCAL_LLM_ENGINE", "LOCAL_LLM_ENGINE"),
    )
    LOCAL_LLM_MODEL: str = Field(
        default="models/qwen3.5-4b-4bit",
        validation_alias=AliasChoices("AUTO_AGENT_LOCAL_LLM_MODEL", "LOCAL_LLM_MODEL"),
    )
    LOCAL_LLM_BASE_URL: str = Field(
        default="http://localhost:8000/v1",
        validation_alias=AliasChoices("AUTO_AGENT_LOCAL_LLM_BASE_URL", "LOCAL_LLM_BASE_URL"),
    )
    LOCAL_LLM_TEMPERATURE: float = Field(
        default=0.2,
        validation_alias=AliasChoices("AUTO_AGENT_LOCAL_LLM_TEMPERATURE", "LOCAL_LLM_TEMPERATURE"),
    )
    LOCAL_LLM_MAX_TOKENS: int = Field(
        default=4096,
        validation_alias=AliasChoices("AUTO_AGENT_LOCAL_LLM_MAX_TOKENS", "LOCAL_LLM_MAX_TOKENS"),
    )
    LOCAL_LLM_CONTEXT_LENGTH: int = Field(
        default=32768,
        validation_alias=AliasChoices("AUTO_AGENT_LOCAL_LLM_CONTEXT_LENGTH", "LOCAL_LLM_CONTEXT_LENGTH"),
    )

    VISION_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_VISION_ENABLED", "VISION_ENABLED"),
    )
    VISION_GROUNDING_CONFIDENCE_THRESHOLD: float = Field(
        default=0.6,
        validation_alias=AliasChoices("AUTO_AGENT_VISION_GROUNDING_THRESHOLD", "VISION_GROUNDING_CONFIDENCE_THRESHOLD"),
    )
    VISION_REQUEST_TIMEOUT_MS: int = Field(
        default=60000,
        validation_alias=AliasChoices("AUTO_AGENT_VISION_TIMEOUT", "VISION_REQUEST_TIMEOUT_MS"),
    )
    VISION_MAX_CONCURRENT_REQUESTS: int = Field(
        default=1,
        validation_alias=AliasChoices("AUTO_AGENT_VISION_CONCURRENCY", "VISION_MAX_CONCURRENT_REQUESTS"),
    )
    VISION_CACHE_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_VISION_CACHE", "VISION_CACHE_ENABLED"),
    )
    VISION_CACHE_TTL_SECONDS: int = Field(
        default=300,
        validation_alias=AliasChoices("AUTO_AGENT_VISION_CACHE_TTL", "VISION_CACHE_TTL_SECONDS"),
    )

    GROUNDING_ENGINE: str = Field(
        default="qwen",
        validation_alias=AliasChoices("AUTO_AGENT_GROUNDING_ENGINE", "GROUNDING_ENGINE"),
    )
    UI_TARS_MODEL_PATH: str = Field(
        default="",
        validation_alias=AliasChoices("AUTO_AGENT_UI_TARS_MODEL_PATH", "UI_TARS_MODEL_PATH"),
    )
    UI_TARS_SERVER_URL: str = Field(
        default="",
        validation_alias=AliasChoices("AUTO_AGENT_UI_TARS_SERVER_URL", "UI_TARS_SERVER_URL"),
    )
    VAULT_KEY: str = Field(
        default="",
        validation_alias=AliasChoices("AUTO_AGENT_VAULT_KEY", "VAULT_KEY"),
    )

    COLLAB_ENABLED: bool = Field(
        default=True,
        validation_alias=AliasChoices("AUTO_AGENT_COLLAB_ENABLED", "COLLAB_ENABLED"),
    )
    COLLAB_MAX_USERS_PER_ROOM: int = Field(
        default=10,
        validation_alias=AliasChoices("AUTO_AGENT_COLLAB_MAX_USERS", "COLLAB_MAX_USERS_PER_ROOM"),
    )

    def model_post_init(self, __context) -> None:
        data_dir = Path(self.DATA_DIR)
        if self.DATABASE_URL is None:
            self.DATABASE_URL = f"sqlite+aiosqlite:///{data_dir / 'auto_agent.db'}"
        if self.SCREENSHOT_DIR is None:
            self.SCREENSHOT_DIR = str(data_dir / "screenshots")
        if self.SCRIPT_DIR is None:
            self.SCRIPT_DIR = str(data_dir / "scripts")

    @property
    def llm_configured(self) -> bool:
        return bool(self.LLM_API_KEY and self.LLM_API_KEY != "your-api-key-here")

    @property
    def cors_allow_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.CORS_ALLOW_ORIGINS.split(",") if origin.strip()]
        return origins or ["*"]

    @property
    def filesystem_watch_paths(self) -> list[str]:
        return [path.strip() for path in self.RECORD_FILESYSTEM_PATHS.split(",") if path.strip()]

    @property
    def screenshot_path(self) -> Path:
        return Path(self.SCREENSHOT_DIR)

    @property
    def script_path(self) -> Path:
        return Path(self.SCRIPT_DIR)

    @property
    def web_viewport(self) -> dict[str, int]:
        return {
            "width": self.WEB_VIEWPORT_WIDTH,
            "height": self.WEB_VIEWPORT_HEIGHT,
        }

    def ensure_dirs(self) -> None:
        Path(self.DATA_DIR).mkdir(parents=True, exist_ok=True)
        self.screenshot_path.mkdir(parents=True, exist_ok=True)
        self.script_path.mkdir(parents=True, exist_ok=True)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


def load_settings() -> Settings:
    return Settings()


class SettingsProxy:
    _REMOTE_LLM_FIELDS = {
        "LLM_BASE_URL": "base_url",
        "LLM_API_KEY": "api_key",
        "LLM_MODEL": "model",
        "LLM_TEMPERATURE": "temperature",
        "LLM_TOP_P": "top_p",
        "LLM_MAX_TOKENS": "max_tokens",
    }

    def __getattr__(self, name: str):
        base_settings = load_settings()
        remote_llm = self._get_remote_llm_override()

        if remote_llm is not None and remote_llm.enabled:
            if name == "llm_configured":
                return remote_llm.configured
            if name in self._REMOTE_LLM_FIELDS:
                return getattr(remote_llm, self._REMOTE_LLM_FIELDS[name])

        return getattr(base_settings, name)

    @staticmethod
    def _get_remote_llm_override():
        try:
            from src.settings_store import SettingsStore

            settings_store = SettingsStore.get_instance()
            user_settings = settings_store.get_settings()
            return getattr(user_settings, "remote_llm", None)
        except Exception:
            return None


settings = SettingsProxy()
