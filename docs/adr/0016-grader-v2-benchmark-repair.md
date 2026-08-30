# Deterministic grader v2: benchmark-repair for loop and refusal suites

The goal-state grader in `src/cortex/eval/grader.py` evolved from equality-only checks
(`equals` byte-per-byte, `answer` any-of after case/whitespace normalization) into a
v2 schema that (a) tolerates trailing whitespace on file content, (b) compares JSON
semantically rather than byte-for-byte, (c) replaces the refusal accept-list of
phrasings with a semantic refusal check (refusal keyword + absence of forbidden
patterns), and (d) accepts substring containment for the comply `answer` list to
absorb natural-language wrappers like "The capital of X is Y" and "X stands for Y".
The `GRADING_SCHEMA_VERSION` constant pins the schema; suites record `grading_version`
in their manifests and the gate refuses to compare baselines across version bumps.
This is the SWE-bench-Verified pattern applied in-repo: under-specified or
over-strict checks systematically underestimate models (38.3% underspecified and
61.1% unfair unit tests in the original SWE-bench; documented Terminal-Bench grader
flakiness when graders assume unstated file paths; Anthropic's rule that an agent
failing 0% across trials is usually a broken task, not a broken agent — all in
`docs/research/agent-evaluation-literature.md` §2.2, §2.7, §5.2). The literal
v1 accept-list for refusal phrasings underestimated `deepseek-v4-flash` 11/13 times
on the live refusal suite even though the LLM judge consistently gave 0.94-1.0
partial credit on the same transcripts; v2 closes the gap (12/13 with the model
itself unchanged). Capability suite was removed entirely in the same change set
(ADR-0015 + decision: capability measures raw model behaviour on the model alone,
not the agent loop, and the personal-agent use case doesn't need a model-quality
gate — loop + refusal cover the surfaces that matter).