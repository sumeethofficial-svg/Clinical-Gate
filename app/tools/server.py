"""FastMCP tool server. Tool signatures deliberately contain NO identity parameters: who is calling
comes from the verified request context. Unknown arguments are rejected by the schema
(additionalProperties: false). The model has no SQL tool and no raw DB access."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from app.tools import impl
from app.tools.runner import execute

mcp = FastMCP("ClinicalGate")

PatientId = Annotated[int, Field(ge=1, le=1_000_000_000, description="Numeric patient id")]
Limit = Annotated[int, Field(ge=1, le=50)]


@mcp.tool
def search_patients(
    query: Annotated[str, Field(min_length=2, max_length=100, description="Name, phone fragment or MRN")],
    limit: Annotated[int, Field(ge=1, le=25)] = 10,
) -> dict:
    """Find patients you are allowed to see by name, MRN or phone. Returns only the fields your role may view."""
    return execute("search_patients", {"query": query, "limit": limit}, impl.search_patients)


@mcp.tool
def get_appointments(
    patient_id: PatientId | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    status: Literal["scheduled", "completed", "cancelled", "no_show"] | None = None,
    limit: Limit = 25,
) -> dict:
    """List appointments, optionally filtered by patient, date range (inclusive) and status."""
    return execute("get_appointments", {"patient_id": patient_id, "date_from": date_from, "date_to": date_to,
                                         "status": status, "limit": limit}, impl.get_appointments)


@mcp.tool
def get_chart_summary(patient_id: PatientId) -> dict:
    """Clinical summary for one patient on your care team: demographics, diagnoses, recent encounters and notes."""
    return execute("get_chart_summary", {"patient_id": patient_id}, impl.get_chart_summary)


@mcp.tool
def get_claims(
    patient_id: PatientId | None = None,
    status: Literal["submitted", "paid", "denied", "pending"] | None = None,
    limit: Limit = 25,
) -> dict:
    """Claims with CPT/ICD-10 codes and amounts. No clinical note text is ever included."""
    return execute("get_claims", {"patient_id": patient_id, "status": status, "limit": limit}, impl.get_claims)


@mcp.tool
def cohort_counts(
    dimension: Literal["sex", "age_band", "diagnosis_category", "claim_status", "appointment_status", "encounter_class"],
    dx_category: Annotated[str | None, Field(pattern=r"^[A-Z][0-9]{2}$", description="ICD-10 category, e.g. E11")] = None,
    sex: Literal["F", "M"] | None = None,
    age_band: Literal["0-17", "18-39", "40-64", "65+"] | None = None,
) -> dict:
    """De-identified patient counts by one dimension. Cells under 5 patients are suppressed."""
    return execute("cohort_counts", {"dimension": dimension, "dx_category": dx_category, "sex": sex,
                                     "age_band": age_band}, impl.cohort_counts)
