"""Provider-neutral LLM interface. Messages are plain dicts:
  {"role":"user","content":str}
  {"role":"assistant","content":str,"tool_calls":[{"id","name","arguments":dict}]}
  {"role":"tool","tool_call_id":str,"name":str,"content":str}
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class LLMClient(Protocol):
    name: str

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse: ...
