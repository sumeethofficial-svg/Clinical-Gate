-- Aggregates for the clinic manager. SECURITY DEFINER: the only path from cg_manager to patient data,
-- and it only ever returns counts. Controls: dimension allowlist, small-cell suppression (<5),
-- differencing guard against overlapping cohorts, one-breakdown-per-population rule, hourly budget.

CREATE OR REPLACE FUNCTION cg_array_diff_count(a int[], b int[]) RETURNS int
LANGUAGE sql IMMUTABLE AS
$$ SELECT count(*)::int FROM unnest(a) AS x WHERE x <> ALL (b) $$;

CREATE OR REPLACE FUNCTION cg_cohort_counts(
    p_dimension   text,
    p_dx_category text DEFAULT NULL,
    p_sex         text DEFAULT NULL,
    p_age_band    text DEFAULT NULL
) RETURNS TABLE (bucket text, patient_count int, suppressed boolean, note text)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
    c_min           CONSTANT int := 5;
    c_window        CONSTANT interval := interval '24 hours';
    c_hourly_budget CONSTANT int := 60;
    v_user          int := nullif(current_setting('app.user_id', true), '')::int;
    v_query         uuid := gen_random_uuid();
    v_pop           int[];
    v_domain        text[];
    v_hidden        boolean := false;
    v_hidden_primary boolean;
    v_other_dim_prior boolean := false;
    v_prior         record;
    rec             record;
    v_calls         int;
    v_sup           boolean;
    v_note          text;
