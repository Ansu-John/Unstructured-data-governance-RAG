{{ config(materialized='view') }}

WITH raw_source AS (
    SELECT * FROM {{ source('raw_zone', 'BRONZE_JSON_RAW') }}
)

SELECT
    RAW_DATA:"Contract_Number"::STRING AS chunk_id,
    RAW_DATA:"File_Name"::STRING AS file_name,
    RAW_DATA:"Contractor_Name"::STRING AS contractor_name,
    RAW_DATA:"Service"::STRING AS service_type,
    
    -- Constructing the text chunk for the AI
    CONCAT(
        'Direct Cost Note: ', COALESCE(RAW_DATA:"Direct_Cost_Explanation"::STRING, ''), 
        ' | Indirect Cost Note: ', COALESCE(RAW_DATA:"Indirect_Cost_Explanation"::STRING, '')
    ) AS raw_text,
    
    RAW_DATA:"Extraction_Time"::TIMESTAMP_NTZ AS extraction_timestamp,
    
    -- Keep the raw JSON just in case!
    RAW_DATA AS metadata_payload

FROM raw_source
