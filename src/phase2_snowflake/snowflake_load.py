import snowflake.connector
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def load_json_directly_to_chunk_table():
    conn = snowflake.connector.connect(
		user=os.getenv("SNOWFLAKE_USER"),
		password=os.getenv("SNOWFLAKE_PASSWORD"),
		account=os.getenv("SNOWFLAKE_ACCOUNT"),
		warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
		database=os.getenv("SNOWFLAKE_DB"),
		schema=os.getenv("SNOWFLAKE_SCHEMA")
    )
    cur = conn.cursor()

    aws_key = os.getenv("AWS_KEY")
    aws_secret = os.getenv("AWS_SECRET")
    s3_path = os.getenv("SNOWFLAKE_INPUT_PATH")

    try:
        logger.info("--- PHASE 2: DIRECT JSON TO VECTOR INGESTION ---")
        
        # 1. Re-verify the CHUNK_TABLE exists with the VECTOR column
        logger.info("Ensuring CHUNK_TABLE schema exists...")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS CHUNK_TABLE (
                CHUNK_ID STRING,
                FILE_NAME STRING,
                RAW_TEXT STRING,
                EXTRACTION_TIMESTAMP TIMESTAMP_NTZ,
                EMBEDDING VECTOR(FLOAT, 768)
            );
        """)

        # 2. Set up the Stage and JSON Format
        cur.execute("""
            CREATE OR REPLACE FILE FORMAT BRONZE_JSON_FORMAT
            TYPE = 'JSON'
            COMPRESSION = 'AUTO'
            STRIP_OUTER_ARRAY = TRUE;
        """)

        cur.execute(f"""
            CREATE OR REPLACE STAGE BRONZE_S3_STAGE
            URL = '{s3_path}'
            CREDENTIALS = (AWS_KEY_ID = '{aws_key}' AWS_SECRET_KEY = '{aws_secret}')
            FILE_FORMAT = BRONZE_JSON_FORMAT;
        """)

        # 3. The Magic Step: Parse JSON into Relational Columns during COPY INTO
        # Notice how we use $1:keyname::type to cast the JSON to the correct column type
        logger.info("Extracting JSON keys directly into CHUNK_TABLE...")
        cur.execute("""
            COPY INTO CHUNK_TABLE (CHUNK_ID, FILE_NAME, RAW_TEXT, EXTRACTION_TIMESTAMP)
            FROM (
                SELECT 
                    $1:chunk_id::STRING, 
                    $1:file_name::STRING, 
                    $1:page_content::STRING, 
                    $1:extraction_timestamp::TIMESTAMP_NTZ
                FROM @BRONZE_S3_STAGE
            )
            ON_ERROR = 'CONTINUE'; 
        """)
        logger.info("✅ JSON successfully parsed and loaded into relational columns.")
        
        # 3. The Magic Step: Parse Case-Sensitive JSON keys
        logger.info("Extracting JSON keys directly into CHUNK_TABLE...")
        cur.execute("""
            COPY INTO CHUNK_TABLE (CHUNK_ID, FILE_NAME, RAW_TEXT, EXTRACTION_TIMESTAMP)
            FROM (
                SELECT 
                    $1:"Contract_Number"::STRING, 
                    $1:"File_Name"::STRING, 
                    
                    -- We concatenate the direct and indirect explanations so Cortex vectors the whole story
                    CONCAT(
                        'Direct Cost Note: ', COALESCE($1:"Direct_Cost_Explanation"::STRING, ''), 
                        ' | Indirect Cost Note: ', COALESCE($1:"Indirect_Cost_Explanation"::STRING, '')
                    ) AS RAW_TEXT,
                    
                    $1:"Extraction_Time"::TIMESTAMP_NTZ
                FROM @BRONZE_S3_STAGE
            )
            ON_ERROR = 'CONTINUE'; 
        """)
        

        # 4. Generate the Vector Embeddings using the parsed RAW_TEXT
        logger.info("Activating Snowflake Cortex to generate Vector Embeddings...")
        cur.execute("""
            UPDATE CHUNK_TABLE 
            SET EMBEDDING = SNOWFLAKE.CORTEX.EMBED_TEXT_768('e5-base-v2', RAW_TEXT)
            WHERE EMBEDDING IS NULL;
        """)
        logger.info("✅ VECTORIZATION COMPLETE: CHUNK_TABLE is fully populated.")

    except Exception as e:
        logger.error(f"❌ Pipeline Error: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    load_json_directly_to_chunk_table()
