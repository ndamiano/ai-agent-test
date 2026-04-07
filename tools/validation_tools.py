"""
Validation tools: submit_verdict.

Allows the validator agent to return a structured evaluation result.
"""

import json
import logging

from tools.tool_manager import tool_manager

logger = logging.getLogger(__name__)


def _submit_verdict(verdict: str, justification: str) -> str:
    """
    Submit a verdict on whether the task goal has been fully achieved.
    """
    if verdict not in ("COMPLETE", "INCOMPLETE"):
        return json.dumps({"error": f"Invalid verdict '{verdict}'. Must be COMPLETE or INCOMPLETE."})

    return json.dumps({"verdict": verdict, "justification": justification})


def register_validation_tools() -> None:
    """Register validation tools with the global tool manager."""

    tool_manager.register_tool(
        name="submit_verdict",
        description=(
            "Submit your evaluation verdict after reviewing the completed work. "
            "You MUST call this tool to complete your evaluation. "
            "Do not respond with text — call this tool."
        ),
        parameters={
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "enum": ["COMPLETE", "INCOMPLETE"],
                    "description": "Whether the goal has been fully achieved.",
                },
                "justification": {
                    "type": "string",
                    "description": "Brief explanation of your verdict (2-4 sentences).",
                },
            },
            "required": ["verdict", "justification"],
        },
        fn=_submit_verdict,
        auto_inject_context=False,
    )

    logger.info("Validation tools registered: submit_verdict")