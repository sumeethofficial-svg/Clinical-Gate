"""Runtime configuration, read from environment variables. No secrets live in the repo."""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ConfigError(f"Missing required environment variable {name} (see .env.example)")
    return val


@dataclass(frozen=True)
class Settings:
    database_url_app: str      # low-privilege cg_app connection used by the API at request time
    jwt_secret: str
    token_ttl_seconds: int
    llm_provider: str          # heuristic | anthropic | openai
    anthropic_model: str
    openai_model: str
    max_agent_steps: int


def load_settings() -> Settings:
    secret = _required("JWT_SECRET")
    if len(secret) < 32:
        raise ConfigError("JWT_SECRET must be at least 32 characters")
    return Settings(
        database_url_app=_required("DATABASE_URL_APP"),
        jwt_secret=secret,
        token_ttl_seconds=int(os.environ.get("TOKEN_TTL_SECONDS", "900")),
        llm_provider=os.environ.get("LLM_PROVIDER", "heuristic").lower(),
        anthropic_model=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        max_agent_steps=int(os.environ.get("MAX_AGENT_STEPS", "6")),
    )


def admin_database_url() -> str:
    """Owner/admin connection. Used ONLY by migrations, seeding and the eval ground-truth oracle."""
    return _required("DATABASE_URL")
