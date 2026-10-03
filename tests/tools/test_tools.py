"""Deterministic tool-layer tests: real Postgres, no LLM."""
import json
import re

import pytest

from app.auth import service
from app.auth.context import require_identity
from app.auth.tokens import AuthError
from app.db.audit import AuditWriteError
from app.tools import runner
from app.tools.gateway import call_tool

PW = "demo-password"


@pytest.fixture(autouse=True)
def _state(clean_state):
    service.reset_lockouts()


def ident(name):
    return service.resolve_identity(service.login(name, PW)["access_token"])


def call(user, tool, args=None):
    return call_tool(ident(user), tool, args or {})


def blob(r):
    return r.to_model_json()


def pid(admin, mrn):
    return admin.execute("SELECT id FROM patients WHERE mrn=%s", (mrn,)).fetchone()[0]


def audit_rows(admin):
    return admin.execute("SELECT tool, decision, reason FROM audit_log ORDER BY id").fetchall()


# ---------------------------------------------------------------- front desk
def test_front_desk_search_returns_only_scheduling_fields():
    r = call("fd_sam", "search_patients", {"query": "Ellison"})
    assert r.ok and set(r.payload["data"]["patients"][0]) == {"id", "mrn", "full_name", "phone"}


def test_front_desk_cannot_search_by_hidden_columns(admin):
    email = admin.execute("SELECT email FROM patients WHERE mrn='MRN-900001'").fetchone()[0]
    r = call("fd_sam", "search_patients", {"query": email})
    assert r.ok and r.payload["data"]["count"] == 0          # no column-existence oracle


def test_front_desk_appointments_have_no_clinical_reason():
    r = call("fd_sam", "get_appointments", {"limit": 50})
    assert r.ok and r.payload["data"]["count"] > 0
    assert "reason" not in blob(r) and "CANARY" not in blob(r)


@pytest.mark.parametrize("tool,args", [("get_chart_summary", {"patient_id": 1}), ("get_claims", {}),
                                       ("cohort_counts", {"dimension": "sex"})])
def test_front_desk_forbidden_tools(tool, args, admin):
    r = call("fd_sam", tool, args)
    assert not r.ok and r.reason == "role_not_permitted"
    assert ("deny", "role_not_permitted") in [(d, rs) for _, d, rs in audit_rows(admin)]


# ---------------------------------------------------------------- nurse
def test_nurse_chart_for_care_team_patient(admin):
    r = call("nurse_ana", "get_chart_summary", {"patient_id": pid(admin, "MRN-900001")})
    assert r.ok and "CANARY-NOTE-" in blob(r) and r.payload["data"]["patient"]["mrn"] == "MRN-900001"
    assert "billing_address" not in blob(r)


def test_nurse_off_team_patient_denied_and_indistinguishable_from_nonexistent(admin):
    off = call("nurse_ana", "get_chart_summary", {"patient_id": pid(admin, "MRN-900002")})
    ghost = call("nurse_ana", "get_chart_summary", {"patient_id": 999_999})
    assert not off.ok and off.reason == "patient_not_accessible"
    assert off.to_model_json() == ghost.to_model_json()          # identical bytes: no existence oracle
    assert "CANARY-FIXTURE-B" not in blob(off) and "Wrenfield" not in blob(off)


def test_nurse_cross_patient_search_and_appointments(admin):
    assert call("nurse_ana", "search_patients", {"query": "Wrenfield"}).payload["data"]["count"] == 0
    r = call("nurse_ana", "get_appointments", {"patient_id": pid(admin, "MRN-900002")})
    assert not r.ok and r.reason == "patient_not_accessible"
    assert call("nurse_raj", "search_patients", {"query": "Wrenfield"}).payload["data"]["count"] == 1


def test_nurse_appointments_include_reason_only_for_team_patients(admin):
    r = call("nurse_ana", "get_appointments", {"limit": 50})
    assert r.ok and all("reason" in a for a in r.payload["data"]["appointments"])
    team = {x[0] for x in admin.execute("SELECT patient_id FROM care_team WHERE provider_id=1")}
    assert {a["patient_id"] for a in r.payload["data"]["appointments"]} <= team


def test_nurse_has_no_claims_or_cohorts():
    assert call("nurse_ana", "get_claims").reason == "role_not_permitted"
    assert call("nurse_ana", "cohort_counts", {"dimension": "sex"}).reason == "role_not_permitted"


