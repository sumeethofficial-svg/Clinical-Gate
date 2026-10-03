import pytest
from fastapi.testclient import TestClient

from app.auth import service
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _state(clean_state):
    service.reset_lockouts()


def login(user, pw="demo-password"):
    r = client.post("/login", json={"username": user, "password": pw})
    return r


def hdr(user):
    return {"Authorization": "Bearer " + login(user).json()["access_token"]}


def test_health_and_ui_served():
    assert client.get("/health").json()["synthetic_data_only"] is True
    r = client.get("/")
    assert r.status_code == 200 and "ClinicalGate" in r.text
    assert r.headers["x-frame-options"] == "DENY" and "default-src 'self'" in r.headers["content-security-policy"]


def test_login_and_me():
    r = login("nurse_ana")
    assert r.status_code == 200 and r.json()["user"]["role"] == "nurse"
    assert client.get("/api/me", headers=hdr("nurse_ana")).json()["role"] == "nurse"


def test_bad_login_and_missing_token():
    assert login("nurse_ana", "nope").status_code == 401
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/api/audit", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_identity_cannot_be_supplied_in_the_body():
    h = hdr("fd_sam")
    r = client.post("/api/chat", json={"message": "show claims", "role": "billing", "username": "billing_lee"}, headers=h)
    assert r.status_code == 200
    assert all(t["decision"] == "deny" for t in r.json()["trace"]) and r.json()["trace"]


def test_chat_nurse_allowed_front_desk_blocked_and_audited():
    n = client.post("/api/chat", json={"message": 'Summarize the chart for "Mara Ellison-Voss"'}, headers=hdr("nurse_ana")).json()
    assert [t["decision"] for t in n["trace"]] == ["allow", "allow"] and "Ellison-Voss" in n["answer"]
    f = client.post("/api/chat", json={"message": "Show chart for patient 501"}, headers=hdr("fd_sam")).json()
    assert f["trace"][0]["decision"] == "deny"
    denied = client.get("/api/audit?denied_only=true", headers=hdr("fd_sam")).json()
    assert denied["scope"] == "your activity" and denied["entries"][0]["tool"] == "get_chart_summary"
    mgr = client.get("/api/audit", headers=hdr("manager_kim")).json()
    assert "args" not in mgr["entries"][0] and len(mgr["entries"]) >= 3


def test_role_switch_is_a_new_login_with_fresh_history():
    from app.main import conversations
    client.post("/api/chat", json={"message": 'Summarize the chart for "Mara Ellison-Voss"'}, headers=hdr("nurse_ana"))
    client.post("/api/chat", json={"message": "hello there"}, headers=hdr("fd_sam"))
    histories = [" ".join(m.get("content") or "" for m in h) for h in conversations._data.values()]
    nurse_h = [h for h in histories if "CANARY-NOTE-" in h]
    fd_h = [h for h in histories if "hello there" in h]
    assert nurse_h and fd_h and not any("CANARY-NOTE-" in h for h in fd_h)


def test_oversize_and_empty_messages_rejected():
    h = hdr("fd_sam")
    assert client.post("/api/chat", json={"message": ""}, headers=h).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 5000}, headers=h).status_code == 422


def test_eval_summary_endpoint_shape():
    assert "mode" in client.get("/api/eval-summary").json()
