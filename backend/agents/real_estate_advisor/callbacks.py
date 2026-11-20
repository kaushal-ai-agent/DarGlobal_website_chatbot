"""Comprehensive callback pipeline and trace logger for Google ADK Real Estate Advisor.

File: backend/agents/real_estate_advisor/callbacks.py
Captures:
  - before_agent_callback, after_agent_callback
  - before_model_callback, after_model_callback
  - before_tool_callback, after_tool_callback
  - on_tool_error_callback, on_model_error_callback
"""

import datetime
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from google.adk.agents.callback_context import CallbackContext
from google.adk.models import LlmRequest, LlmResponse
from google.adk.tools.base_tool import BaseTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types

LOG_FILE = Path(__file__).resolve().parent / "agent_activity.log"


def log_step(step_name: str, payload: Dict[str, Any]) -> None:
    """Appends a structured trace log entry to agent_activity.log."""
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        safe_payload = payload if isinstance(payload, dict) else {"raw": str(payload)}
        log_line = f"[{timestamp}] [{step_name}] {json.dumps(safe_payload, default=str)}\n"
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(log_line)
    except Exception as e:
        logging.getLogger("adk_callbacks").error(f"Failed to write to agent_activity.log: {e}")


# ==============================================================================
# Agent Callbacks
# ==============================================================================

def advisor_before_agent(callback_context: CallbackContext) -> Optional[types.Content]:
    """before_agent_callback: Ensures session state variables are initialized only if not present."""
    agent_name = getattr(callback_context, "agent_name", "unknown")
    state = getattr(callback_context, "state", None)

    # State is an ADK State object (not a dict) that supports dict-like subscripting and .get()
    if state is not None:
        try:
            if "last_active_agent" not in state:
                state["last_active_agent"] = "None"
            if "selected_property_id" not in state:
                state["selected_property_id"] = "None"
            if "last_search_city" not in state:
                state["last_search_city"] = "None"
        except Exception as e:
            log_step("STATE_INIT_ERROR", {"error": str(e)})

    log_step("BEFORE_AGENT", {
        "agent": str(agent_name),
        "session_id": str(getattr(callback_context, "session_id", "unknown")),
        "last_active_agent": state.get("last_active_agent") if hasattr(state, "get") else None,
    })
    return None


def advisor_after_agent(callback_context: CallbackContext) -> Optional[types.Content]:
    """after_agent_callback: Records completion and saves state for cross-agent consumption."""
    agent_name = getattr(callback_context, "agent_name", "unknown")
    state = getattr(callback_context, "state", None)

    if state is not None:
        try:
            state["last_active_agent"] = str(agent_name)
        except Exception as e:
            log_step("STATE_UPDATE_ERROR", {"error": str(e)})

    log_step("AFTER_AGENT", {
        "agent": str(agent_name),
        "session_id": str(getattr(callback_context, "session_id", "unknown")),
        "last_active_agent": state.get("last_active_agent") if hasattr(state, "get") else None,
    })
    return None


# ==============================================================================
# Model Callbacks
# ==============================================================================

def advisor_before_model(
    callback_context: CallbackContext, llm_request: LlmRequest
) -> Optional[LlmResponse]:
    """before_model_callback: Inspects request contents, logs invocation, and guards input."""
    agent_name = getattr(callback_context, "agent_name", "unknown")
    
    contents_count = 0
    last_msg = ""
    if isinstance(llm_request, LlmRequest) and getattr(llm_request, "contents", None):
        contents_count = len(llm_request.contents)
        if llm_request.contents and getattr(llm_request.contents[-1], "role", "") == "user":
            parts = getattr(llm_request.contents[-1], "parts", None)
            if parts and len(parts) > 0:
                last_msg = str(getattr(parts[0], "text", "") or "")

    log_step("BEFORE_MODEL", {
        "agent": str(agent_name),
        "model": str(getattr(llm_request, "model", "unknown")),
        "contents_count": contents_count,
        "last_user_message_snippet": last_msg[:120] if last_msg else "",
    })
    return None


