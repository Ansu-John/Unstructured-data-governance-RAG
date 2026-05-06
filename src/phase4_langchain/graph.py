import logging
from typing import TypedDict
from langgraph.graph import StateGraph, END
from snowflake.snowpark import Session
from src.phase4_langchain.retriever import get_snowflake_retriever

logger = logging.getLogger(__name__)

# 1. Define the State Schema
class AgentState(TypedDict):
    question: str
    context: str
    answer: str

# 2. Graph Factory Function
def build_agent_graph(session: Session):
    """
    Builds and compiles the LangGraph state machine, injecting the DB session.
    """
    
    def retrieve_node(state: AgentState):
        logger.info(f"Executing similarity search for: '{state['question']}'")
        retriever = get_snowflake_retriever(session)
        docs = retriever.invoke(state["question"])
        
        context_text = "\n---\n".join([d.page_content for d in docs])
        if not context_text:
            logger.warning("No context retrieved. Database may be empty or query is highly out of domain.")
            context_text = "No relevant documents found."
            
        return {"context": context_text}

    def generate_node(state: AgentState):
        logger.info("Sending prompt to Snowflake Cortex (llama3-70b)...")
        
        prompt = f"""
        You are the core intelligence of an AI-Driven Data Quality & Cataloging Agent.
        Answer the user's question using ONLY the provided context. 
        If the answer is not in the context, output exactly: {{"answer": "Data not found in catalog.", "sources": []}}
        
        Return the output in strictly valid JSON format matching this schema:
        {{"answer": "...", "sources": ["..."]}}
        
        Context: 
        {state['context']}
        
        Question: {state['question']}
        """
        
        try:
            # Execute Cortex LLM via Snowpark SQL
            sql_query = "SELECT SNOWFLAKE.CORTEX.COMPLETE('llama3-70b', ?) AS LLM_RESPONSE"
            df = session.sql(sql_query, params=[prompt]).collect()
            response = df[0]['LLM_RESPONSE']
            
            return {"answer": response}
        except Exception as e:
            logger.error(f"LLM Generation failed: {str(e)}")
            return {"answer": '{"error": "Generation failed", "sources": []}'}

    # 3. Construct the workflow
    workflow = StateGraph(AgentState)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("generate", generate_node)
    
    workflow.set_entry_point("retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", END)
    
    return workflow.compile()
