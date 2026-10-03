import time

import jwt
import pytest

from app.auth import service
from app.auth.tokens import AUDIENCE, ISSUER, AuthError, issue_token
from app.config import get_settings
from app.db.audit import fetch_audit, write_audit
from app.db.session import scoped

SECRET = lambda: get_settings().jwt_secret  # noqa: E731


@pytest.fixture(autouse=True)
def _reset():
    service.reset_lockouts()
    yield
    service.reset_lockouts()


def uid(admin, name):
    return admin.execute("SELECT id FROM users WHERE username=%s", (name,)).fetchone()[0]


def test_login_issues_token_that_resolves_to_db_backed_identity():
    res = service.login("nurse_ana", "demo-password")
    ident = service.resolve_identity(res["access_token"])
    assert (ident.username, ident.role, ident.provider_id) == ("nurse_ana", "nurse", 1)


@pytest.mark.parametrize("user,pw", [("nurse_ana", "wrong"), ("nobody", "demo-password"), ("", ""), ("nurse_ana", "")])
def test_bad_credentials_rejected(user, pw):
    with pytest.raises(AuthError):
        service.login(user, pw)


def test_lockout_after_repeated_failures():
    for _ in range(5):
        with pytest.raises(AuthError):
            service.login("fd_sam", "bad")
    with pytest.raises(AuthError, match="too many"):
        service.login("fd_sam", "demo-password")


def test_forged_and_malformed_tokens_rejected(admin):
    good = issue_token(SECRET(), user_id=uid(admin, "fd_sam"), role="front_desk")
    now = int(time.time())
    cases = {
        "wrong_key": issue_token("x" * 40, user_id=uid(admin, "fd_sam"), role="front_desk"),
        "expired": issue_token(SECRET(), user_id=uid(admin, "fd_sam"), role="front_desk", now=now - 7200, ttl=60),
        "wrong_audience": issue_token(SECRET(), user_id=uid(admin, "fd_sam"), role="front_desk", audience="other"),
        "alg_none": jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "1", "role": "manager", "sid": "a",
                                "iat": now, "exp": now + 600}, key=None, algorithm="none"),
        "role_claim_escalated": issue_token(SECRET(), user_id=uid(admin, "fd_sam"), role="manager"),
        "garbage": "not.a.jwt",
        "empty": "",
        "tampered_payload": good.split(".")[0] + "." + good.split(".")[1][:-2] + "AA." + good.split(".")[2],
        "unknown_user": issue_token(SECRET(), user_id=99999, role="nurse"),
        "bad_role_value": issue_token(SECRET(), user_id=uid(admin, "fd_sam"), role="admin"),
    }
    for tok in cases.values():
        with pytest.raises(AuthError):
            service.resolve_identity(tok)
    assert service.resolve_identity(good).role == "front_desk"


def test_deactivated_user_token_stops_working(admin):
    tok = service.login("billing_lee", "demo-password")["access_token"]
    admin.execute("UPDATE users SET active=false WHERE username='billing_lee'")
    try:
        with pytest.raises(AuthError):
            service.resolve_identity(tok)
        with pytest.raises(AuthError):
            service.login("billing_lee", "demo-password")
    finally:
        admin.execute("UPDATE users SET active=true WHERE username='billing_lee'")


def test_scoped_session_enforces_identity_not_caller_input():
    ident = service.resolve_identity(service.login("nurse_ana", "demo-password")["access_token"])
    with scoped(ident) as c:
        n = c.execute("SELECT count(*) AS n FROM patients").fetchone()["n"]
        assert 0 < n < 500
        assert c.execute("SELECT current_user AS u").fetchone()["u"] == "cg_nurse"


def test_context_does_not_leak_between_sessions():
    ana = service.resolve_identity(service.login("nurse_ana", "demo-password")["access_token"])
    fd = service.resolve_identity(service.login("fd_sam", "demo-password")["access_token"])
    with scoped(ana) as c:
        assert c.execute("SELECT current_setting('app.provider_id', true) AS p").fetchone()["p"] == "1"
    with scoped(fd) as c:
        assert c.execute("SELECT current_setting('app.provider_id', true) AS p").fetchone()["p"] in ("", None)
        assert c.execute("SELECT count(*) AS n FROM patients").fetchone()["n"] >= 500


def test_audit_roundtrip_and_visibility(clean_state):
    ana = service.resolve_identity(service.login("nurse_ana", "demo-password")["access_token"])
    raj = service.resolve_identity(service.login("nurse_raj", "demo-password")["access_token"])
    kim = service.resolve_identity(service.login("manager_kim", "demo-password")["access_token"])
    write_audit(ana, "get_chart_summary", {"patient_id": 5}, "deny", "not accessible", 0)
    write_audit(raj, "search_patients", {"query": "x" * 5000}, "allow", None, 3)
    own = fetch_audit(ana)
    assert [r["username"] for r in own] == ["nurse_ana"] and own[0]["args"] == {"patient_id": 5}
    assert len(fetch_audit(ana, denied_only=True)) == 1 and fetch_audit(raj, denied_only=True) == []
    mgr = fetch_audit(kim)
    assert {r["username"] for r in mgr} == {"nurse_ana", "nurse_raj"} and all("args" not in r for r in mgr)
    assert len(fetch_audit(raj)[0]["args"]["query"]) == 200   # clipped