def advisor_after_model(
    callback_context: CallbackContext, llm_response: LlmResponse
) -> Optional[LlmResponse]:
    """after_model_callback: Strips $0.00 unpriced luxury anomalies and audits response."""
    agent_name = getattr(callback_context, "agent_name", "unknown")
    snippet = ""

    if isinstance(llm_response, LlmResponse):
        content = getattr(llm_response, "content", None)
        if content and getattr(content, "parts", None):
            for part in content.parts:
                text = getattr(part, "text", None)
                if isinstance(text, str) and text:
                    # Eliminate $0.00 or $0 price hallucinations for off-plan luxury projects
                    cleaned = re.sub(r"\$0(\.00)?\b", "Price upon inquiry", text)
                    if cleaned != text:
                        part.text = cleaned
            if content.parts and getattr(content.parts[0], "text", None):
                snippet = str(content.parts[0].text)

    log_step("AFTER_MODEL", {
        "agent": str(agent_name),
        "response_snippet": snippet[:150] if snippet else "",
    })
    return None


def advisor_on_model_error(
    callback_context: CallbackContext, llm_request: LlmRequest, exception: Exception
) -> Optional[LlmResponse]:
    """on_model_error_callback: Logs model failures for debugging."""
    agent_name = getattr(callback_context, "agent_name", "unknown")
    model_name = getattr(llm_request, "model", "unknown") if isinstance(llm_request, LlmRequest) else "unknown"

    log_step("ON_MODEL_ERROR", {
        "agent": str(agent_name),
        "model": str(model_name),
        "error_type": type(exception).__name__,
        "error_message": str(exception),
    })
    return None


# ==============================================================================
# Tool Callbacks & Error Interception
# ==============================================================================

def advisor_before_tool(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext
) -> Optional[Dict[str, Any]]:
    """before_tool_callback: Logs tool invocation and parameters."""
    tool_name = getattr(tool, "name", str(tool))
    safe_args = args if isinstance(args, dict) else {"raw": str(args)}

    log_step("BEFORE_TOOL", {
        "tool": str(tool_name),
        "arguments": safe_args,
    })
    return None


def advisor_after_tool(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext, output: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """after_tool_callback: Logs tool execution results."""
    tool_name = getattr(tool, "name", str(tool))
    count = output.get("count") if isinstance(output, dict) else None
    success = output.get("success") if isinstance(output, dict) else None
    found = output.get("found") if isinstance(output, dict) else None

    log_step("AFTER_TOOL", {
        "tool": str(tool_name),
        "count": count,
        "success": success,
        "found": found,
    })
    return None


def advisor_on_tool_error(
    tool: BaseTool, args: Dict[str, Any], tool_context: ToolContext, exception: Exception
) -> Optional[Dict[str, Any]]:
    """on_tool_error_callback: Intercepts tool execution errors and feeds error feedback back to LLM.

    Instead of crashing or leaving the LLM to apologize, this returns a structured
    dictionary so the LLM can review the database schema and retry with correct parameters.
    """
    tool_name = getattr(tool, "name", str(tool))
    safe_args = args if isinstance(args, dict) else {"raw": str(args)}
    error_msg = str(exception)
    
    log_step("ON_TOOL_ERROR", {
        "tool": str(tool_name),
        "arguments": safe_args,
        "error": error_msg,
    })

    return {
        "success": False,
        "error": f"Tool Execution Error in '{tool_name}': {error_msg}",
        "guidance": (
            "Please review the exact database column names from the Database Schema Reference "
            "in your instructions (e.g. use 'price_amount' or 'price_numerical', 'built_up_area_sqm', "
            "'bedrooms') and retry your query."
        ),
        "tool_attempted": str(tool_name),
    }
