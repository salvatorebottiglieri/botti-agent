"""Tests for agentic models."""

import pytest
from datetime import datetime
from uuid import uuid4
from dataclasses import dataclass, field

from cortex.agentic.models import (
    Decision,
    DecisionType,
    Mode,
    Context,
    GoalContext,
    ChatResponse,
    GoalResult,
    GoalStatus,
    Goal,
    GoalStep,
    MaxIterationsError,
)


class TestMode:
    """Tests for Mode enum."""

    def test_chat_mode_is_chat(self):
        assert Mode.CHAT.value == "chat"

    def test_goal_mode_is_goal(self):
        assert Mode.GOAL.value == "goal"

    def test_mode_has_correct_values(self):
        assert Mode.CHAT == Mode.CHAT
        assert Mode.GOAL == Mode.GOAL


class TestDecisionType:
    """Tests for DecisionType enum."""

    def test_respond_type_exists(self):
        assert DecisionType.RESPOND == DecisionType.RESPOND

    def test_execute_tools_type_exists(self):
        assert DecisionType.EXECUTE_TOOLS == DecisionType.EXECUTE_TOOLS

    def test_ask_question_type_exists(self):
        assert DecisionType.ASK_QUESTION == DecisionType.ASK_QUESTION


class TestDecision:
    """Tests for Decision dataclass."""

    def test_respond_decision(self):
        """Decision to respond with text."""
        decision = Decision(
            decision_type=DecisionType.RESPOND,
            text="Hello, how can I help?",
            tool_calls=None,
            reasoning="User asked a simple question",
        )

        assert decision.decision_type == DecisionType.RESPOND
        assert decision.text == "Hello, how can I help?"
        assert decision.tool_calls is None

    def test_execute_tools_decision(self):
        """Decision to execute tools."""
        from cortex.tools.interfaces import ToolCall

        tool_calls = [
            ToolCall(name="file_read", arguments={"path": "/tmp/test.txt"})
        ]

        decision = Decision(
            decision_type=DecisionType.EXECUTE_TOOLS,
            text=None,
            tool_calls=tool_calls,
            reasoning="Need to read file to answer question",
        )

        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert len(decision.tool_calls) == 1
        assert decision.tool_calls[0].name == "file_read"

    def test_ask_question_decision(self):
        """Decision to ask for clarification."""
        decision = Decision(
            decision_type=DecisionType.ASK_QUESTION,
            text="Did you mean the project in /home or /work?",
            tool_calls=None,
            reasoning="Ambiguous request needs clarification",
        )

        assert decision.decision_type == DecisionType.ASK_QUESTION
        assert "Ambiguous" in decision.reasoning

    def test_respond_factory(self):
        """Factory method for RESPOND decisions."""
        decision = Decision.respond("Simple response")

        assert decision.decision_type == DecisionType.RESPOND
        assert decision.text == "Simple response"
        assert decision.tool_calls is None

    def test_execute_tools_factory(self):
        """Factory method for EXECUTE_TOOLS decisions."""
        from cortex.tools.interfaces import ToolCall

        calls = [ToolCall(name="grep", arguments={"pattern": "TODO"})]
        decision = Decision.execute_tools(calls, reasoning="Need to search")

        assert decision.decision_type == DecisionType.EXECUTE_TOOLS
        assert decision.tool_calls == calls

    def test_ask_question_factory(self):
        """Factory method for ASK_QUESTION decisions."""
        decision = Decision.ask_question("Which file did you mean?")

        assert decision.decision_type == DecisionType.ASK_QUESTION


class TestContext:
    """Tests for Context dataclass."""

    def test_context_creation(self):
        """Context can be created with all fields."""
        context = Context(
            session_id=uuid4(),
            conversation=[],
            facts=[],
            tools=[],
            personality=None,
            goal=None,
            ambient=None,
        )

        assert context.session_id is not None

    def test_context_with_messages(self):
        """Context can hold conversation messages."""
        from cortex.sessions.models import Message, MessageRole

        messages = [
            Message(session_id=uuid4(), role=MessageRole.USER, content="Hello"),
            Message(session_id=uuid4(), role=MessageRole.ASSISTANT, content="Hi there!"),
        ]

        context = Context(
            session_id=uuid4(),
            conversation=messages,
            facts=[],
            tools=[],
        )

        assert len(context.conversation) == 2

    def test_context_with_facts(self):
        """Context can hold relevant facts."""
        from cortex.memory.models import Fact, FactType, FactMutability

        facts = [
            Fact(
                type=FactType.LOCATION,
                symbolic_repr="location.home",
                natural_lang_repr="At home",
                mutability=FactMutability.MUTABLE,
            )
        ]

        context = Context(
            session_id=uuid4(),
            conversation=[],
            facts=facts,
            tools=[],
        )

        assert len(context.facts) == 1

    def test_context_defaults(self):
        """Context has sensible defaults."""
        context = Context(session_id=uuid4())

        assert context.conversation == []
        assert context.facts == []
        assert context.tools == []
        assert context.personality is None
        assert context.goal is None


