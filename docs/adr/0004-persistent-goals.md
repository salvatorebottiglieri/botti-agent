# Persistent goals via GoalRepository

Goals were stored in an in-memory `dict[UUID, Goal]` and lost on restart, despite the `goals`
and `goal_steps` tables already existing in migration 005. The `ExecutionModule` docstring
acknowledged this as a known gap.

We added `GoalRepository` (ABC) + `PostgresGoalRepository` following the same pattern as
`SessionRepository` and `FactRepository`. The in-memory dict was deleted; all goal state
now goes through the repository.

On startup, `ExecutionModule` queries `GoalRepository.get_in_flight()` for goals with
`status = 'running'` at the time of the last shutdown, marks them `pending`, and resumes
execution automatically. The resume is a direct call — not routed through the event bus,
because goal recovery is a startup concern, not a runtime communication concern. No other
module needs to observe `goal.resumed`.
