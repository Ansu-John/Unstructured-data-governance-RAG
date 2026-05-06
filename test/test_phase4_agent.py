import pytest
from unittest.mock import MagicMock, patch
from src.phase4_langchain.graph import build_agent_graph

@pytest.fixture
def mock_session():
    """Creates a fake Snowflake session so tests run locally and instantly."""
    return MagicMock()

@patch('src.phase4_langchain.graph.get_snowflake_retriever')
def test_retrieve_node(mock_get_retriever, mock_session):
    """Tests if the graph correctly formats context from retrieved documents."""
    
    # 1. Setup the Mock Retriever
    mock_retriever_instance = MagicMock()
    fake_doc = MagicMock()
    fake_doc.page_content = "Mock direct cost: $500 for server hosting."
    mock_retriever_instance.invoke.return_value = [fake_doc]
    mock_get_retriever.return_value = mock_retriever_instance

    # We also mock the SQL return so the subsequent generate node doesn't crash
    mock_session.sql.return_value.collect.return_value = [{"LLM_RESPONSE": '{"answer": "dummy"}'}]

    # 2. Compile and run the FULL graph using standard invoke()
    app_graph = build_agent_graph(mock_session)
    initial_state = {"question": "What is the cost?"}
    
    # This runs both retrieve and generate seamlessly
    result = app_graph.invoke(initial_state)

    # 3. Assertions
    assert "Mock direct cost: $500" in result["context"]
    mock_retriever_instance.invoke.assert_called_once_with("What is the cost?")


@patch('src.phase4_langchain.graph.get_snowflake_retriever')
def test_generate_node(mock_get_retriever, mock_session):
    """Tests if the graph successfully parses LLM SQL results."""
    
    # 1. Setup the Mock Retriever to feed context to the LLM
    mock_retriever_instance = MagicMock()
    fake_doc = MagicMock()
    fake_doc.page_content = "Mock direct cost: $500 for server hosting."
    mock_retriever_instance.invoke.return_value = [fake_doc]
    mock_get_retriever.return_value = mock_retriever_instance
    
    # 2. Setup the Mock Snowflake SQL response
    mock_df = [{"LLM_RESPONSE": '{"answer": "It is 500", "sources": ["doc_1"]}'}]
    mock_session.sql.return_value.collect.return_value = mock_df
    
    # 3. Run the FULL graph using standard invoke()
    app_graph = build_agent_graph(mock_session)
    initial_state = {"question": "What is the cost?"}
    result = app_graph.invoke(initial_state)
    
    # 4. Assertions
    assert "It is 500" in result["answer"]
    # Ensure Cortex Complete was called during the generate step
    mock_session.sql.assert_called()