class TestGoalContext:
    """Tests for GoalContext."""

    def test_goal_context_creation(self):
        """GoalContext holds goal information."""
        goal_id = uuid4()

        goal_ctx = GoalContext(
            goal_id=goal_id,
            description="Clean up the project directory",
            priority="high",
        )

        assert goal_ctx.goal_id == goal_id
        assert "Clean up" in goal_ctx.description
        assert goal_ctx.priority == "high"

    def test_goal_context_with_steps(self):
        """GoalContext can track progress steps."""
        goal_id = uuid4()

        goal_ctx = GoalContext(
            goal_id=goal_id,
            description="Complex task",
            steps_completed=["Step 1", "Step 2"],
        )

        assert len(goal_ctx.steps_completed) == 2


class TestChatResponse:
    """Tests for ChatResponse."""

    def test_chat_response_creation(self):
        """ChatResponse holds the response message."""
        response = ChatResponse(
            message="Here's your file.",
            iterations=3,
        )

        assert response.message == "Here's your file."
        assert response.iterations == 3

    def test_chat_response_with_metadata(self):
        """ChatResponse can include metadata."""
        response = ChatResponse(
            message="Done",
            iterations=1,
            tools_used=["file_read", "shell"],
            session_id=uuid4(),
        )

        assert "file_read" in response.tools_used

    def test_chat_response_defaults(self):
        """ChatResponse has sensible defaults."""
        response = ChatResponse(message="Hello")

        assert response.iterations == 0
        assert response.tools_used == []
        assert response.session_id is None


class TestGoalStatus:
    """Tests for GoalStatus enum."""

    def test_goal_status_values(self):
        """All expected status values exist."""
        assert GoalStatus.PENDING == GoalStatus.PENDING
        assert GoalStatus.RUNNING == GoalStatus.RUNNING
        assert GoalStatus.PAUSED == GoalStatus.PAUSED
        assert GoalStatus.COMPLETED == GoalStatus.COMPLETED
        assert GoalStatus.FAILED == GoalStatus.FAILED


class TestGoal:
    """Tests for Goal model."""

    def test_goal_creation(self):
        """Goal can be created."""
        goal = Goal(
            description="Fix the bug",
        )

        assert goal.description == "Fix the bug"
        assert goal.status == GoalStatus.PENDING

    def test_goal_with_priority(self):
        """Goal can have priority."""
        goal = Goal(
            description="Urgent task",
            priority="high",
        )

        assert goal.priority == "high"

    def test_goal_update_status(self):
        """Goal status can be updated."""
        goal = Goal(description="Test")

        goal.status = GoalStatus.RUNNING
        assert goal.status == GoalStatus.RUNNING


class TestGoalStep:
    """Tests for GoalStep model."""

    def test_goal_step_creation(self):
        """GoalStep can be created."""
        step = GoalStep(
            step_number=1,
            action="Read the file",
        )

        assert step.step_number == 1
        assert step.action == "Read the file"

    def test_goal_step_with_result(self):
        """GoalStep can hold execution result."""
        step = GoalStep(
            step_number=1,
            action="Write data",
            result="Data written successfully",
        )

        assert step.result == "Data written successfully"


class TestMaxIterationsError:
    """Tests for MaxIterationsError."""

    def test_error_contains_limit(self):
        """Error includes the iteration limit."""
        error = MaxIterationsError(max_iterations=20)

        assert error.max_iterations == 20

    def test_error_has_message(self):
        """Error has a human-readable message."""
        error = MaxIterationsError(max_iterations=20)

        assert "20" in str(error)
        assert "iteration" in str(error).lower()

    def test_error_is_exception(self):
        """Error is a proper exception."""
        error = MaxIterationsError(max_iterations=10)

        with pytest.raises(MaxIterationsError):
            raise error