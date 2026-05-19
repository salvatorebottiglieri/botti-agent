# PyDatalog concept validation with LLM retry loop

Concepts derived by the LLM were stored with `validated=False` and never validated.
All concepts in the database were perpetually unvalidated — the `validated` column
was dead, and `get_validated()` returned empty results.

We added `ConceptValidationService` backed by a PyDatalog `LogicEngine`. The loop:

1. LLM proposes a `ConceptDerivation` with a symbolic proof chain
2. `LogicEngine` loads source facts as Datalog axioms, parses the proof chain as
   Datalog assertions, and checks consistency
3. If valid → stored with `validated=True`
4. If invalid → `Rejection` returned with conflicting facts and suggested fix.
   LLM retries with the rejection context (max 3 retries)
5. After max retries → `concept.rejected` event emitted

A pure-Python structural validator was rejected because it can't detect logical
contradictions — e.g., a concept claiming "user is at office" when a fact says
"user is at home" with overlapping timestamps. PyDatalog catches these.