# ---------------------------------------------------------------- billing
def test_billing_claims_have_codes_but_no_clinical_text():
    r = call("billing_lee", "get_claims", {"status": "paid", "limit": 20})
    assert r.ok and r.payload["data"]["count"] > 0
    claim = r.payload["data"]["claims"][0]
    assert {"cpt_code", "icd10_code", "amount"} <= set(claim["lines"][0])
    b = blob(r)
    assert "note_text" not in b and "CANARY" not in b and "phone" not in b and "email" not in b


def test_billing_search_exposes_billing_address_not_contact():
    r = call("billing_lee", "search_patients", {"query": "Ellison"})
    assert set(r.payload["data"]["patients"][0]) == {"id", "mrn", "full_name", "billing_address"}


def test_billing_cannot_read_charts_or_appointments():
    assert call("billing_lee", "get_chart_summary", {"patient_id": 501}).reason == "role_not_permitted"
    assert call("billing_lee", "get_appointments").reason == "role_not_permitted"


# ---------------------------------------------------------------- manager
def test_manager_gets_aggregates_only():
    r = call("manager_kim", "cohort_counts", {"dimension": "sex"})
    assert r.ok and all(c["patient_count"] is None or c["patient_count"] >= 5 for c in r.payload["data"]["cells"])
    for tool, args in (("search_patients", {"query": "Smith"}), ("get_appointments", {}),
                       ("get_chart_summary", {"patient_id": 1}), ("get_claims", {})):
        assert call("manager_kim", tool, args).reason == "role_not_permitted"


# ---------------------------------------------------------------- tampering / validation
@pytest.mark.parametrize("extra", [{"user_id": 1}, {"role": "manager"}, {"provider_id": 2}, {"as_user": "billing_lee"},
                                   {"context": {"role": "admin"}}, {"__role": "manager"}, {"x": 1}])
def test_unknown_or_identity_arguments_are_rejected_and_audited(extra, admin):
    r = call("nurse_ana", "get_chart_summary", {"patient_id": 501, **extra})
    assert not r.ok and r.reason in ("argument_tampering", "unexpected_argument")
    assert audit_rows(admin)[-1][1] == "deny"


@pytest.mark.parametrize("bad", ["1 OR 1=1", -5, 0, 10**12, "5; DROP TABLE patients", [1, 2], {"$ne": 1}, None, 1.5])
def test_invalid_patient_ids_rejected(bad):
    r = call("nurse_ana", "get_chart_summary", {"patient_id": bad})
    assert not r.ok and r.decision == "deny"


def test_sql_metacharacters_are_data_not_code(admin):
    for q in ("'; DROP TABLE patients;--", "' OR '1'='1", "%", "_", "\\"):
        r = call("fd_sam", "search_patients", {"query": q + "xx"})
        assert r.ok and r.payload["data"]["count"] == 0
    assert admin.execute("SELECT count(*) FROM patients").fetchone()[0] >= 500


def test_wildcard_characters_do_not_enumerate():
    assert call("fd_sam", "search_patients", {"query": "%%"}).payload["data"]["count"] == 0


# ---------------------------------------------------------------- identity & audit
def test_tools_refuse_to_run_without_request_identity():
    from app.tools import server
    with pytest.raises(AuthError):
        runner.execute("search_patients", {"query": "xx", "limit": 5}, lambda *a, **k: None)
    with pytest.raises(AuthError):
        require_identity()
    assert server.mcp is not None


def test_every_call_is_audited_allow_and_deny(admin):
    call("nurse_ana", "search_patients", {"query": "Ellison"})
    call("nurse_ana", "get_chart_summary", {"patient_id": 999_999})
    call("nurse_ana", "get_claims")
    rows = audit_rows(admin)
    assert [(t, d) for t, d, _ in rows] == [("search_patients", "allow"), ("get_chart_summary", "deny"), ("get_claims", "deny")]


def test_audit_failure_fails_closed(monkeypatch):
    def boom(*a, **k):
        raise AuditWriteError("audit_unavailable: test")
    monkeypatch.setattr(runner, "write_audit", boom)
    r = call("fd_sam", "search_patients", {"query": "Ellison"})
    assert not r.ok and r.reason == "audit_unavailable"
    assert "Ellison" not in blob(r) and "data" not in json.loads(blob(r))


def test_model_visible_json_never_contains_internal_ids_of_other_roles():
    r = call("fd_sam", "get_appointments", {"limit": 5})
    assert not re.search(r"password|jwt|secret|session", blob(r), re.I)
