SELECT 
    chunk_id, 
    LENGTH(raw_text) AS text_length
FROM {{ ref('fct_knowledge_base') }}
WHERE LENGTH(raw_text) > 4000
