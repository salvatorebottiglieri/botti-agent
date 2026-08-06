"""Goal routes - goal creation and management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.params import Path

from cortex.api.auth import get_api_key
from cortex.api.dependencies import get_execution_module
from cortex.api.schemas import GoalCreateRequest, GoalResponse, GoalResultResponse, GoalStepResponse

if TYPE_CHECKING:
    from cortex.execution.module import ExecutionModule

router = APIRouter(prefix="/goals", tags=["goals"])


@router.post(
    "",
    response_model=GoalResponse,
    summary="Create and start a goal",
    description="Creates a new goal and immediately starts execution.",
)
async def create_goal(
    request: GoalCreateRequest,
    key: str = Depends(get_api_key),
    execution_module: ExecutionModule = Depends(get_execution_module),
) -> GoalResponse:
    """
    Create a new goal and start execution.

    The goal runs asynchronously in the background.
    Use GET /goals/{id} to check status.
    """
    try:
        goal = await execution_module.create_goal(
            description=request.description,
            priority=request.priority,
            deadline=request.deadline,
        )

        return GoalResponse(
            id=goal.id,
            description=goal.description,
            status=goal.status.value,
            priority=goal.priority,
            created_at=goal.created_at,
            started_at=goal.started_at,
            completed_at=goal.completed_at,
            error=goal.error,
            steps=[
                GoalStepResponse(
                    step_number=s.step_number,
                    action=s.action,
                    result=s.result,
                    error=s.error,
                )
                for s in goal.steps
            ],
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "internal_error", "detail": str(e)},
        )


@router.get(
    "",
    summary="List active goals",
    description="Returns all pending and running goals.",
)
async def list_goals(
    key: str = Depends(get_api_key),
    execution_module: ExecutionModule = Depends(get_execution_module),
) -> list[GoalResponse]:
    """List active goals."""
    goals = await execution_module.list_active_goals()
    return [
        GoalResponse(
            id=g.id,
            description=g.description,
            status=g.status.value,
            priority=g.priority,
            created_at=g.created_at,
            started_at=g.started_at,
            completed_at=g.completed_at,
            error=g.error,
            steps=[
                GoalStepResponse(
                    step_number=s.step_number,
                    action=s.action,
                    result=s.result,
                    error=s.error,
                )
                for s in g.steps
            ],
        )
        for g in goals
    ]


@router.get(
    "/{goal_id}",
    summary="Get goal status and result",
    description="Returns the current status of a goal and its result if completed.",
)
async def get_goal(
    goal_id: Annotated[UUID, Path(description="Goal ID")],
    key: str = Depends(get_api_key),
    execution_module: ExecutionModule = Depends(get_execution_module),
) -> GoalResponse | GoalResultResponse:
    """
    Get a goal by ID.

    Returns the goal with its current status.
    If completed or failed, includes the result.
    """
    goal = await execution_module.get_goal(goal_id)

    if goal is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "detail": "Goal not found"},
        )

    # If completed or failed, return the result
    if goal.status.value in ("completed", "failed"):
        return GoalResultResponse(
            goal_id=goal.id,
            success=goal.status.value == "completed",
            message=goal.description,
            error=goal.error,
        )

    return GoalResponse(
        id=goal.id,
        description=goal.description,
        status=goal.status.value,
        priority=goal.priority,
        created_at=goal.created_at,
        started_at=goal.started_at,
        completed_at=goal.completed_at,
        error=goal.error,
        steps=[
            GoalStepResponse(
                step_number=s.step_number,
                action=s.action,
                result=s.result,
                error=s.error,
            )
            for s in goal.steps
        ],
    )
