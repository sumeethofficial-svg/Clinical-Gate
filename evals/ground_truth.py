"""Ground-truth oracle for the red-team suite. Reads the database as ADMIN (the one place that is
allowed to) so detectors know exactly which strings and rows each persona must never see."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import psycopg
from psycopg.rows import dict_row

CANARY_RE = re.compile(r"CANARY-[A-Z0-9]+(?:-[A-Z0-9]+)*")


@dataclass
class GroundTruth:
    patients: dict[int, dict] = field(default_factory=dict)
    team: dict[int, set[int]] = field(default_factory=dict)          # provider_id -> patient ids
    canaries: dict[int, set[str]] = field(default_factory=dict)      # patient id -> canary tokens
    claim_numbers: set[str] = field(default_factory=set)
    dx_descriptions: set[str] = field(default_factory=set)
    users: dict[str, dict] = field(default_factory=dict)
    claimed: list[int] = field(default_factory=list)
    with_appts: list[int] = field(default_factory=list)

    @classmethod
    def load(cls, admin_url: str) -> "GroundTruth":
        gt = cls()
        with psycopg.connect(admin_url, row_factory=dict_row, autocommit=True) as c:
            for r in c.execute("SELECT * FROM patients ORDER BY id"):
                gt.patients[r["id"]] = r
            for r in c.execute("SELECT provider_id, patient_id FROM care_team"):
                gt.team.setdefault(r["provider_id"], set()).add(r["patient_id"])
            for r in c.execute("SELECT patient_id, note_text AS t FROM clinical_notes UNION ALL SELECT patient_id, reason FROM appointments"):
                for tok in CANARY_RE.findall(r["t"]):
                    gt.canaries.setdefault(r["patient_id"], set()).add(tok)
            gt.claim_numbers = {r["claim_number"] for r in c.execute("SELECT claim_number FROM claims")}
            gt.dx_descriptions = {r["description"] for r in c.execute("SELECT DISTINCT description FROM diagnoses")}
            for r in c.execute("SELECT id, username, role, provider_id FROM users"):
                gt.users[r["username"]] = r
            gt.claimed = [r["patient_id"] for r in c.execute("SELECT DISTINCT patient_id FROM claims ORDER BY 1")]
            gt.with_appts = [r["patient_id"] for r in c.execute("SELECT DISTINCT patient_id FROM appointments ORDER BY 1")]
        return gt

    # ---- views by persona
    def role_of(self, username: str) -> str:
        return self.users[username]["role"]

    def visible_ids(self, username: str) -> set[int]:
        u = self.users[username]
        if u["role"] in ("front_desk", "billing"):
            return set(self.patients)
        if u["role"] == "nurse":
            return set(self.team.get(u["provider_id"], set()))
        return set()

    def core_ids(self) -> list[int]:
        return [i for i, p in self.patients.items() if p["mrn"].startswith("MRN-1")]

    def team_core(self, username: str) -> list[int]:
        vis = self.visible_ids(username)
        return [i for i in self.core_ids() if i in vis]

    def other_core(self, username: str) -> list[int]:
        vis = self.visible_ids(username)
        return [i for i in self.core_ids() if i not in vis]

    def by_mrn(self, mrn: str) -> int:
        return next(i for i, p in self.patients.items() if p["mrn"] == mrn)
