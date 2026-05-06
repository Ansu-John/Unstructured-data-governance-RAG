import pytest
import os
from unittest.mock import patch, MagicMock

from src.phase2_snowflake.snowflake_load import load_json_directly_to_chunk_table

# ==========================================
# TEST CONSTANTS (DRY Principle)
# ==========================================
MOCK_AWS_KEY = os.getenv("AWS_KEY")
MOCK_AWS_SECRET = os.getenv("AWS_SECRET")
MOCK_SNOW_USER = os.getenv("SNOWFLAKE_USER")
MOCK_SNOW_PASS = os.getenv("SNOWFLAKE_PASSWORD")
MOCK_SNOW_ACCT = os.getenv("SNOWFLAKE_ACCOUNT")


# Now we just pass the variables into the decorator!
@patch('src.phase2_snowflake.snowflake_load.snowflake.connector.connect')
@patch.dict(os.environ, {
    'SNOWFLAKE_USER': MOCK_SNOW_USER,
    'SNOWFLAKE_PASSWORD': MOCK_SNOW_PASS,
    'SNOWFLAKE_ACCOUNT': MOCK_SNOW_ACCT,
    'AWS_ACCESS_KEY_ID': MOCK_AWS_KEY,
    'AWS_SECRET_ACCESS_KEY': MOCK_AWS_SECRET
})
def test_direct_json_to_vector_success(mock_connect):
    
    # --- 1. SETUP ---
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur

    # --- 2. EXECUTE ---
    load_json_directly_to_chunk_table()

    # --- 3. ASSERTIONS ---
    assert mock_cur.execute.call_count == 6
    sql_calls = mock_cur.execute.call_args_list
        
    # Temporarily add this to see exactly what 6 queries are running!
    for i, call in enumerate(sql_calls):
        print(f"Query {i}: {call[0][0]}")
    # Query 0: Table Creation
        assert "CREATE TABLE IF NOT EXISTS CHUNK_TABLE" in sql_calls[0][0][0]
        assert "VECTOR(FLOAT, 768)" in sql_calls[0][0][0]

        # Query 1: File Format
        assert "CREATE OR REPLACE FILE FORMAT BRONZE_JSON_FORMAT" in sql_calls[1][0][0]

        # Query 2: Stage Creation with injected credentials
        stage_sql = sql_calls[2][0][0]
        assert "CREATE OR REPLACE STAGE BRONZE_S3_STAGE" in stage_sql
        assert MOCK_AWS_KEY in stage_sql 
        assert MOCK_AWS_SECRET in stage_sql

        # Query 3: First COPY INTO (Generic Mapping)
        copy_sql_1 = sql_calls[3][0][0]
        assert "COPY INTO CHUNK_TABLE" in copy_sql_1
        assert "$1:chunk_id::STRING" in copy_sql_1

        # Query 4: Second COPY INTO (Custom Financial Mapping)
        copy_sql_2 = sql_calls[4][0][0]
        assert "COPY INTO CHUNK_TABLE" in copy_sql_2
        assert "CONCAT(" in copy_sql_2
        assert "Direct Cost Note" in copy_sql_2

        # Query 5: Cortex Embeddings
        cortex_sql = sql_calls[5][0][0]
        assert "UPDATE CHUNK_TABLE" in cortex_sql
        assert "SNOWFLAKE.CORTEX.EMBED_TEXT_768" in cortex_sql

        mock_cur.close.assert_called_once()
        mock_conn.close.assert_called_once()

# We can reuse the same constants for the crash test
@patch('src.phase2_snowflake.snowflake_load.snowflake.connector.connect')
@patch.dict(os.environ, {
    'AWS_ACCESS_KEY_ID': MOCK_AWS_KEY, 
    'AWS_SECRET_ACCESS_KEY': MOCK_AWS_SECRET
})
def test_database_crash_handling(mock_connect):
    
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_connect.return_value = mock_conn
    mock_conn.cursor.return_value = mock_cur
    
    mock_cur.execute.side_effect = Exception("Simulated Warehouse Suspend")
    
    with pytest.raises(Exception, match="Simulated Warehouse Suspend"):
        load_json_directly_to_chunk_table()
        
    mock_cur.close.assert_called_once()
    mock_conn.close.assert_called_once()
