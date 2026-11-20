# DarGlobal & Wasalt Real Estate Advisor (Google ADK V2)

A modular, multi-agent AI advisory system built with **Google Agent Development Kit (ADK)** for DarGlobal luxury developments and the Wasalt Saudi property ecosystem.

---

## Modular Directory Architecture

```text
backend/agents/real_estate_advisor/
├── .env                              # API keys (GEMINI_API_KEY, OPENROUTER_API_KEY, OPENROUTER_BACKUP_API_KEY)
├── __init__.py                       # from . import agent (ADK package entrypoint)
├── agent.py                          # Root Orchestrator: wires sub-agents, callbacks, resilient failover
├── prompt.py                         # Global, static & dynamic instructions
├── callbacks.py                      # Full lifecycle callbacks + tool error interceptor
├── model_resilience.py               # 2-strike Gemini failover + OpenRouter dual-key rotation
├── agent_activity.log                # Live step-by-step trace log for easy inspection and debugging
├── subagents/
│   ├── __init__.py
│   ├── company_knowledge_agent/      # Corporate & Brand Partnership Specialist
│   │   ├── __init__.py               # from . import agent
│   │   ├── agent.py                  # LlmAgent definition
│   │   ├── prompt.py                 # Static instructions & RAG guidelines
│   │   ├── tools.py                  # search_knowledge_base, read_document
│   │   └── tests/
│   │       ├── company_eval.test.json# Evaluation test suite
│   │       └── test_config.json      # Evaluation criteria
│   └── property_database_agent/      # Structured Database & Property Inspection Specialist
│       ├── __init__.py               # from . import agent
│       ├── agent.py                  # LlmAgent definition
│       ├── prompt.py                 # Static instructions with Markdown Database Schema Table
│       ├── tools.py                  # search_properties, get_property_details, safe_sql, etc.
│       └── tests/
│           ├── database_eval.test.json# Evaluation test suite
│           └── test_config.json      # Evaluation criteria
├── tests/
│   ├── advisor_eval.test.json        # Root Orchestrator evaluation test suite
│   └── test_config.json              # Evaluation criteria
└── README.md                         # Quickstart & evaluation guide
```

---

## Key Capabilities & Problem Solvers

1. **Modular Sub-Agents:**
   - Every sub-agent has its own folder, prompt, tools, and evaluation dataset.
   - The root orchestrator dynamically transfers control via ADK's native `transfer_to_agent`.
2. **True Conversational Memory & Single-Card Deep Dives:**
   - When a user asks follow-up questions about a previously shown property card (e.g. *"tell me more about this Apartment 124.69 SQM with 3 Bedrooms"*), the system calls **`get_property_details`** and returns the specific attributes (area, price, district, broker agency, and REGA permit #7201224771) instead of repeating the broad search list.
3. **Database Schema Injection & Safe SQL:**
   - The database agent prompt includes an exact Markdown table of all views (`v_wasalt_listings`, `v_darglobal_projects`, `v_wasalt_auctions`, `v_wasalt_auction_assets`, `v_residency_rules`).
   - `price_amount` and `price_numerical` are both supported for numerical sortable prices.
4. **Tool Error Interception & Self-Correction:**
   - If an SQL query fails, `on_tool_error_callback` catches the exception and returns the error message and schema guidance directly to the LLM, prompting it to correct its parameters rather than giving up or apologizing.
5. **Brand Partner Integrity:**
   - `v_darglobal_projects` accurately maps all Trump projects to *The Trump Organization*.
   - Prompts instruct agents to report tool data factually without questioning brand partner names.
6. **Resilient Model Failover & Key Rotation:**
   - **Google Gemini** is tried first with automatic retry.
   - If Gemini fails twice, it automatically switches to **OpenRouter**.
   - If `OPENROUTER_API_KEY` encounters quota exhaustion or 429, it automatically rotates to `OPENROUTER_BACKUP_API_KEY`.
7. **Trace File Logging:**
   - All callback events (`before_agent`, `after_agent`, `before_model`, `after_model`, `before_tool`, `after_tool`, `on_tool_error`) are continuously appended to `agent_activity.log` for easy monitoring.

---

## How to Test Visually with `adk web`

### Step 1: Configure Your API Key
Open `.env` in this folder (`backend/agents/real_estate_advisor/.env`) and add your key(s):
```ini
# Google Gemini (Primary):
GEMINI_API_KEY=your_gemini_api_key_here

# OpenRouter (Primary Failover):
OPENROUTER_API_KEY=your_openrouter_api_key_here

# OpenRouter Backup (Used if primary exhausts):
OPENROUTER_BACKUP_API_KEY=your_backup_openrouter_api_key_here
```

### Step 2: Run `adk web`
From your terminal in the repository root:
```powershell
adk web backend/agents
```
*(Or specify port 8080 if 8000 is occupied: `adk web --port 8080 backend/agents`)*

### Step 3: Test Live Conversations
Navigate to `http://localhost:8000` (or `8080`) in your browser and try:
1. *"Tell me little bit about darglobal company"*
2. *"What is Trump Tower Jeddah?"* (Observing brand partner shows *The Trump Organization* without model doubt)
3. *"Give me properties in Jeddah"* (Observing clear explanation of Wasalt Riyadh scope + Trump Tower Jeddah highlight)
4. *"What is the absolute minimum priced property in Riyadh?"* (Observing SQL runs cleanly without column errors)
5. *"Show 3-bedroom apartments in Riyadh under 600,000 SAR"* followed by *"Tell me more about this Apartment 124.69 SQM with 3 Bedrooms"* (Observing deep card inspection instead of repeated search)

---

## How to Run Evaluations

### Option 1: Via ADK Web UI
1. In `adk web`, click the **Eval** tab on the top-left navigation panel.
2. Select any evaluation case from the loaded dataset and click **Run Evaluation** to view turn-by-turn trajectory scores and word-match scores.

### Option 2: Via CLI (`adk eval`)
Run the evaluation test suite from your terminal:
```powershell
# Evaluate Root Orchestrator:
adk eval --agent_module_path backend/agents/real_estate_advisor --eval_set_file_path backend/agents/real_estate_advisor/tests/advisor_eval.test.json --eval_config_file_path backend/agents/real_estate_advisor/tests/test_config.json

# Evaluate Company Knowledge Agent:
adk eval --agent_module_path backend/agents/real_estate_advisor/subagents/company_knowledge_agent --eval_set_file_path backend/agents/real_estate_advisor/subagents/company_knowledge_agent/tests/company_eval.test.json --eval_config_file_path backend/agents/real_estate_advisor/subagents/company_knowledge_agent/tests/test_config.json

# Evaluate Property Database Agent:
adk eval --agent_module_path backend/agents/real_estate_advisor/subagents/property_database_agent --eval_set_file_path backend/agents/real_estate_advisor/subagents/property_database_agent/tests/database_eval.test.json --eval_config_file_path backend/agents/real_estate_advisor/subagents/property_database_agent/tests/test_config.json
```
