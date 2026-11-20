"""Property Database Agent definition for Google ADK."""

import os
from google.adk.agents import Agent

from ...callbacks import (
    advisor_after_agent,
    advisor_after_model,
    advisor_after_tool,
    advisor_before_agent,
    advisor_before_model,
    advisor_before_tool,
    advisor_on_model_error,
    advisor_on_tool_error,
)
from .prompt import DYNAMIC_INSTRUCTION, STATIC_INSTRUCTION
from .tools import (
    execute_safe_sql,
    get_property_details,
    get_residency_rules,
    search_auctions,
    search_luxury_projects,
    search_properties,
)

agent = Agent(
    name="property_database_agent",
    model=os.getenv("ADK_MODEL_NAME", "gemini-2.5-flash"),
    description=(
        "Specialist in real estate listings, exact pricing, REGA/FAL licenses, "
        "developer masterplans, Infath auctions, and individual property card inspection. "
        "Connects directly to SQLite relational tables and views. Use get_property_details "
        "when the client asks deep follow-up questions about a specific property."
    ),
    static_instruction=STATIC_INSTRUCTION,
    instruction=DYNAMIC_INSTRUCTION,
    tools=[
        search_properties,
        get_property_details,
        search_luxury_projects,
        search_auctions,
        get_residency_rules,
        execute_safe_sql,
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

# Alias for explicit import
property_database_agent = agent
