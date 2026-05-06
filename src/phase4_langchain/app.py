import os
import json
import logging
from snowflake.snowpark import Session
from src.phase4_langchain.graph import build_agent_graph

# Configure professional logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def create_snowpark_session():
    """Initializes connection using environment variables."""
    connection_parameters = {
        "account": os.getenv("SNOWFLAKE_ACCOUNT"),
        "user": os.getenv("SNOWFLAKE_USER"),
        "password": os.getenv("SNOWFLAKE_PASSWORD"),
        "role": os.getenv("SNOWFLAKE_ROLE"),
        "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE"),
        "database": os.getenv("SNOWFLAKE_DB"),
        "schema": os.getenv("SNOWFLAKE_SCHEMA")
    }
    return Session.builder.configs(connection_parameters).create()

def main():
    logger.info("Starting AI Agent Pipeline...")
    
    try:
        session = create_snowpark_session()
        app_graph = build_agent_graph(session)
        
        # Test Query
        query = "What is the direct cost explanation for the Acme contract?"
        inputs = {"question": query}
        
        logger.info("Executing graph stream...")
        for output in app_graph.stream(inputs):
            for node_name, state_updates in output.items():
                logger.info(f"--- Node Executed: {node_name} ---")
                
                # If we hit the final generation node, print the JSON payload nicely
                if node_name == "generate":
                    print("\n=== FINAL AI RESPONSE ===")
                    try:
                        parsed_json = json.loads(state_updates['answer'])
                        print(json.dumps(parsed_json, indent=2))
                    except json.JSONDecodeError:
                        print(state_updates['answer'])
                        
    except Exception as e:
        logger.critical(f"Application crashed: {str(e)}")
    finally:
        if 'session' in locals():
            session.close()
            logger.info("Snowflake session closed.")

if __name__ == "__main__":
    main()