BEGIN
    IF v_user IS NULL THEN RAISE EXCEPTION 'no identity in session'; END IF;
    IF p_dimension NOT IN ('sex','age_band','diagnosis_category','claim_status','appointment_status','encounter_class') THEN
        RAISE EXCEPTION 'invalid dimension';
    END IF;
    IF p_sex IS NOT NULL AND p_sex NOT IN ('F','M') THEN RAISE EXCEPTION 'invalid sex filter'; END IF;
    IF p_age_band IS NOT NULL AND p_age_band NOT IN ('0-17','18-39','40-64','65+') THEN RAISE EXCEPTION 'invalid age_band filter'; END IF;
    IF p_dx_category IS NOT NULL AND p_dx_category !~ '^[A-Z][0-9]{2}$' THEN RAISE EXCEPTION 'invalid dx_category filter'; END IF;

    SELECT count(*) INTO v_calls FROM audit_log
     WHERE user_id = v_user AND tool = 'cohort_counts' AND ts > now() - interval '1 hour';
    IF v_calls >= c_hourly_budget THEN
        RETURN QUERY SELECT '(all)'::text, NULL::int, true, 'refused: hourly query budget exhausted'::text;
        RETURN;
    END IF;

    SELECT coalesce(array_agg(p.id ORDER BY p.id), '{}') INTO v_pop
      FROM patients p
     WHERE (p_sex IS NULL OR p.sex = p_sex)
       AND (p_age_band IS NULL OR (CASE
              WHEN extract(year FROM age(current_date, p.dob)) < 18 THEN '0-17'
              WHEN extract(year FROM age(current_date, p.dob)) < 40 THEN '18-39'
              WHEN extract(year FROM age(current_date, p.dob)) < 65 THEN '40-64'
              ELSE '65+' END) = p_age_band)
       AND (p_dx_category IS NULL OR EXISTS (
              SELECT 1 FROM diagnoses d WHERE d.patient_id = p.id AND left(d.icd10_code, 3) = p_dx_category));

    IF cardinality(v_pop) < c_min THEN
        RETURN QUERY SELECT '(all)'::text, NULL::int, true, 'suppressed: population below minimum cell size'::text;
        RETURN;
    END IF;

    -- Same population already released under a different breakdown? (Two partitions of one population
    -- let a reader recover a suppressed cell by subtraction.)
    FOR v_prior IN
        SELECT label, ids FROM cohort_release_log
         WHERE user_id = v_user AND ts > now() - c_window AND label LIKE 'pop:%'
    LOOP
        IF cg_array_diff_count(v_pop, v_prior.ids) + cg_array_diff_count(v_prior.ids, v_pop) < c_min
           AND split_part(v_prior.label, ':', 2) <> p_dimension THEN
            v_other_dim_prior := true;
            IF split_part(v_prior.label, ':', 3) = 'hidden' THEN
                RETURN QUERY SELECT '(all)'::text, NULL::int, true,
                    'refused: this population was already released under another breakdown with suppressed cells'::text;
                RETURN;
            END IF;
        END IF;
    END LOOP;

    v_domain := CASE p_dimension
        WHEN 'sex'                THEN ARRAY['F','M']
        WHEN 'age_band'           THEN ARRAY['0-17','18-39','40-64','65+']
        WHEN 'claim_status'       THEN ARRAY['denied','paid','pending','submitted']
        WHEN 'appointment_status' THEN ARRAY['cancelled','completed','no_show','scheduled']
        ELSE ARRAY[]::text[] END;

    -- First pass: would any cell be suppressed under primary suppression alone?
    SELECT coalesce(bool_or(cardinality(x.ids) < c_min), false) INTO v_hidden_primary FROM (
        WITH pop AS (
            SELECT p.id, p.sex, CASE
                WHEN extract(year FROM age(current_date, p.dob)) < 18 THEN '0-17'
                WHEN extract(year FROM age(current_date, p.dob)) < 40 THEN '18-39'
                WHEN extract(year FROM age(current_date, p.dob)) < 65 THEN '40-64'
                ELSE '65+' END AS age_band
              FROM patients p WHERE p.id = ANY (v_pop)),
        raw AS (
            SELECT pop.id AS pid, pop.sex AS b FROM pop WHERE p_dimension = 'sex'
            UNION ALL SELECT pop.id, pop.age_band FROM pop WHERE p_dimension = 'age_band'
            UNION ALL SELECT d.patient_id, left(d.icd10_code, 3) FROM diagnoses d
                       WHERE p_dimension = 'diagnosis_category' AND d.patient_id = ANY (v_pop)
            UNION ALL SELECT c.patient_id, c.status FROM claims c
                       WHERE p_dimension = 'claim_status' AND c.patient_id = ANY (v_pop)
            UNION ALL SELECT a.patient_id, a.status FROM appointments a
                       WHERE p_dimension = 'appointment_status' AND a.patient_id = ANY (v_pop)
            UNION ALL SELECT e.patient_id, e.encounter_class FROM encounters e
                       WHERE p_dimension = 'encounter_class' AND e.patient_id = ANY (v_pop)),
        cells AS (SELECT r.b, array_agg(DISTINCT r.pid ORDER BY r.pid) AS ids FROM raw r GROUP BY r.b),
        dom AS (SELECT unnest(v_domain) AS b)
        SELECT coalesce(cells.ids, '{}'::int[]) AS ids FROM cells FULL JOIN dom ON dom.b = cells.b
    ) x;

    IF v_hidden_primary AND v_other_dim_prior THEN
        RETURN QUERY SELECT '(all)'::text, NULL::int, true,
            'refused: this population was already released under another breakdown'::text;
        RETURN;
    END IF;

    FOR rec IN
        WITH pop AS (
            SELECT p.id, p.sex, CASE
                WHEN extract(year FROM age(current_date, p.dob)) < 18 THEN '0-17'
                WHEN extract(year FROM age(current_date, p.dob)) < 40 THEN '18-39'
                WHEN extract(year FROM age(current_date, p.dob)) < 65 THEN '40-64'
                ELSE '65+' END AS age_band
              FROM patients p WHERE p.id = ANY (v_pop)),
        raw AS (
            SELECT pop.id AS pid, pop.sex AS b FROM pop WHERE p_dimension = 'sex'
            UNION ALL SELECT pop.id, pop.age_band FROM pop WHERE p_dimension = 'age_band'
            UNION ALL SELECT d.patient_id, left(d.icd10_code, 3) FROM diagnoses d
                       WHERE p_dimension = 'diagnosis_category' AND d.patient_id = ANY (v_pop)
            UNION ALL SELECT c.patient_id, c.status FROM claims c
                       WHERE p_dimension = 'claim_status' AND c.patient_id = ANY (v_pop)
            UNION ALL SELECT a.patient_id, a.status FROM appointments a
                       WHERE p_dimension = 'appointment_status' AND a.patient_id = ANY (v_pop)
            UNION ALL SELECT e.patient_id, e.encounter_class FROM encounters e
                       WHERE p_dimension = 'encounter_class' AND e.patient_id = ANY (v_pop)),
        cells AS (SELECT r.b, array_agg(DISTINCT r.pid ORDER BY r.pid) AS ids FROM raw r GROUP BY r.b),
        dom AS (SELECT unnest(v_domain) AS b)
        SELECT coalesce(cells.b, dom.b) AS cell_bucket, coalesce(cells.ids, '{}'::int[]) AS cell_ids
          FROM cells FULL JOIN dom ON dom.b = cells.b
         ORDER BY 1
    LOOP
        v_sup := cardinality(rec.cell_ids) < c_min;
        v_note := NULL;
        IF NOT v_sup THEN
            -- Differencing guard: a cell that differs from an earlier released cell by 1..4 patients
            -- (one containing the other) would reveal those patients by subtraction.
            FOR v_prior IN
                SELECT ids FROM cohort_release_log
                 WHERE user_id = v_user AND ts > now() - c_window AND label LIKE 'cell:%' AND query_id <> v_query
            LOOP
                IF (cg_array_diff_count(rec.cell_ids, v_prior.ids) BETWEEN 1 AND c_min - 1
                        AND cg_array_diff_count(v_prior.ids, rec.cell_ids) = 0)
                   OR (cg_array_diff_count(v_prior.ids, rec.cell_ids) BETWEEN 1 AND c_min - 1
                        AND cg_array_diff_count(rec.cell_ids, v_prior.ids) = 0) THEN
                    v_sup := true;
                    v_note := 'suppressed: differencing guard';
                    EXIT;
                END IF;
            END LOOP;
        END IF;
        IF v_sup AND v_note IS NULL THEN v_note := 'suppressed: fewer than 5 patients'; END IF;
        IF v_sup THEN
            v_hidden := true;
            bucket := rec.cell_bucket; patient_count := NULL; suppressed := true; note := v_note;
        ELSE
            INSERT INTO cohort_release_log (user_id, query_id, label, ids)
            VALUES (v_user, v_query, 'cell:' || p_dimension || ':' || rec.cell_bucket, rec.cell_ids);
            bucket := rec.cell_bucket; patient_count := cardinality(rec.cell_ids); suppressed := false; note := NULL;
        END IF;
        RETURN NEXT;
    END LOOP;

    INSERT INTO cohort_release_log (user_id, query_id, label, ids)
    VALUES (v_user, v_query, 'pop:' || p_dimension || ':' || CASE WHEN v_hidden THEN 'hidden' ELSE 'visible' END, v_pop);
END;
$$;

REVOKE ALL ON FUNCTION cg_cohort_counts(text, text, text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION cg_cohort_counts(text, text, text, text) TO cg_manager;
