-- ============================================================================
-- gold_customer_360.sql
-- Gold Layer: Customer 360 Enriched View
--
-- Combines CRM, transaction, and support interaction data into a unified
-- customer 360 table optimized for vector indexing and downstream ML.
-- Materialized as a gold-layer table with full audit columns.
-- ============================================================================

WITH crm_base AS (
    SELECT
        customer_key,
        customer_id,
        email,
        full_name,
        engagement_segment,
        tenure_days,
        country_code,
        ingestion_date
    FROM {{ ref('silver_crm_users') }}
),

/* Placeholder for future transaction fact table join
transaction_agg AS (
    SELECT
        customer_id,
        COUNT(DISTINCT order_id)        AS lifetime_orders,
        SUM(order_total)                AS lifetime_value,
        MAX(order_date)                 AS last_order_date,
        AVG(order_total)                AS avg_order_value
    FROM {{ source('silver', 'transactions') }}
    GROUP BY customer_id
),

support_agg AS (
    SELECT
        customer_id,
        COUNT(DISTINCT ticket_id)       AS total_support_tickets,
        AVG(satisfaction_score)         AS avg_satisfaction,
        MAX(ticket_created_at)          AS last_ticket_date
    FROM {{ source('silver', 'support_tickets') }}
    GROUP BY customer_id
),
*/

final AS (
    SELECT
        c.customer_key,
        c.customer_id,
        c.email,
        c.full_name,
        c.engagement_segment,
        c.tenure_days,
        c.country_code,
        c.ingestion_date,
        CURRENT_TIMESTAMP               AS gold_processed_at
    FROM crm_base c
)

SELECT * FROM final