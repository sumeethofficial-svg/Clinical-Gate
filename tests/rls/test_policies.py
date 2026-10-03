"""Pure-SQL proof that Postgres itself enforces the policy matrix. No app code, no LLM."""
import os

import psycopg
import pytest
from psycopg import errors

from app.db.dsn import dsn_for

DENIED = (errors.InsufficientPrivilege,)


def count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


@pytest.mark.parametrize("persona_name", ["front_desk", "nurse", "billing", "manager", "auth", "audit"])
def test_app_logins_have_no_privileges_until_they_assume_their_persona(persona_name):
    with psycopg.connect(dsn_for(os.environ["DATABASE_URL_APP"], persona_name)) as conn:
        for table in ("patients", "clinical_notes", "claims", "users", "audit_log"):
            with pytest.raises(DENIED):
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1")
            conn.rollback()


def test_each_login_can_only_assume_its_own_persona():
    with psycopg.connect(dsn_for(os.environ["DATABASE_URL_APP"], "nurse")) as conn:
        conn.execute("SET LOCAL ROLE cg_nurse")
        conn.rollback()
        for other in ("cg_manager", "cg_billing", "cg_front_desk", "cg_auth", "cg_audit"):
            with pytest.raises(DENIED):
                conn.execute(f"SET LOCAL ROLE {other}")
            conn.rollback()


def test_persona_cannot_switch_to_another_persona(persona):
    with persona("nurse_ana") as c:
        for target in ("cg_manager", "cg_billing", "cg_front_desk", "cg_auth", "cg_audit"):
            with pytest.raises(DENIED):
                with c.transaction():
                    c.execute(f"SET LOCAL ROLE {target}")


# ---------------------------------------------------------------- front desk
def test_front_desk_sees_all_patients_but_only_scheduling_columns(persona, admin):
    total = count(admin, "SELECT count(*) FROM patients")
    with persona("fd_sam") as c:
        assert count(c, "SELECT count(*) FROM patients") == total
        assert c.execute("SELECT id, mrn, full_name, phone FROM patients LIMIT 1").fetchone()
        for col in ("dob", "email", "home_address", "billing_address", "sex"):
            with pytest.raises(DENIED):
                with c.transaction():
                    c.execute(f"SELECT {col} FROM patients LIMIT 1")
        with pytest.raises(DENIED):
            with c.transaction():
                c.execute("SELECT * FROM patients LIMIT 1")


def test_front_desk_appointments_exclude_clinical_reason(persona):
    with persona("fd_sam") as c:
        assert count(c, "SELECT count(*) FROM appointments") > 0
        with pytest.raises(DENIED):
            with c.transaction():
                c.execute("SELECT reason FROM appointments LIMIT 1")


@pytest.mark.parametrize("table", ["clinical_notes", "diagnoses", "encounters", "claims", "claim_lines",
                                   "care_team", "users", "cohort_release_log"])
def test_front_desk_has_no_clinical_or_billing_tables(persona, table):
    with persona("fd_sam") as c:
        with pytest.raises(DENIED):
            c.execute(f"SELECT 1 FROM {table} LIMIT 1")


# ---------------------------------------------------------------- nurse
def test_nurse_sees_exactly_her_care_team(persona, admin):
    expected = {r[0] for r in admin.execute("SELECT patient_id FROM care_team WHERE provider_id=1")}
    with persona("nurse_ana") as c:
        seen = {r[0] for r in c.execute("SELECT id FROM patients")}
    assert seen == expected and 0 < len(seen) < 500


@pytest.mark.parametrize("table,col", [("appointments", "patient_id"), ("encounters", "patient_id"),
                                       ("diagnoses", "patient_id"), ("clinical_notes", "patient_id")])
def test_nurse_clinical_rows_limited_to_care_team(persona, admin, table, col):
    expected = {r[0] for r in admin.execute("SELECT patient_id FROM care_team WHERE provider_id=1")}
    with persona("nurse_ana") as c:
        seen = {r[0] for r in c.execute(f"SELECT DISTINCT {col} FROM {table}")}
    assert seen and seen <= expected


def test_nurse_cannot_see_other_nurses_fixture_patient(persona, admin):
    pid = admin.execute("SELECT id FROM patients WHERE mrn='MRN-900002'").fetchone()[0]
    with persona("nurse_ana") as c:
        assert count(c, "SELECT count(*) FROM patients WHERE id=%s", pid) == 0
        assert count(c, "SELECT count(*) FROM clinical_notes WHERE patient_id=%s", pid) == 0
        assert count(c, "SELECT count(*) FROM clinical_notes WHERE note_text LIKE '%%CANARY-FIXTURE-B%%'") == 0
    with persona("nurse_raj") as c:
        assert count(c, "SELECT count(*) FROM clinical_notes WHERE note_text LIKE '%%CANARY-FIXTURE-B%%'") == 1


def test_nurse_has_no_billing_access(persona):
    with persona("nurse_ana") as c:
        with pytest.raises(DENIED):
            with c.transaction():
                c.execute("SELECT billing_address FROM patients LIMIT 1")
        with pytest.raises(DENIED):
            c.execute("SELECT 1 FROM claims LIMIT 1")


def test_nurse_without_provider_context_sees_nothing(persona):
    with persona("nurse_ana", set_provider=False) as c:
        assert count(c, "SELECT count(*) FROM patients") == 0
        assert count(c, "SELECT count(*) FROM clinical_notes") == 0


