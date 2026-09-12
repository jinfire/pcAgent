from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


SUPPORTED_PROVIDERS = {"openai", "gemini"}
SUPPORTED_REASONING_LEVELS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    gemini_api_key: str
    chat: ModelSettings
    manager: ModelSettings
    coding: ModelSettings
    general: ModelSettings
    review: ModelSettings
    escalation: ModelSettings
    agent_workspace_root: Path | None
    allowed_workspace_roots: tuple[Path, ...]
    database_path: Path
    max_agent_steps: int
    command_timeout_seconds: int
    max_tool_output_chars: int
    auto_escalation_enabled: bool
    usage_log_path: Path
    molit_api_key: str
    rone_api_key: str
    real_estate_source_timeout_seconds: int
    real_estate_source_max_bytes: int
    real_estate_policy_feed_urls: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        workspace_value = os.getenv("AGENT_WORKSPACE_ROOT", "").strip()
        workspace = Path(workspace_value).expanduser().resolve() if workspace_value else None
        allowed_value = os.getenv("AGENT_WORKSPACE_ALLOWED_ROOTS", "").strip()
        allowed_roots = tuple(
            Path(value.strip()).expanduser().resolve()
            for value in allowed_value.split(";")
            if value.strip()
        )
        if not allowed_roots and workspace is not None:
            allowed_roots = (workspace,)
        database_path = Path(os.getenv("AGENT_DB_PATH", "data/agent.db")).expanduser()
        if not database_path.is_absolute():
            database_path = Path.cwd() / database_path
        usage_path = Path(os.getenv("USAGE_LOG_PATH", "logs/llm_usage.jsonl")).expanduser()
        if not usage_path.is_absolute():
            usage_path = Path.cwd() / usage_path
        policy_feed_urls = tuple(
            value.strip()
            for value in os.getenv("REAL_ESTATE_POLICY_FEED_URLS", "").split(";")
            if value.strip()
        )
        return cls(
            openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            chat=_model_settings("CHAT", "gemini", "gemini-3.8-flash", "low"),
            manager=_model_settings("MANAGER", "gemini", "gemini-3.8-flash", "high"),
            coding=_model_settings("CODING", "gemini", "gemini-3.8-flash", "medium"),
            general=_model_settings("GENERAL", "gemini", "gemini-3.8-flash", "low"),
            review=_model_settings("REVIEW", "openai", "gpt-5.4-mini", "medium"),
            escalation=_model_settings("ESCALATION", "openai", "gpt-5.6-sol", "high"),
            agent_workspace_root=workspace,
            allowed_workspace_roots=allowed_roots,
            database_path=database_path.resolve(),
            max_agent_steps=_bounded_int("MAX_AGENT_STEPS", 15, 1, 50),
            command_timeout_seconds=_bounded_int("COMMAND_TIMEOUT_SECONDS", 120, 1, 600),
            max_tool_output_chars=_bounded_int("MAX_TOOL_OUTPUT_CHARS", 20_000, 1_000, 100_000),
            auto_escalation_enabled=_boolean("AUTO_ESCALATION_ENABLED", True),
            usage_log_path=usage_path.resolve(),
            molit_api_key=os.getenv("MOLIT_API_KEY", "").strip(),
            rone_api_key=os.getenv("RONE_API_KEY", "").strip(),
            real_estate_source_timeout_seconds=_bounded_int(
                "REAL_ESTATE_SOURCE_TIMEOUT_SECONDS", 20, 3, 120
            ),
            real_estate_source_max_bytes=_bounded_int(
                "REAL_ESTATE_SOURCE_MAX_BYTES", 2_000_000, 10_000, 10_000_000
            ),
            real_estate_policy_feed_urls=policy_feed_urls,
        )

    def has_provider_key(self, provider: str) -> bool:
        if provider == "openai":
            return bool(self.openai_api_key)
        if provider == "gemini":
            return bool(self.gemini_api_key)
        return False

    def required_agent_providers(self) -> set[str]:
        models = [self.manager, self.coding, self.general, self.review]
        if self.auto_escalation_enabled:
            models.append(self.escalation)
        return {item.provider for item in models}


def _model_settings(prefix: str, provider: str, model: str, reasoning: str) -> ModelSettings:
    configured_provider = os.getenv(f"{prefix}_PROVIDER", provider).strip().casefold()
    configured_model = os.getenv(f"{prefix}_MODEL", model).strip()
    configured_reasoning = os.getenv(f"{prefix}_REASONING_EFFORT", reasoning).strip().casefold()
    if configured_provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"{prefix}_PROVIDER must be one of: {', '.join(sorted(SUPPORTED_PROVIDERS))}")
    if not configured_model:
        raise ValueError(f"{prefix}_MODEL must not be empty")
    if configured_reasoning not in SUPPORTED_REASONING_LEVELS:
        raise ValueError(
            f"{prefix}_REASONING_EFFORT must be one of: "
            f"{', '.join(sorted(SUPPORTED_REASONING_LEVELS))}"
        )
    return ModelSettings(configured_provider, configured_model, configured_reasoning)


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _boolean(name: str, default: bool) -> bool:
    raw = os.getenv(name, str(default)).strip().casefold()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
