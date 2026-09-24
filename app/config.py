"""Environment-first application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from math import isfinite
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    app_name: str = os.getenv("AIGC_LITE_APP_NAME", "aigc-lite")
    host: str = os.getenv("AIGC_LITE_HOST", "127.0.0.1")
    port: int = int(os.getenv("AIGC_LITE_PORT", "8000"))
    debug: bool = _bool("AIGC_LITE_DEBUG")
    api_key: str = os.getenv("AIGC_LITE_API_KEY", "")
    llm_api_key: str = os.getenv("AIGC_LITE_LLM_API_KEY", "")
    llm_base_url: str = os.getenv("AIGC_LITE_LLM_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("AIGC_LITE_LLM_MODEL", "gpt-4o-mini")
    llm_timeout: float = float(os.getenv("AIGC_LITE_LLM_TIMEOUT", "120"))
    data_dir: str = os.getenv("AIGC_LITE_DATA_DIR", "data")
    max_agent_steps: int = int(os.getenv("AIGC_LITE_MAX_AGENT_STEPS", "8"))
    max_agent_tool_calls: int = int(
        os.getenv("AIGC_LITE_MAX_AGENT_TOOL_CALLS", "16")
    )
    max_agent_run_seconds: float = float(
        os.getenv("AIGC_LITE_MAX_AGENT_RUN_SECONDS", "300")
    )
    max_tool_result_chars: int = int(os.getenv("AIGC_LITE_MAX_TOOL_RESULT_CHARS", "100000"))
    max_tool_record_chars: int = int(os.getenv("AIGC_LITE_MAX_TOOL_RECORD_CHARS", "50000"))
    default_tool_timeout_seconds: float = float(
        os.getenv("AIGC_LITE_DEFAULT_TOOL_TIMEOUT_SECONDS", "30")
    )
    tenants_json: str = os.getenv("AIGC_LITE_TENANTS_JSON", "")
    database_url: str = os.getenv("AIGC_LITE_DATABASE_URL", "")
    auth_secret: str = os.getenv("AIGC_LITE_AUTH_SECRET", "change-this-in-production")
    master_key: str = os.getenv("AIGC_LITE_MASTER_KEY", "")
    session_ttl_hours: int = int(os.getenv("AIGC_LITE_SESSION_TTL_HOURS", "168"))
    rate_limit_per_minute: int = int(os.getenv("AIGC_LITE_RATE_LIMIT_PER_MINUTE", "60"))
    max_body_bytes: int = int(os.getenv("AIGC_LITE_MAX_BODY_BYTES", "3000000"))
    admin_email: str = os.getenv("AIGC_LITE_ADMIN_EMAIL", "")
    admin_password: str = os.getenv("AIGC_LITE_ADMIN_PASSWORD", "")
    default_input_price: float = float(os.getenv("AIGC_LITE_DEFAULT_INPUT_PRICE", "0"))
    default_output_price: float = float(os.getenv("AIGC_LITE_DEFAULT_OUTPUT_PRICE", "0"))
    allow_signup: bool = _bool("AIGC_LITE_ALLOW_SIGNUP", True)
    mcp_api_key: str = os.getenv("AIGC_LITE_MCP_API_KEY", "")
    legacy_mcp_protocol_version: str = os.getenv(
        "AIGC_LITE_LEGACY_MCP_PROTOCOL_VERSION",
        os.getenv("AIGC_LITE_MCP_PROTOCOL_VERSION", "2025-06-18"),
    )
    mcp_allowed_hosts: str = os.getenv(
        "AIGC_LITE_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*"
    )
    mcp_allowed_origins: str = os.getenv("AIGC_LITE_MCP_ALLOWED_ORIGINS", "")
    mcp_servers_json: str = os.getenv("AIGC_LITE_MCP_SERVERS_JSON", "")

    def __post_init__(self) -> None:
        if not 1 <= self.max_agent_steps <= 32:
            raise ValueError("AIGC_LITE_MAX_AGENT_STEPS must be between 1 and 32")
        if not 0 <= self.max_agent_tool_calls <= 256:
            raise ValueError(
                "AIGC_LITE_MAX_AGENT_TOOL_CALLS must be between 0 and 256"
            )
        if not isfinite(self.max_agent_run_seconds) or self.max_agent_run_seconds <= 0:
            raise ValueError("AIGC_LITE_MAX_AGENT_RUN_SECONDS must be positive")

    @property
    def database_path(self) -> Path:
        return Path(self.data_dir) / "aigc-lite.db"


settings = Settings()
