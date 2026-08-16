"""Reasoner - LLM-powered decision making."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from cortex.agentic.models import (
    Context,
    Decision,
)
from cortex.llm.models import ChatMessage, ChatResult, Role
from cortex.sessions.models import MessageRole
from cortex.tools.interfaces import ToolCall

if TYPE_CHECKING:
    from cortex.llm.base import LLMClient
    from cortex.tools.interfaces import ToolRegistry

logger = logging.getLogger(__name__)

QUESTION_PATTERN = re.compile(r"\[QUESTION\](.*?)\[/QUESTION\]", re.DOTALL)


_MESSAGE_ROLE_TO_LLM_ROLE: dict[str, Role] = {
    MessageRole.SYSTEM.value: Role.SYSTEM,
    MessageRole.USER.value: Role.USER,
    MessageRole.ASSISTANT.value: Role.ASSISTANT,
    MessageRole.TOOL_RESULT.value: Role.TOOL,
}


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
        return (
            "You are Cortex, an intelligent AI assistant. "
            "When a tool fits the user's request, call it; otherwise respond directly. "
            "Always be helpful, concise, and precise. "
            "If you need clarification from the user, respond with "
            "[QUESTION]your question here[/QUESTION] and nothing else."
        )

    async def reason(self, context: Context) -> Decision:
        """
        Make a decision based on context.

        Args:
            context: All context needed for reasoning

        Returns:
            Decision on what to do next
        """
        messages = self._build_prompt(context)
        tools = context.tools if context.tools else None

        try:
            result = await self._llm.chat(messages, tools=tools)
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

        Tools are advertised structurally via the `tools=` argument to
        `llm.chat`; they are not duplicated in the system prompt text.
        """
        messages = []

        messages.append(ChatMessage(role=Role.SYSTEM, content=self._system_prompt))

        ambient = context.memory.ambient
        if ambient:
            ambient_parts = []
            if ambient.time_of_day:
                ambient_parts.append(f"Time: {ambient.time_of_day}")
            if ambient.location:
                ambient_parts.append(f"Location: {ambient.location}")
            if ambient.activity:
                ambient_parts.append(f"Activity: {ambient.activity}")

            if ambient_parts:
                messages.append(ChatMessage(
                    role=Role.SYSTEM,
                    content=f"Context: {', '.join(ambient_parts)}"
                ))

        personality = context.memory.personality
        if personality:
            personality_parts = []
            if personality.formality > 0.7:
                personality_parts.append("Use formal language")
            elif personality.formality < 0.3:
                personality_parts.append("Be casual and friendly")
            if personality.verbosity > 0.7:
                personality_parts.append("Be thorough and detailed")
            elif personality.verbosity < 0.3:
                personality_parts.append("Be concise")

            if personality_parts:
                messages.append(ChatMessage(
                    role=Role.SYSTEM,
                    content=f"Style: {', '.join(personality_parts)}"
                ))

        if context.goal:
            messages.append(ChatMessage(
                role=Role.SYSTEM,
                content=f"Goal: {context.goal.description}"
            ))

        if context.memory.facts:
            fact_texts = [f.natural_lang_repr for f in context.memory.facts[:5]]
            if fact_texts:
                messages.append(ChatMessage(
                    role=Role.SYSTEM,
                    content=f"Known facts: {', '.join(fact_texts)}"
                ))

        for msg in context.conversation:
            messages.append(ChatMessage(
                role=_MESSAGE_ROLE_TO_LLM_ROLE[msg.role],
                content=msg.content,
                tool_call_id=msg.tool_call_id,
                tool_calls=(
                    [ToolCall(**tc) for tc in msg.tool_calls]
                    if msg.tool_calls
                    else None
                ),
            ))

        return messages

    def _parse_response(self, result: ChatResult, context: Context) -> Decision:
        """Parse LLM response into a Decision."""
        message = result.message
        if message is None:
            raise ValueError("Empty response from LLM")

        content = message.content or ""
        tool_calls = result.tool_calls or []

        if not content and not tool_calls:
            raise ValueError("Empty response from LLM")

        if tool_calls:
            return Decision.execute_tools(
                tool_calls=list(tool_calls),
                reasoning=f"Using {len(tool_calls)} tool(s) to complete the request",
            )

        match = QUESTION_PATTERN.search(content)
        if match:
            question = match.group(1).strip()
            if not question:
                raise ValueError("LLM signaled clarification with no question")
            return Decision.ask_question(
                question=question,
                reasoning="LLM signaled need for clarification",
            )

        return Decision.respond(
            text=content,
            reasoning="Direct response to user",
        )

