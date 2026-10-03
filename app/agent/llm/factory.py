from __future__ import annotations

from app.agent.llm.base import LLMClient
from app.agent.llm.mock import HeuristicLLM
from app.config import Settings


def get_llm(settings: Settings, provider: str | None = None) -> LLMClient:
    provider = (provider or settings.llm_provider).lower()
    if provider in ("heuristic", "mock"):
        return HeuristicLLM()
    if provider == "anthropic":
        from app.agent.llm.anthropic_client import AnthropicLLM
        return AnthropicLLM(settings.anthropic_model)
    if provider == "openai":
        from app.agent.llm.openai_client import OpenAILLM
        return OpenAILLM(settings.openai_model)
    raise ValueError(f"unknown LLM_PROVIDER {provider!r}")
