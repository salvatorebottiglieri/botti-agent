"""Route-level tests for GET /goals/{id} result reporting.

The seam is the full HTTP path: auth, execution-module dependency, and the
terminal-result payload (message/iterations must come from the stored
GoalResult, not from goal metadata).
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from cortex.agentic.models import Goal, GoalResult, GoalStatus
from cortex.api.auth import get_api_key
from cortex.api.dependencies import get_execution_module
from cortex.main import create_app

AUTH_HEADERS = {"Authorization": "Bearer dummy-key"}


def _goal(status: GoalStatus, description: str = "Find TODOs") -> Goal:
    now = datetime.now(UTC).timestamp()
    return Goal(
        id=uuid4(),
        description=description,
        status=status,
        created_at=now,
        started_at=now,
        completed_at=now if status in (GoalStatus.COMPLETED, GoalStatus.FAILED) else None,
        error=None,
    )


def build_client(execution_module) -> TestClient:
    """App with execution-module and auth dependencies overridden."""
    settings = MagicMock()
    settings.version = "0.1.0"
    with patch("cortex.main.get_settings", return_value=settings):
        app = create_app()
    app.dependency_overrides[get_api_key] = lambda: "dummy-key"
    app.dependency_overrides[get_execution_module] = lambda: execution_module
    return TestClient(app)


class TestGetGoalResult:
    """GET /goals/{id} reports the stored terminal result."""

    def test_completed_goal_returns_actual_message_and_iterations(self):
        goal = _goal(GoalStatus.COMPLETED)
        result = GoalResult(
            goal_id=goal.id,
            success=True,
            message="Found 3 TODOs in 2 files",
            iterations=4,
            steps_completed=["grep", "file_read"],
        )

        module = MagicMock()
        module.get_goal = AsyncMock(return_value=goal)
        module.get_goal_result = AsyncMock(return_value=result)
        client = build_client(module)

        response = client.get(f"/goals/{goal.id}", headers=AUTH_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["message"] == "Found 3 TODOs in 2 files"
        assert body["iterations"] == 4

    def test_failed_goal_reports_error(self):
        goal = _goal(GoalStatus.FAILED, description="Doomed")
        result = GoalResult(
            goal_id=goal.id,
            success=False,
            message="Goal failed",
            iterations=2,
            error="boom",
        )

        module = MagicMock()
        module.get_goal = AsyncMock(return_value=goal)
        module.get_goal_result = AsyncMock(return_value=result)
        client = build_client(module)

        response = client.get(f"/goals/{goal.id}", headers=AUTH_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body["success"] is False
        assert body["error"] == "boom"
        assert body["iterations"] == 2

    def test_running_goal_returns_status_not_result(self):
        goal = _goal(GoalStatus.RUNNING)

        module = MagicMock()
        module.get_goal = AsyncMock(return_value=goal)
        client = build_client(module)

        response = client.get(f"/goals/{goal.id}", headers=AUTH_HEADERS)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "running"
        assert "success" not in body

    def test_missing_goal_returns_404(self):
        module = MagicMock()
        module.get_goal = AsyncMock(return_value=None)
        client = build_client(module)

        response = client.get(f"/goals/{uuid4()}", headers=AUTH_HEADERS)

        assert response.status_code == 404
