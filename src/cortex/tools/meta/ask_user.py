"""ask_user tool — pause the loop to ask the user a clarifying question.

Unlike ordinary tools, ``ask_user`` produces no data to feed back into the
loop: it returns a ``ToolResult`` carrying the ``control="ask_user"`` signal so
the :class:`~cortex.agentic.loop.AgentLoop` halts and surfaces the question to
the user instead of iterating. Replaces the older ``[QUESTION]...[/QUESTION]``
text-marker convention with a structured tool call.
"""

from typing import Any

from cortex.tools.interfaces import ToolErrorSeverity, ToolResult
from cortex.tools.meta.base import BaseMetaTool

# Tool name and the control signal it emits, kept in one place so the tool and
# the loop agree on the exact strings (they share the literal, but are distinct
# concepts: one identifies the tool, the other flags the loop-halt behavior).
ASK_USER_TOOL_NAME = "ask_user"
ASK_USER_CONTROL = "ask_user"


class AskUserTool(BaseMetaTool):
    """Ask the user for clarification when information is missing.

    The model calls this instead of guessing when it cannot proceed. Optional
    ``options`` are model-suggested answers the UI can render as choices.
    """

    @property
    def name(self) -> str:
        return ASK_USER_TOOL_NAME

    @property
    def description(self) -> str:
        return (
            "Ask the user a clarifying question when you lack the information "
            "to proceed. Prefer this over guessing. Optionally provide a short "
            "list of suggested answers as `options`. Calling this ends your turn "
            "and waits for the user's reply."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The clarifying question to ask the user.",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional suggested answers to present to the user as "
                        "selectable choices."
                    ),
                },
            },
            "required": ["question"],
        }

    @property
    def tags(self) -> list[str]:
        return ["clarification", "interaction", "control"]

    async def execute(self, arguments: dict[str, Any]) -> ToolResult:
        question = arguments.get("question", "")
        if not isinstance(question, str) or not question.strip():
            return ToolResult(
                tool_call_id="",
                tool_name=self.name,
                success=False,
                error="`question` is required and must be a non-empty string.",
                error_severity=ToolErrorSeverity.ERROR,
            )

        # Normalize options: keep only non-empty strings; drop anything else.
        raw_options = arguments.get("options") or []
        options = [o for o in raw_options if isinstance(o, str) and o.strip()]

        return ToolResult(
            tool_call_id="",
            tool_name=self.name,
            success=True,
            output=question,
            control=ASK_USER_CONTROL,
            metadata={"options": options},
        )
