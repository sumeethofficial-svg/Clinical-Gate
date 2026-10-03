"""ClinicalGate API. SYNTHETIC DATA ONLY."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.llm.factory import get_llm
from app.agent.loop import ConversationStore, run_agent
from app.auth import service
from app.auth.tokens import AuthError, Identity
from app.config import get_settings
from app.db.audit import fetch_audit

STATIC = Path(__file__).parent / "static"
EVAL_RESULTS = Path(__file__).resolve().parent.parent / "evals"

app = FastAPI(title="ClinicalGate", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
conversations = ConversationStore()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Security-Policy"] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; "
                                               "script-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
    return resp


def current_identity(authorization: str | None = Header(default=None)) -> Identity:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        return service.resolve_identity(authorization.split(" ", 1)[1].strip())
    except AuthError:
        raise HTTPException(401, "invalid or expired token") from None


class LoginBody(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=200)


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.get("/health")
def health():
    return {"status": "ok", "synthetic_data_only": True}


@app.post("/login")
def login(body: LoginBody):
    try:
        return service.login(body.username, body.password)
    except AuthError as e:
        raise HTTPException(401, str(e)) from None


@app.get("/api/me")
def me(ident: Identity = Depends(current_identity)):
    return {"username": ident.username, "role": ident.role, "display_name": ident.display_name}


@app.post("/api/chat")
def chat(body: ChatBody, ident: Identity = Depends(current_identity)):
    settings = get_settings()
    result = run_agent(ident, body.message, get_llm(settings), conversations.get(ident.session_id), settings.max_agent_steps)
    return {"answer": result.answer,
            "trace": [{"tool": t.tool, "args": t.args, "decision": t.decision, "reason": t.reason} for t in result.trace]}


@app.get("/api/audit")
def audit(denied_only: bool = False, limit: int = 40, ident: Identity = Depends(current_identity)):
    rows = fetch_audit(ident, denied_only=denied_only, limit=limit)
    for r in rows:
        r["ts"] = r["ts"].isoformat(timespec="seconds")
    return {"scope": "all users (arguments hidden)" if ident.role == "manager" else "your activity", "entries": rows}


@app.get("/api/eval-summary")
def eval_summary():
    """Latest committed red-team numbers, shown in the UI header. No auth: contains no patient data."""
    for mode in ("real", "mock"):
        f = EVAL_RESULTS / f"results.{mode}.json"
        if f.exists():
            data = json.loads(f.read_text())
            s = data["summary"]
            return {"mode": mode, "attack_cases": s["attack_cases"], "leaking_cases": s["leaking_cases"],
                    "leak_rate": s["leak_rate"], "legit_success_rate": s["legit_success_rate"], "when": data["meta"]["when"]}
    return JSONResponse({"mode": None}, status_code=200)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
