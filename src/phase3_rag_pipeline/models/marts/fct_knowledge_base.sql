{{ config(
    materialized='incremental',
    unique_key='chunk_id'
) }}

WITH staged_data AS (
    SELECT * FROM {{ ref('stg_json_flattened') }}
)

SELECT 
    chunk_id,
    file_name,
    contractor_name,
    service_type,
    raw_text,
    extraction_timestamp,
    metadata_payload,
    
    -- Generate the AI Embedding on the fly
    SNOWFLAKE.CORTEX.EMBED_TEXT_768('e5-base-v2', raw_text) AS vector_embedding

FROM staged_data

{% if is_incremental() %}
  WHERE extraction_timestamp > (SELECT MAX(extraction_timestamp) FROM {{ this }})
{% endif %}
