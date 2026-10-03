-- Roles, column-level grants and row-level security.
-- Model: one NOLOGIN Postgres role per persona. The API connects as cg_app (NOINHERIT, no table
-- privileges of its own) and does `SET LOCAL ROLE cg_<persona>` per request, so Postgres itself
-- enforces what each persona may read. Deny by default: no policy => no rows.

DO $$
DECLARE r text;
BEGIN
  FOREACH r IN ARRAY ARRAY['cg_front_desk','cg_nurse','cg_billing','cg_manager','cg_auth','cg_audit'] LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
      EXECUTE format('CREATE ROLE %I NOLOGIN NOINHERIT', r);
    END IF;
  END LOOP;
END $$;

-- Session context helpers (values are SET LOCAL by the API from the verified token + DB lookup).
CREATE OR REPLACE FUNCTION cg_current_user_id() RETURNS int LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.user_id', true), '')::int $$;

CREATE OR REPLACE FUNCTION cg_current_provider_id() RETURNS int LANGUAGE sql STABLE AS
$$ SELECT nullif(current_setting('app.provider_id', true), '')::int $$;

-- Runs as the invoker, so care_team's own RLS applies inside the check.
CREATE OR REPLACE FUNCTION cg_on_care_team(pid int) RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT EXISTS (SELECT 1 FROM care_team ct
                  WHERE ct.patient_id = pid AND ct.provider_id = cg_current_provider_id()) $$;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO cg_front_desk, cg_nurse, cg_billing, cg_manager, cg_auth, cg_audit;

ALTER TABLE providers          ENABLE ROW LEVEL SECURITY;
ALTER TABLE users              ENABLE ROW LEVEL SECURITY;
ALTER TABLE patients           ENABLE ROW LEVEL SECURITY;
ALTER TABLE care_team          ENABLE ROW LEVEL SECURITY;
ALTER TABLE appointments       ENABLE ROW LEVEL SECURITY;
ALTER TABLE encounters         ENABLE ROW LEVEL SECURITY;
ALTER TABLE diagnoses          ENABLE ROW LEVEL SECURITY;
ALTER TABLE clinical_notes     ENABLE ROW LEVEL SECURITY;
ALTER TABLE claims             ENABLE ROW LEVEL SECURITY;
ALTER TABLE claim_lines        ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log          ENABLE ROW LEVEL SECURITY;
ALTER TABLE cohort_release_log ENABLE ROW LEVEL SECURITY;  -- no policies, no grants: definer-only

-- ---------------------------------------------------------------- column masking (GRANTs)
-- front_desk: scheduling only. No DOB, email, addresses, appointment reason, or anything clinical.
GRANT SELECT (id, mrn, full_name, phone) ON patients TO cg_front_desk;
GRANT SELECT (id, patient_id, provider_id, starts_at, appointment_type, status) ON appointments TO cg_front_desk;
-- nurse: full chart, but not billing data.
GRANT SELECT (id, mrn, full_name, dob, sex, phone, email, home_address) ON patients TO cg_nurse;
GRANT SELECT ON appointments, encounters, diagnoses, clinical_notes, care_team TO cg_nurse;
-- billing: codes and claims; no note text, no appointment reason, no contact details beyond billing address.
GRANT SELECT (id, mrn, full_name, billing_address) ON patients TO cg_billing;
GRANT SELECT (id, encounter_id, patient_id, icd10_code) ON diagnoses TO cg_billing;
GRANT SELECT ON claims, claim_lines TO cg_billing;
-- provider directory (names only)
GRANT SELECT (id, full_name, specialty, kind) ON providers TO cg_front_desk, cg_nurse, cg_billing;
-- manager: NO privileges on any patient-level table. Aggregates only, via cg_cohort_counts (003).
-- auth path: only the login / identity-resolution code uses this role.
GRANT SELECT (id, username, password_hash, role, provider_id, display_name, active) ON users TO cg_auth;
-- audit: write-only for cg_audit; each persona reads its own entries; manager reads all minus args.
GRANT INSERT ON audit_log TO cg_audit;
GRANT USAGE ON SEQUENCE audit_log_id_seq TO cg_audit;
GRANT SELECT (id, ts, user_id, username, role, session_id, tool, args, decision, reason, rows_returned)
      ON audit_log TO cg_front_desk, cg_nurse, cg_billing;
GRANT SELECT (id, ts, user_id, username, role, tool, decision, reason, rows_returned)
      ON audit_log TO cg_manager;

-- ---------------------------------------------------------------- row-level security
CREATE POLICY providers_read ON providers FOR SELECT TO cg_front_desk, cg_nurse, cg_billing USING (true);
CREATE POLICY users_auth     ON users     FOR SELECT TO cg_auth USING (true);

CREATE POLICY patients_front_desk ON patients FOR SELECT TO cg_front_desk USING (true);
CREATE POLICY patients_billing    ON patients FOR SELECT TO cg_billing    USING (true);
CREATE POLICY patients_nurse      ON patients FOR SELECT TO cg_nurse      USING (cg_on_care_team(id));

CREATE POLICY care_team_nurse ON care_team FOR SELECT TO cg_nurse USING (provider_id = cg_current_provider_id());

CREATE POLICY appointments_front_desk ON appointments FOR SELECT TO cg_front_desk USING (true);
CREATE POLICY appointments_nurse      ON appointments FOR SELECT TO cg_nurse USING (cg_on_care_team(patient_id));

CREATE POLICY encounters_nurse ON encounters     FOR SELECT TO cg_nurse USING (cg_on_care_team(patient_id));
CREATE POLICY diagnoses_nurse  ON diagnoses      FOR SELECT TO cg_nurse USING (cg_on_care_team(patient_id));
CREATE POLICY notes_nurse      ON clinical_notes FOR SELECT TO cg_nurse USING (cg_on_care_team(patient_id));
CREATE POLICY diagnoses_billing ON diagnoses     FOR SELECT TO cg_billing USING (true);

CREATE POLICY claims_billing       ON claims      FOR SELECT TO cg_billing USING (true);
CREATE POLICY claim_lines_billing  ON claim_lines FOR SELECT TO cg_billing USING (true);

CREATE POLICY audit_select_own ON audit_log FOR SELECT TO cg_front_desk, cg_nurse, cg_billing
    USING (user_id = cg_current_user_id());
CREATE POLICY audit_select_all_manager ON audit_log FOR SELECT TO cg_manager USING (true);
CREATE POLICY audit_insert ON audit_log FOR INSERT TO cg_audit WITH CHECK (true);