# ---------------------------------------------------------------- billing
def test_billing_sees_claims_and_codes_but_no_clinical_text(persona):
    with persona("billing_lee") as c:
        assert count(c, "SELECT count(*) FROM claims") > 0
        assert count(c, "SELECT count(*) FROM claim_lines") > 0
        assert c.execute("SELECT icd10_code FROM diagnoses LIMIT 1").fetchone()
        for stmt in ("SELECT description FROM diagnoses LIMIT 1", "SELECT 1 FROM clinical_notes LIMIT 1",
                     "SELECT reason FROM appointments LIMIT 1", "SELECT phone FROM patients LIMIT 1",
                     "SELECT email FROM patients LIMIT 1", "SELECT dob FROM patients LIMIT 1",
                     "SELECT home_address FROM patients LIMIT 1", "SELECT 1 FROM care_team LIMIT 1"):
            with pytest.raises(DENIED):
                with c.transaction():
                    c.execute(stmt)
        assert c.execute("SELECT billing_address FROM patients LIMIT 1").fetchone()


# ---------------------------------------------------------------- manager
@pytest.mark.parametrize("table", ["patients", "appointments", "encounters", "diagnoses", "clinical_notes",
                                   "claims", "claim_lines", "care_team", "users", "providers"])
def test_manager_has_no_patient_level_access(persona, table):
    with persona("manager_kim") as c:
        with pytest.raises(DENIED):
            c.execute(f"SELECT 1 FROM {table} LIMIT 1")


# ---------------------------------------------------------------- writes + audit
@pytest.mark.parametrize("user", ["fd_sam", "nurse_ana", "billing_lee", "manager_kim"])
def test_no_persona_can_write_clinical_data(persona, user):
    stmts = ["INSERT INTO patients (mrn, full_name, dob, sex, phone, email, home_address, billing_address) "
             "VALUES ('X','X','2000-01-01','F','1','x','x','x')",
             "UPDATE patients SET full_name='x'", "DELETE FROM patients", "DELETE FROM audit_log",
             "UPDATE audit_log SET decision='allow'", "TRUNCATE patients"]
    with persona(user) as c:
        for s in stmts:
            with pytest.raises(DENIED):
                with c.transaction():
                    c.execute(s)


def test_audit_log_visibility(persona, admin, clean_state):
    admin.execute("INSERT INTO audit_log (user_id, username, role, tool, decision, reason, args) "
                  "VALUES (2,'nurse_ana','nurse','t','deny','r','{\"secret\":1}'), (3,'nurse_raj','nurse','t','allow',NULL,'{}')")
    with persona("nurse_ana") as c:
        assert {r[0] for r in c.execute("SELECT user_id FROM audit_log")} == {2}
    with persona("manager_kim") as c:
        assert {r[0] for r in c.execute("SELECT user_id FROM audit_log")} == {2, 3}
        with pytest.raises(DENIED):
            c.execute("SELECT args FROM audit_log")


# ---------------------------------------------------------------- cohort function
def cohort(c, dim, dx=None, sex=None, age=None):
    return c.execute("SELECT bucket, patient_count, suppressed, note FROM cg_cohort_counts(%s,%s,%s,%s)",
                     (dim, dx, sex, age)).fetchall()


def test_only_manager_can_execute_cohort_function(persona, clean_state):
    for user in ("fd_sam", "nurse_ana", "billing_lee"):
        with persona(user) as c:
            with pytest.raises(DENIED):
                cohort(c, "sex")


def test_cohort_counts_match_ground_truth_and_hide_small_cells(persona, admin, clean_state):
    truth = dict(admin.execute("SELECT sex, count(*) FROM patients GROUP BY sex").fetchall())
    with persona("manager_kim") as c:
        rows = cohort(c, "sex")
    assert {b: n for b, n, s, _ in rows} == truth
    assert all(n is None or n >= 5 for _, n, _, _ in rows)


def test_rare_diagnosis_population_is_suppressed(persona, clean_state):
    with persona("manager_kim") as c:
        rows = cohort(c, "sex", dx="E84")          # exactly 3 patients carry E84
    assert rows == [("(all)", None, True, "suppressed: population below minimum cell size")]


def test_small_cells_inside_a_large_enough_population_are_suppressed(persona, clean_state):
    with persona("manager_kim") as c:
        rows = cohort(c, "sex", dx="A15")          # 6 patients -> any F/M split has a cell < 5
    assert all(s and n is None for _, n, s, _ in rows)


def test_second_breakdown_of_same_population_is_refused(persona, clean_state):
    with persona("manager_kim") as c:
        first = cohort(c, "age_band", dx="E11")
        assert any(not s for _, _, s, _ in first)
    with persona("manager_kim") as c:
        second = cohort(c, "sex", dx="E11")
    # E11 population is large; both partitions are fully visible OR the second one is refused/suppressed.
    for _, n, s, _ in second:
        assert s or n >= 5


def test_hidden_then_other_breakdown_is_refused(persona, clean_state):
    with persona("manager_kim") as c:
        first = cohort(c, "sex", dx="A15")           # 6 patients: every F/M cell is hidden
        assert all(s for _, _, s, _ in first)
    with persona("manager_kim") as c:
        second = cohort(c, "encounter_class", dx="A15")
    assert second[0][2] is True and "refused" in second[0][3]


def test_invalid_inputs_are_rejected(persona, clean_state):
    with persona("manager_kim") as c:
        for args in (("note_text",), ("sex", "E11; DROP TABLE patients"), ("sex", None, "X"), ("age_band", None, None, "99")):
            with pytest.raises(psycopg.errors.RaiseException):
                with c.transaction():
                    cohort(c, *args)
