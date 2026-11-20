"""Google ADK Multi-Agent Orchestrator for DarGlobal & Wasalt Real Estate Advisory.

File: backend/agents/real_estate_advisor/agent.py
Exposes: 
  - root_agent: required top-level variable for Google ADK
  - app: ADK App instance configured with ContextCacheConfig for multi-agent transfer caching

Hierarchy:
  - root_agent (real_estate_orchestrator):
      ├── subagents/company_knowledge_agent (hybrid RAG over 88 markdown docs)
      └── subagents/property_database_agent (SQL tables, views & single-card inspection)
"""

import os
import sys
from pathlib import Path

# Ensure repository root is on sys.path for backend imports
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from google.adk.agents import Agent
from google.adk.apps.app import App, ContextCacheConfig

from .callbacks import (
    advisor_after_agent,
    advisor_after_model,
    advisor_after_tool,
    advisor_before_agent,
    advisor_before_model,
    advisor_before_tool,
    advisor_on_model_error,
    advisor_on_tool_error,
)
from .prompt import DYNAMIC_INSTRUCTION, GLOBAL_INSTRUCTION, STATIC_INSTRUCTION
from .subagents.company_knowledge_agent.agent import company_knowledge_agent
from .subagents.property_database_agent.agent import property_database_agent
from .subagents.property_database_agent.tools import get_property_details

# Root Conversational Orchestrator Agent
root_agent = Agent(
    name="real_estate_orchestrator",
    model=os.getenv("ADK_MODEL_NAME", "gemini-2.5-flash"),
    description=(
        "Main consultative conversational orchestrator for DarGlobal luxury developments "
        "and Wasalt Saudi real estate listings. Coordinates specialized sub-agents, tracks "
        "multi-turn context, inspects specific properties, and produces natural, non-repetitive responses."
    ),
    global_instruction=GLOBAL_INSTRUCTION,
    static_instruction=STATIC_INSTRUCTION,
    instruction=DYNAMIC_INSTRUCTION,
    sub_agents=[
        company_knowledge_agent,
        property_database_agent,
    ],
    tools=[
        get_property_details,
    ],
    before_agent_callback=advisor_before_agent,
    after_agent_callback=advisor_after_agent,
    before_model_callback=advisor_before_model,
    after_model_callback=advisor_after_model,
    before_tool_callback=advisor_before_tool,
    after_tool_callback=advisor_after_tool,
    on_tool_error_callback=advisor_on_tool_error,
    on_model_error_callback=advisor_on_model_error,
)

# App configured with ContextCacheConfig to give each agent its own cache
# and prevent uncached prompt re-transfers
app = App(
    name="real_estate_advisor",
    root_agent=root_agent,
    context_cache_config=ContextCacheConfig(),
)

# Standard alias for ADK agent loading conventions
agent = root_agent
