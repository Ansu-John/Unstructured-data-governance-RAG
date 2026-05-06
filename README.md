# 🤖 AI-Driven Data Quality & Cataloging Agent

![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python&logoColor=white)
![PySpark](https://img.shields.io/badge/PySpark-3.5-E25A1C?logo=apachespark&logoColor=white)
![Snowflake](https://img.shields.io/badge/Snowflake-Cortex-29B5E8?logo=snowflake&logoColor=white)
![LangChain](https://img.shields.io/badge/LangChain-LangGraph-green)
![AWS S3](https://img.shields.io/badge/AWS-S3-569A31?logo=amazons3&logoColor=white)
![Pytest](https://img.shields.io/badge/Testing-Pytest-0A9EDC?logo=pytest&logoColor=white)

An enterprise-grade Retrieval-Augmented Generation (RAG) pipeline and AI Agent designed to extract complex financial and contractual data from unstructured PDFs, catalog it securely in Snowflake, and provide intelligent, context-aware answers using **Snowflake Cortex** and **LangGraph**.

---

## 🚀 Overview

This project bridges the gap between unstructured document storage and intelligent data querying. It processes financial reports and contracts—specifically handling complex nested tables and checkboxes—and loads them into a vector database for semantic search. A LangGraph-orchestrated state machine then routes user queries to a Snowflake Cortex LLM (`llama3-70b`) for highly accurate, grounded responses.

### ✨ Key Features

* **Distributed Data Extraction:** Utilizes PySpark and `pdfplumber` to accurately parse structurally complex PDFs (including tabular financial data and checkbox booleans) directly from AWS S3.
* **Native In-Database AI:** Leverages Snowflake Cortex for both generating vector embeddings (`e5-base-v2`) and executing LLM completions (`llama3-70b`), ensuring data never leaves the secure database boundary.
* **Agentic Orchestration:** Built with LangGraph to manage the retrieval and generation state, allowing for scalable, multi-step reasoning capabilities.
* **Enterprise Testing:** Comprehensive CI/CD-ready test suite using `pytest` and `unittest.mock` to validate pipeline logic and SQL executions without consuming database compute credits.

## 🛠️ Technology Stack

* **Data Engineering:** PySpark, Python, `pdfplumber`, Regular Expressions
* **Cloud Storage:** AWS S3 (`hadoop-aws` integration)
* **Data Warehouse & AI:** Snowflake, Snowflake Cortex, Snowpark
* **LLM Orchestration:** LangChain, LangGraph
* **Testing:** Pytest, `unittest.mock`

## 🏗️ System Architecture

The pipeline is broken down into three distinct phases: Data Extraction (AWS), Database Cataloging (Snowflake), and the AI Agent (LangGraph).

```mermaid

flowchart TB
    subgraph AWS[Phase 1: AWS Cloud Extraction]
        direction TB
        S3_Raw[("Raw Financial PDFs\n(S3 Bucket)")]
        PySpark["PySpark Extractor\n(pdfplumber)"]
        S3_Json[("Structured JSON\n(S3 Staging)")]
        
        S3_Raw --> PySpark --> S3_Json
    end

    subgraph Snowflake[Phase 2: Snowflake Data Cloud]
        direction TB
        Stage["External S3 Stage"]
        Table[("CHUNK_TABLE\n(Relational Data)")]
        Cortex_Embed["Snowflake Cortex\n(e5-base-v2 Embeddings)"]
        
        S3_Json --> Stage --> Table
        Table --> Cortex_Embed --> Table
    end

    subgraph App[Phase 3: LangGraph AI Agent]
        direction TB
        User(("User"))
        State["Agent State Machine\n(graph.py)"]
        Retriever["Native Vector Search\n(retriever.py)"]
        Cortex_LLM["Snowflake Cortex\n(llama3-70b)"]

        User -->|1. Question| State
        State -->|2. Search Query| Retriever
        Retriever -->|3. Cosine Similarity| Table
        Table -.->|4. Relevant Context| Retriever
        Retriever -.-> State
        State -->|5. Prompt + Context| Cortex_LLM
        Cortex_LLM -.->|6. JSON Response| State
        State -->|7. Final Answer| User
    end
    
    %% Styling for enterprise look
    classDef aws fill:#FF9900,stroke:#232F3E,stroke-width:2px,color:black;
    classDef snow fill:#29B5E8,stroke:#1A365D,stroke-width:2px,color:black;
    classDef ai fill:#00A67E,stroke:#005A40,stroke-width:2px,color:white;
    
    class S3_Raw,S3_Json,PySpark aws;
    class Stage,Table,Cortex_Embed snow;
    class State,Retriever,Cortex_LLM ai;

```
    
    class S3_Raw,S3_Json,PySpark aws;
    class Stage,Table,Cortex_Embed snow;
    class State,Retriever,Cortex_LLM ai;
