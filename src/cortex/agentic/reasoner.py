"""Reasoner - LLM-powered decision making."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from cortex.agentic.models import (
    Context,
    Decision,
    DecisionType,
)
from cortex.llm.models import ChatMessage

if TYPE_CHECKING:
    from cortex.llm.base import LLMClient
    from cortex.tools.interfaces import ToolRegistry

logger = logging.getLogger(__name__)


class Reasoner:
    """
    LLM-powered decision making.

    Given context, decides what to do next:
    - RESPOND: Done, return text to user
    - EXECUTE_TOOLS: Execute tools, continue loop
    - ASK_QUESTION: Need clarification
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        system_prompt: str | None = None,
    ):
        self._llm = llm_client
        self._tool_registry = tool_registry
        self._system_prompt = system_prompt or self._default_system_prompt()

    def _default_system_prompt(self) -> str:
        """Get the default system prompt."""
        return """You are Cortex, an intelligent AI assistant with access to tools.

You can use tools to:
- file_read: Read files from the filesystem
- file_write: Write content to files
- shell: Execute shell commands
- grep: Search for patterns in files

When a user asks something:
1. If you can answer directly, respond with a RESPOND decision
2. If you need to use tools, respond with an EXECUTE_TOOLS decision
3. If you need more information, ask a question

Always be helpful, concise, and precise."""

    async def reason(self, context: Context) -> Decision:
        """
        Make a decision based on context.

        Args:
            context: All context needed for reasoning

        Returns:
            Decision on what to do next
        """
        # Build the prompt
        messages = self._build_prompt(context)

        # Call LLM
        try:
            result = await self._llm.chat(messages)

            # Parse the response
            return self._parse_response(result, context)
        except Exception as e:
            logger.error(f"Reasoner error: {e}")
            return Decision.respond(
                text="I encountered an error. Please try again.",
                reasoning=f"LLM error: {str(e)}"
            )

    def _build_prompt(self, context: Context) -> list[ChatMessage]:
        """
        Build the prompt from context.

        Args:
            context: Reasoning context

        Returns:
            List of messages for the LLM
        """
        messages = []

        # System prompt
        system = self._system_prompt

        # Add tool definitions
        if context.tools:
            tool_section = "\n\nAvailable tools:\n"
            for tool in context.tools:
                if hasattr(tool, 'name'):
                    tool_section += f"- {tool.name}: {getattr(tool, 'description', '')}\n"
            system += tool_section

        messages.append(ChatMessage(role="system", content=system))

        # Add ambient context
        if context.ambient:
            ambient_parts = []
            if context.ambient.time_of_day:
                ambient_parts.append(f"Time: {context.ambient.time_of_day}")
            if context.ambient.location:
                ambient_parts.append(f"Location: {context.ambient.location}")
            if context.ambient.activity:
                ambient_parts.append(f"Activity: {context.ambient.activity}")

            if ambient_parts:
                messages.append(ChatMessage(
                    role="system",
                    content=f"Context: {', '.join(ambient_parts)}"
                ))

        # Add personality context
        if context.personality:
            personality_parts = []
            p = context.personality
            if p.formality > 0.7:
                personality_parts.append("Use formal language")
            elif p.formality < 0.3:
                personality_parts.append("Be casual and friendly")
            if p.verbosity > 0.7:
                personality_parts.append("Be thorough and detailed")
            elif p.verbosity < 0.3:
                personality_parts.append("Be concise")

            if personality_parts:
                messages.append(ChatMessage(
                    role="system",
                    content=f"Style: {', '.join(personality_parts)}"
                ))

        # Add goal context
        if context.goal:
            messages.append(ChatMessage(
                role="system",
                content=f"Goal: {context.goal.description}"
            ))

        # Add relevant facts
        if context.facts:
            fact_texts = [f.natural_lang_repr for f in context.facts[:5]]
            if fact_texts:
                messages.append(ChatMessage(
                    role="system",
                    content=f"Known facts: {', '.join(fact_texts)}"
                ))

        # Add conversation history
        for msg in context.conversation:
            role = getattr(msg, 'role', 'user')
            if hasattr(role, 'value'):
                role = role.value
            content = getattr(msg, 'content', '')
            messages.append(ChatMessage(role=role, content=content))

        return messages

    def _parse_response(self, result: Any, context: Context) -> Decision:
        """
        Parse LLM response into a Decision.

        Args:
            result: LLM response
            context: Original context (for fallbacks)

        Returns:
            Parsed Decision
        """
        content = getattr(result, 'content', '')
        tool_calls = getattr(result, 'tool_calls', []) or []

        # Check if we have tool calls
        if tool_calls:
            return Decision.execute_tools(
                tool_calls=tool_calls,
                reasoning=f"Using {len(tool_calls)} tool(s) to complete the request"
            )

        # Return text response
        if content:
            return Decision.respond(
                text=content,
                reasoning="Direct response to user"
            )

        # Fallback
        return Decision.respond(
            text="I'm not sure how to respond. Could you clarify?",
            reasoning="Empty response from LLM"
        )

    def _format_conversation(self, messages: list[Any]) -> str:
        """Format conversation for the prompt."""
        lines = []
        for msg in messages:
            role = getattr(msg, 'role', 'unknown')
            if hasattr(role, 'value'):
                role = role.value
            content = getattr(msg, 'content', '')
            lines.append(f"{role.upper()}: {content}")
        return "\n".join(lines)