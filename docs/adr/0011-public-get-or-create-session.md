# Public get_or_create_session on InteractionService

The chat route called `interaction_service._get_or_create_session()` — a private
method — for session lookup/creation. The `sessions/policy.py` module already
contained the `get_or_create_session()` function but was never wired through
`InteractionService`.

We exposed `get_or_create_session(session_id: UUID | None) -> Session` as a public
method on `InteractionService`, backed by `sessions/policy.py`. The chat route and
SSE route now call one public method instead of branching on `request.session_id`
and calling different private methods.
