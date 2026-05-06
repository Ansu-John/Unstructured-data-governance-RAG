import logging
from langchain_core.documents import Document
from snowflake.snowpark import Session

logger = logging.getLogger(__name__)

class SnowflakeNativeRetriever:
    def __init__(self, session: Session, k: int = 3):
        self.session = session
        self.k = k

    def invoke(self, query: str):
        logger.info(f"Executing Cortex Vector Search for: '{query}'")
        
        # We use Snowpark to run the exact Cortex similarity function against our dbt table
        sql = f"""
            SELECT 
                METADATA_PAYLOAD, 
                VECTOR_COSINE_SIMILARITY(
                    VECTOR_EMBEDDING, 
                    SNOWFLAKE.CORTEX.EMBED_TEXT_768('e5-base-v2', ?)
                ) as SIMILARITY_SCORE
            FROM FCT_KNOWLEDGE_BASE
            WHERE METADATA_PAYLOAD IS NOT NULL
            ORDER BY SIMILARITY_SCORE DESC
            LIMIT {self.k}
        """
        
        try:
            # Execute the query, safely passing the user's question as a bound parameter (?)
            df = self.session.sql(sql, params=[query]).collect()
            
            # Convert the raw Snowflake rows into standard LangChain Document objects
            docs = [Document(page_content=row['METADATA_PAYLOAD']) for row in df]
            
            logger.info(f"Retrieved {len(docs)} documents from Snowflake.")
            return docs
            
        except Exception as e:
            logger.error(f"Vector search failed: {str(e)}")
            raise

def get_snowflake_retriever(session: Session, k: int = 3):
    """Returns our custom native Snowpark retriever."""
    return SnowflakeNativeRetriever(session=session, k=k)
