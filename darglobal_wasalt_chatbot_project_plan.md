# Project Plan: DarGlobal & Wasalt AI Chatbot

## 1. Project Scope
The objective is to build and deploy an end-to-end AI chatbot that answers user queries based on real estate data scraped from DarGlobal and Wasalt. The solution will encompass data extraction, a Retrieval-Augmented Generation (RAG) pipeline, a modern web frontend, and a fully containerized deployment.

**Key Deliverables:**
*   Web scraping scripts for dynamic property data.
*   Vector database containing embedded property knowledge.
*   Backend API with an Agentic LLM pipeline.
*   Web-based Chat UI.
*   Dockerized setup and deployed live URL.

## 2. Technical Stack

### Web Scraping
*   **Target Websites:** DarGlobal (ultra-luxury real estate, high-end UI) & Wasalt (digital real estate portal, dynamic search/listings). Both sites heavily rely on JavaScript rendering.
*   **Libraries:** 
    *   `playwright`: Essential for rendering dynamic JavaScript content, handling pagination, and bypassing basic bot protections on modern real estate portals.
    *   `beautifulsoup4`: For parsing the rendered HTML DOM.
    *   `pydantic`: For structuring and validating the scraped data (e.g., Price, Location, Amenities).

### Database (Vector Store)
*   **Library/Engine:** `chromadb`
*   **Reasoning:** Open-source, easily embeddable within a Docker container without needing a separate cloud database instance, and highly optimized for RAG setups.

### Agent & LLM Orchestration
*   **Libraries:** 
    *   `litellm`: To interface uniformly with OpenRouter.
    *   `google-genai` / Agent Framework: To handle the agentic reasoning and tool-calling loop (routing queries, retrieving context).
    *   `sentence-transformers`: For generating local embeddings (e.g., `all-MiniLM-L6-v2`) before storing them in ChromaDB (free and efficient).
*   **LLM Provider:** OpenRouter (Targeting free-tier models like `meta-llama/llama-3-8b-instruct:free` or `google/gemma-2-9b-it:free`).

### Backend API
*   **Libraries:** `fastapi`, `uvicorn`, `python-dotenv`.
*   **Reasoning:** High performance, built-in async support (perfect for LLM calls and Playwright), and automatic Swagger UI documentation.

### Frontend UI
*   **Technologies:** React, Vite (for fast bundling), Tailwind CSS (styling), `lucide-react` (icons).
*   **Reasoning:** Lightweight, fast, and easily containerized alongside the backend.

### Deployment
*   **Technologies:** `Docker`, `Docker Compose`.
*   **Hosting Recommendation:** Render, Railway, or Google Cloud Run (to provide the required working URL).

---

## 3. System Architecture (ASCII)

```text
+-------------------+        +----------------------------------------------------+
|                   |        |                   DOCKER ENVIRONMENT               |
|                   |  HTTP  |  +----------------+      +----------------------+  |
|  User (Browser)   | <====> |  | Frontend (UI)  | ===> | Backend (FastAPI)    |  |
|                   |        |  | (React + Vite) | REST | - Query Processing   |  |
|                   |        |  +----------------+      | - Agent Orchestration|  |
+-------------------+        |                          +----------------------+  |
                                                                |   |
                                +-------------------------------+   |
                                |                                   |
                                V                                   V
                      +-------------------+              +----------------------+
                      | Vector Database   |              | LLM Provider         |
                      | (ChromaDB)        | <==========> | (OpenRouter/LiteLLM) |
                      | - Embeddings      |  Retrieval   | - Free Model         |
                      +-------------------+              +----------------------+
                                ^
                                |
                      +-------------------+
                      | Scraping Pipeline |
                      | (Playwright)      |
                      | - DarGlobal       |
                      | - Wasalt          |
                      +-------------------+
```

---

## 4. Implementation Phases

### Phase 1: Data Acquisition (Scraping)
1. Initialize a Python script using `playwright` (async mode).
2. Navigate to DarGlobal and Wasalt property listing pages.
3. Extract critical data: Property Name, Description, Location, Price, Status, Amenities.
4. Clean and output data to a structured JSON file (`scraped_data.json`).

### Phase 2: RAG Pipeline & Database
1. Load `scraped_data.json`.
2. Chunk the text logically (e.g., one document per property listing).
3. Generate embeddings using a free HuggingFace embedding model.
4. Ingest embeddings and metadata into a local `chromadb` persistent volume.

### Phase 3: Backend & Agent Development
1. Setup FastAPI with a `/chat` endpoint.
2. Integrate `litellm` with the OpenRouter API key.
3. Build the Retrieval-Augmented Agent:
    *   Receive user query.
    *   Convert query to embedding.
    *   Query ChromaDB for top-K relevant properties.
    *   Inject context into the system prompt.
    *   Call OpenRouter LLM via LiteLLM.
    *   Return formatted response.

### Phase 4: Frontend Development
1. Scaffold a React app using Vite (`npm create vite@latest`).
2. Build a chat interface with message history, loading states, and Markdown rendering for LLM responses.
3. Connect the frontend to the FastAPI `/chat` endpoint.

### Phase 5: Containerization & Deployment
1. Write a `Dockerfile` using a multi-stage build or `docker-compose.yml` to run both the React build (served via Nginx or FastAPI static files) and the Python backend.
2. Ensure Playwright browser binaries are correctly installed in the Docker image.
3. Expose the necessary ports (e.g., 80 or 8000).
4. Deploy the container to a cloud provider and verify the live URL.