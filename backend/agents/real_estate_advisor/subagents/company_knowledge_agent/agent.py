"""Company Knowledge Agent definition for Google ADK."""

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
from .tools import read_document, search_knowledge_base

agent = Agent(
    name="company_knowledge_agent",
    model=os.getenv("ADK_MODEL_NAME", "gemini-3.5-flash-lite"),
    description=(
        "Specialist in DarGlobal and Wasalt corporate background, brand partnerships "
        "(Trump, Pagani, Aston Martin, Elie Saab, Missoni), real estate tokenization, "
        "residency & Golden Visa investment regulations, and platform manuals. "
        "Executes hybrid vector and lexical search across 88 Markdown knowledge documents."
    ),
    static_instruction=STATIC_INSTRUCTION,
    instruction=DYNAMIC_INSTRUCTION,
    tools=[
        search_knowledge_base,
        read_document,
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
company_knowledge_agent = agent
