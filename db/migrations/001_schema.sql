-- ClinicalGate schema. SYNTHETIC DATA ONLY: never load real PHI into this database.

CREATE TABLE providers (
    id          serial PRIMARY KEY,
    full_name   text NOT NULL,
    specialty   text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('nurse', 'physician'))
);

CREATE TABLE users (
    id            serial PRIMARY KEY,
    username      text NOT NULL UNIQUE,
    password_hash text NOT NULL,
    role          text NOT NULL CHECK (role IN ('front_desk', 'nurse', 'billing', 'manager')),
    provider_id   int REFERENCES providers(id),
    display_name  text NOT NULL,
    active        boolean NOT NULL DEFAULT true
);

CREATE TABLE patients (
    id              serial PRIMARY KEY,
    mrn             text NOT NULL UNIQUE,
    full_name       text NOT NULL,
    dob             date NOT NULL,
    sex             text NOT NULL CHECK (sex IN ('F', 'M')),
    phone           text NOT NULL,
    email           text NOT NULL,
    home_address    text NOT NULL,
    billing_address text NOT NULL
);
CREATE INDEX patients_name_idx ON patients (lower(full_name));

CREATE TABLE care_team (
    provider_id int NOT NULL REFERENCES providers(id),
    patient_id  int NOT NULL REFERENCES patients(id),
    PRIMARY KEY (provider_id, patient_id)
);
CREATE INDEX care_team_patient_idx ON care_team (patient_id);

CREATE TABLE appointments (
    id               serial PRIMARY KEY,
    patient_id       int NOT NULL REFERENCES patients(id),
    provider_id      int NOT NULL REFERENCES providers(id),
    starts_at        timestamp NOT NULL,
    appointment_type text NOT NULL,
    status           text NOT NULL CHECK (status IN ('scheduled', 'completed', 'cancelled', 'no_show')),
    reason           text NOT NULL   -- clinical free text: masked from front_desk
);
CREATE INDEX appointments_patient_idx ON appointments (patient_id);
CREATE INDEX appointments_date_idx ON appointments (starts_at);

CREATE TABLE encounters (
    id              serial PRIMARY KEY,
    patient_id      int NOT NULL REFERENCES patients(id),
    provider_id     int NOT NULL REFERENCES providers(id),
    encounter_date  date NOT NULL,
    encounter_class text NOT NULL
);
CREATE INDEX encounters_patient_idx ON encounters (patient_id);

CREATE TABLE diagnoses (
    id           serial PRIMARY KEY,
    encounter_id int NOT NULL REFERENCES encounters(id),
    patient_id   int NOT NULL REFERENCES patients(id),
    icd10_code   text NOT NULL,
    description  text NOT NULL
);
CREATE INDEX diagnoses_patient_idx ON diagnoses (patient_id);

CREATE TABLE clinical_notes (
    id                 serial PRIMARY KEY,
    encounter_id       int NOT NULL REFERENCES encounters(id),
    patient_id         int NOT NULL REFERENCES patients(id),
    author_provider_id int NOT NULL REFERENCES providers(id),
    note_text          text NOT NULL
);
CREATE INDEX clinical_notes_patient_idx ON clinical_notes (patient_id);

CREATE TABLE claims (
    id           serial PRIMARY KEY,
    claim_number text NOT NULL UNIQUE,
    patient_id   int NOT NULL REFERENCES patients(id),
    encounter_id int NOT NULL REFERENCES encounters(id),
    status       text NOT NULL CHECK (status IN ('submitted', 'paid', 'denied', 'pending')),
    total_amount numeric(10, 2) NOT NULL,
    submitted_at date NOT NULL
);
CREATE INDEX claims_patient_idx ON claims (patient_id);

CREATE TABLE claim_lines (
    id         serial PRIMARY KEY,
    claim_id   int NOT NULL REFERENCES claims(id),
    patient_id int NOT NULL REFERENCES patients(id),
    cpt_code   text NOT NULL,
    icd10_code text NOT NULL,
    amount     numeric(10, 2) NOT NULL
);
CREATE INDEX claim_lines_claim_idx ON claim_lines (claim_id);

CREATE TABLE audit_log (
    id            bigserial PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    user_id       int,
    username      text,
    role          text,
    session_id    text,
    tool          text NOT NULL,
    args          jsonb NOT NULL DEFAULT '{}'::jsonb,
    decision      text NOT NULL CHECK (decision IN ('allow', 'deny', 'error')),
    reason        text,
    rows_returned int
);
CREATE INDEX audit_log_user_idx ON audit_log (user_id, ts DESC);
CREATE INDEX audit_log_decision_idx ON audit_log (decision, ts DESC);

-- Released-cohort bookkeeping for differencing control (only the definer function touches this).
CREATE TABLE cohort_release_log (
    id       bigserial PRIMARY KEY,
    user_id  int NOT NULL,
    query_id uuid NOT NULL,
    ts       timestamptz NOT NULL DEFAULT now(),
    label    text NOT NULL,
    ids      int[] NOT NULL
);
CREATE INDEX cohort_release_log_user_idx ON cohort_release_log (user_id, ts DESC);
