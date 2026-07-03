-- ============================================================================
-- silver_crm_users.sql
-- Silver → Gold: CRM Users Transformation
--
-- Reads validated data from the Silver layer and:
--   1. Deduplicates by customer_id keeping the latest record
--   2. Normalizes email addresses to lowercase
--   3. Computes derived metrics (age_group, lifetime_value_segment)
--   4. Applies soft-delete filtering
--   5. Outputs to Gold with a surrogate key and audit columns
-- ============================================================================

WITH silver_users AS (
    SELECT *
    FROM {{ source('silver', 'crm_users') }}
    WHERE _deleted_at IS NULL           -- exclude soft-deleted rows
),

deduplicated AS (
    SELECT DISTINCT ON (customer_id)    -- keep most recent per customer_id
        customer_id,
        LOWER(TRIM(email))             AS email,
        INITCAP(TRIM(full_name))       AS full_name,
        signup_date,
        last_login_date,
        COALESCE(score, 0.0)           AS engagement_score,
        country_code,
        lifecycle_stage,
        created_at,
        updated_at,
        ingestion_date
    FROM silver_users
    ORDER BY customer_id, updated_at DESC NULLS LAST
),

enriched AS (
    SELECT
        -- Surrogate key for analytics
        MD5(customer_id || '_' || email) AS customer_key,

        customer_id,
        email,
        full_name,
        signup_date,
        last_login_date,
        engagement_score,

        -- Segment by engagement
        CASE
            WHEN engagement_score >= 90.0 THEN 'VIP'
            WHEN engagement_score >= 70.0 THEN 'Active'
            WHEN engagement_score >= 40.0 THEN 'Occasional'
            ELSE 'At-Risk'
        END                              AS engagement_segment,

        -- Customer tenure
        DATE_PART('day', NOW()::date - signup_date::date)::INTEGER AS tenure_days,

        country_code,
        lifecycle_stage,
        created_at,
        updated_at,
        ingestion_date
    FROM deduplicated
)

SELECT * FROM enriched