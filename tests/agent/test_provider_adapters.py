"""Exercise the real-provider response parsing with stub SDK clients (no network, no keys)."""
import json
from types import SimpleNamespace as NS

from app.agent.llm.anthropic_client import AnthropicLLM
from app.agent.llm.openai_client import OpenAILLM


def test_anthropic_response_parsing_and_request_shape():
    seen = {}

    def create(**kw):
        seen.update(kw)
        return NS(content=[NS(type="text", text="checking"),
                           NS(type="tool_use", id="tu_1", name="get_chart_summary", input={"patient_id": 7})])
    llm = AnthropicLLM.__new__(AnthropicLLM)
    llm.model, llm.client = "m", NS(messages=NS(create=create))
    out = llm.complete("sys", [{"role": "user", "content": "hi"}],
                       [{"name": "get_chart_summary", "description": "d", "input_schema": {"type": "object"}}])
    assert out.text == "checking" and out.tool_calls[0].name == "get_chart_summary" and out.tool_calls[0].arguments == {"patient_id": 7}
    assert seen["system"] == "sys" and seen["tools"][0]["input_schema"] == {"type": "object"}
    assert "user_id" not in json.dumps(seen["tools"])


def test_openai_response_parsing_including_malformed_arguments():
    def create(**kw):
        calls = [NS(id="c1", function=NS(name="search_patients", arguments='{"query": "ab"}')),
                 NS(id="c2", function=NS(name="get_claims", arguments="{not json"))]
        return NS(choices=[NS(message=NS(content=None, tool_calls=calls))])
    llm = OpenAILLM.__new__(OpenAILLM)
    llm.model, llm.client = "m", NS(chat=NS(completions=NS(create=create)))
    out = llm.complete("sys", [{"role": "user", "content": "hi"}],
                       [{"name": "search_patients", "description": "d", "input_schema": {"type": "object"}}])
    assert out.tool_calls[0].arguments == {"query": "ab"}
    assert out.tool_calls[1].arguments == {"_raw": "{not json"}      # malformed JSON becomes a rejected tool call, not a crash
