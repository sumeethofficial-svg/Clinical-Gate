"""Tool bodies. Every query runs inside `scoped(identity)`, i.e. as the caller's Postgres persona role,
so row-level security and column grants decide what comes back. Explicit column lists are used so a
missing grant fails loudly instead of silently widening a result."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.auth.tokens import Identity
from app.db.session import scoped
from app.policy.rules import PATIENT_COLUMNS, SEARCHABLE, PolicyDenied

NOT_ACCESSIBLE = "No accessible record was found for that request."


@dataclass
class Out:
    payload: dict[str, Any]
    rows: int


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def search_patients(identity: Identity, query: str, limit: int) -> Out:
    cols = PATIENT_COLUMNS[identity.role]
    search_cols = SEARCHABLE[identity.role]
    where = " OR ".join(f"{c}::text ILIKE %(q)s" for c in search_cols)
    sql = f"SELECT {', '.join(cols)} FROM patients WHERE {where} ORDER BY full_name, id LIMIT %(lim)s"
    with scoped(identity) as conn:
        rows = conn.execute(sql, {"q": _like(query.strip()), "lim": limit}).fetchall()
    return Out({"patients": rows, "count": len(rows)}, len(rows))


def get_appointments(identity: Identity, patient_id, date_from, date_to, status, limit: int) -> Out:
    reason_col = ", a.reason" if identity.role == "nurse" else ""
    clauses, params = [], {"lim": limit}
    for name, expr, val in (("pid", "a.patient_id = %(pid)s", patient_id), ("dfrom", "a.starts_at >= %(dfrom)s", date_from),
                            ("dto", "a.starts_at < (%(dto)s::date + 1)", date_to), ("st", "a.status = %(st)s", status)):
        if val is not None:
            clauses.append(expr)
            params[name] = val
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = (f"SELECT a.id, a.patient_id, p.full_name AS patient_name, a.starts_at, a.appointment_type, a.status, "
           f"pr.full_name AS provider_name{reason_col} FROM appointments a "
           f"JOIN patients p ON p.id = a.patient_id JOIN providers pr ON pr.id = a.provider_id "
           f"{where} ORDER BY a.starts_at, a.id LIMIT %(lim)s")
    with scoped(identity) as conn:
        rows = conn.execute(sql, params).fetchall()
        if not rows and patient_id is not None and identity.role == "nurse":
            visible = conn.execute("SELECT 1 FROM patients WHERE id = %s", (patient_id,)).fetchone()
            if not visible:
                raise PolicyDenied("patient_not_accessible", NOT_ACCESSIBLE)
    for r in rows:
        r["starts_at"] = r["starts_at"].isoformat(timespec="minutes")
    return Out({"appointments": rows, "count": len(rows)}, len(rows))


def get_chart_summary(identity: Identity, patient_id: int) -> Out:
    with scoped(identity) as conn:
        pat = conn.execute("SELECT id, mrn, full_name, dob, sex, phone, email, home_address FROM patients WHERE id = %s",
                           (patient_id,)).fetchone()
        if not pat:   # same answer whether the patient does not exist or is off the care team: no existence oracle
            raise PolicyDenied("patient_not_accessible", NOT_ACCESSIBLE)
        dx = conn.execute(
            "SELECT d.icd10_code, d.description, max(e.encounter_date) AS last_seen FROM diagnoses d "
            "JOIN encounters e ON e.id = d.encounter_id WHERE d.patient_id = %s "
            "GROUP BY d.icd10_code, d.description ORDER BY last_seen DESC, d.icd10_code LIMIT 15", (patient_id,)).fetchall()
        enc = conn.execute(
            "SELECT id, encounter_date, encounter_class FROM encounters WHERE patient_id = %s "
            "ORDER BY encounter_date DESC, id DESC LIMIT 5", (patient_id,)).fetchall()
        notes = conn.execute(
            "SELECT n.id, e.encounter_date, n.note_text FROM clinical_notes n JOIN encounters e ON e.id = n.encounter_id "
            "WHERE n.patient_id = %s ORDER BY e.encounter_date DESC, n.id DESC LIMIT 5", (patient_id,)).fetchall()
        appts = conn.execute(
            "SELECT id, starts_at, appointment_type, status, reason FROM appointments WHERE patient_id = %s AND status = 'scheduled' "
            "ORDER BY starts_at LIMIT 3", (patient_id,)).fetchall()
    for r in appts:
        r["starts_at"] = r["starts_at"].isoformat(timespec="minutes")
    for r in dx + enc + notes:
        for k, v in list(r.items()):
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
    pat["dob"] = pat["dob"].isoformat()
    payload = {"patient": pat, "diagnoses": dx, "recent_encounters": enc, "recent_notes": notes, "upcoming_appointments": appts}
    return Out(payload, 1 + len(dx) + len(enc) + len(notes) + len(appts))


def get_claims(identity: Identity, patient_id, status, limit: int) -> Out:
    clauses, params = [], {"lim": limit}
    if patient_id is not None:
        clauses.append("c.patient_id = %(pid)s")
        params["pid"] = patient_id
    if status is not None:
        clauses.append("c.status = %(st)s")
        params["st"] = status
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with scoped(identity) as conn:
        claims = conn.execute(
            f"SELECT c.id, c.claim_number, c.patient_id, p.full_name AS patient_name, c.status, c.total_amount, c.submitted_at "
            f"FROM claims c JOIN patients p ON p.id = c.patient_id {where} ORDER BY c.submitted_at DESC, c.id DESC LIMIT %(lim)s",
            params).fetchall()
        lines = conn.execute(
            "SELECT claim_id, cpt_code, icd10_code, amount FROM claim_lines WHERE claim_id = ANY(%s) ORDER BY claim_id, id",
            ([c["id"] for c in claims],)).fetchall() if claims else []
    by_claim: dict[int, list] = {}
    for ln in lines:
        by_claim.setdefault(ln["claim_id"], []).append({"cpt_code": ln["cpt_code"], "icd10_code": ln["icd10_code"], "amount": str(ln["amount"])})
    for c in claims:
        c["total_amount"] = str(c["total_amount"])
        c["submitted_at"] = c["submitted_at"].isoformat()
        c["lines"] = by_claim.get(c["id"], [])
    return Out({"claims": claims, "count": len(claims)}, len(claims))


def cohort_counts(identity: Identity, dimension: str, dx_category, sex, age_band) -> Out:
    with scoped(identity) as conn:
        rows = conn.execute("SELECT bucket, patient_count, suppressed, note FROM cg_cohort_counts(%s,%s,%s,%s)",
                            (dimension, dx_category, sex, age_band)).fetchall()
    refused = next((r["note"] for r in rows if r["note"] and r["note"].startswith("refused")), None)
    if refused:
        raise PolicyDenied("cohort_refused", "This breakdown is not available (" + refused + ").")
    return Out({"cells": rows, "min_cell_size": 5, "dimension": dimension}, len(rows))
