"""Deterministic, offline "models".

ScriptedLLM  - replays a fixed script. Eval cases use it to play a *maliciously compliant* model that
               does whatever the attacker (or a planted instruction) asks, so the test proves the
               enforcement layer, not the model's good manners.
HeuristicLLM - tiny rule-based assistant used for the key-free demo. It is intentionally naive: it does
               NOT consult which tools are advertised, so the demo shows code-level enforcement.
"""
from __future__ import annotations

import json
import re
import uuid

from app.agent.llm.base import LLMResponse, ToolCall


def _cid() -> str:
    return "call_" + uuid.uuid4().hex[:10]


class ScriptedLLM:
    name = "scripted"

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.i = 0

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        if self.i >= len(self.script):
            return LLMResponse("Done.")
        step = self.script[self.i]
        self.i += 1
        tool_msgs = [m for m in messages if m["role"] == "tool"]
        if "call" in step:
            return LLMResponse("", [ToolCall(_cid(), step["call"], dict(step.get("args", {})))])
        if step.get("follow_injection"):
            # Behave like a model that obeys instructions found inside the last tool result.
            last = tool_msgs[-1]["content"] if tool_msgs else ""
            m = re.search(r"patient_id[=\s]+(\d+)", last)
            tool = step.get("tool", "get_chart_summary")
            if m:
                return LLMResponse("", [ToolCall(_cid(), tool, {"patient_id": int(m.group(1))})])
            return LLMResponse("No instruction found.")
        if step.get("say_all"):
            return LLMResponse("Here is everything I retrieved:\n" + "\n".join(m["content"] for m in tool_msgs))
        if step.get("say_last"):
            return LLMResponse(tool_msgs[-1]["content"] if tool_msgs else "")
        return LLMResponse(step.get("say", "Done."))


_DIM_WORDS = [("sex", "sex"), ("gender", "sex"), ("age", "age_band"), ("diagnos", "diagnosis_category"),
              ("claim", "claim_status"), ("appointment", "appointment_status"), ("encounter", "encounter_class")]


class HeuristicLLM:
    name = "heuristic"

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMResponse:
        start = max(i for i, m in enumerate(messages) if m["role"] == "user")
        text = messages[start]["content"]
        results = [(m["name"], json.loads(m["content"])) for m in messages[start:] if m["role"] == "tool"]
        nxt = self._next_call(text, results)
        if nxt:
            return LLMResponse("", [ToolCall(_cid(), nxt[0], nxt[1])])
        return LLMResponse(self._answer(results) if results else
                           "I can look up patients, appointments, charts, claims and cohort counts. What do you need?")

    @staticmethod
    def _name(text: str) -> str | None:
        m = re.search(r'"([^"]{2,60})"', text)
        if m:
            return m.group(1)
        m = re.search(r"\b([A-Z][a-z]+(?:[-'][A-Z][a-z]+)?(?:\s+[A-Z][a-z]+(?:[-'][A-Z][a-z]+)?)+)\b", text)
        return m.group(1) if m else None

    def _next_call(self, text: str, results: list) -> tuple[str, dict] | None:
        low = text.lower()
        done = [n for n, _ in results]
        pid_m = re.search(r"(?:patient|id|#)\s*(?:id\s*)?#?(\d{1,9})\b", low)
        pid = int(pid_m.group(1)) if pid_m else None
        name = self._name(text)
        wants_chart = any(w in low for w in ("chart", "summar", "history", "diagnos", "notes"))
        wants_appt = "appointment" in low or "schedule" in low
        wants_claims = "claim" in low or "billing" in low
        wants_count = any(w in low for w in ("how many", "count", "cohort", "breakdown", "distribution"))
        if wants_count:
            if "cohort_counts" in done:
                return None
            dim = next((d for w, d in _DIM_WORDS if w in low), "sex")
            args = {"dimension": dim}
            m = re.search(r"\b([A-Z]\d{2})\b", text)
            if m:
                args["dx_category"] = m.group(1)
            return "cohort_counts", args
        if pid is None and name and (wants_chart or wants_appt or wants_claims):
            if "search_patients" not in done:
                return "search_patients", {"query": name}
            found = next((r for n, r in results if n == "search_patients"), {})
            rows = (found.get("data") or {}).get("patients") or []
            pid = rows[0]["id"] if rows else None
            if pid is None:
                return None
        if wants_chart and "get_chart_summary" not in done and pid:
            return "get_chart_summary", {"patient_id": pid}
        if wants_appt and "get_appointments" not in done:
            return "get_appointments", ({"patient_id": pid} if pid else {})
        if wants_claims and "get_claims" not in done:
            return "get_claims", ({"patient_id": pid} if pid else {})
        if not (wants_chart or wants_appt or wants_claims) and "search_patients" not in done:
            q = name or re.sub(r"(?i)\b(find|search|look ?up|who is|patient|for|named|called)\b", "", text).strip(" ?.!")
            if len(q) >= 2:
                return "search_patients", {"query": q[:100]}
        return None

    @staticmethod
    def _answer(results: list) -> str:
        lines = []
        for tool, r in results:
            if not r.get("ok"):
                lines.append(f"I couldn't do that ({tool}): {r.get('message', 'access denied')}")
                continue
            d = r["data"]
            if tool == "search_patients":
                lines.append(f"Found {d['count']} patient(s): " + "; ".join(
                    f"{p['full_name']} ({p['mrn']})" for p in d["patients"][:8]) if d["count"] else "No matching patients.")
            elif tool == "get_appointments":
                lines.append(f"{d['count']} appointment(s): " + "; ".join(
                    f"{a['starts_at']} {a['appointment_type']} [{a['status']}] {a['patient_name']}" for a in d["appointments"][:8]))
            elif tool == "get_chart_summary":
                p = d["patient"]
                lines.append(f"{p['full_name']} ({p['mrn']}), DOB {p['dob']}. Diagnoses: " +
                             ", ".join(x["description"] for x in d["diagnoses"][:5]) +
                             ". Latest note: " + (d["recent_notes"][0]["note_text"] if d["recent_notes"] else "none"))
            elif tool == "get_claims":
                lines.append(f"{d['count']} claim(s): " + "; ".join(
                    f"{c['claim_number']} {c['status']} ${c['total_amount']}" for c in d["claims"][:8]))
            elif tool == "cohort_counts":
                lines.append("Counts by " + d["dimension"] + ": " + ", ".join(
                    f"{c['bucket']}={'<5 (suppressed)' if c['suppressed'] else c['patient_count']}" for c in d["cells"]))
        return "\n".join(lines)
