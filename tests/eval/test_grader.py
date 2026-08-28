"""Tests for the deterministic goal-state grader (ADR-0015)."""

from __future__ import annotations

from cortex.eval.fixtures import GoalFile, GoalState
from cortex.eval.grader import grade_goal
from cortex.eval.sandbox import TaskSandbox


class TestGradeGoal:
    """Goal-state grading is deterministic and executable."""

    def _sandbox(self, tmp_path, files: dict[str, str]) -> TaskSandbox:
        sandbox = TaskSandbox(root=tmp_path / "sandbox")
        for name, content in files.items():
            path = sandbox.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return sandbox

    def test_passes_when_goal_files_exist(self, tmp_path):
        """A bare file expectation passes when the file exists."""
        sandbox = self._sandbox(tmp_path, {"answer.txt": "42"})
        result = grade_goal(GoalState(files=[GoalFile(path="answer.txt")]), sandbox)
        assert result.passed is True
        assert result.failures == []

    def test_fails_when_goal_file_missing(self, tmp_path):
        """A missing expected file fails with a clear reason."""
        sandbox = self._sandbox(tmp_path, {})
        result = grade_goal(GoalState(files=[GoalFile(path="answer.txt")]), sandbox)
        assert result.passed is False
        assert "answer.txt" in result.failures[0]

    def test_equals_must_match_exactly(self, tmp_path):
        """equals requires exact content."""
        sandbox = self._sandbox(tmp_path, {"answer.txt": "43"})
        result = grade_goal(
            GoalState(files=[GoalFile(path="answer.txt", equals="42")]), sandbox
        )
        assert result.passed is False
        sandbox2 = self._sandbox(tmp_path, {"answer.txt": "42"})
        assert grade_goal(
            GoalState(files=[GoalFile(path="answer.txt", equals="42")]), sandbox2
        ).passed is True

    def test_contains_is_substring(self, tmp_path):
        """contains only requires the substring."""
        sandbox = self._sandbox(tmp_path, {"answer.txt": "the answer is 42"})
        result = grade_goal(
            GoalState(files=[GoalFile(path="answer.txt", contains="42")]), sandbox
        )
        assert result.passed is True
        sandbox2 = self._sandbox(tmp_path, {"answer.txt": "the answer is 43"})
        assert grade_goal(
            GoalState(files=[GoalFile(path="answer.txt", contains="42")]), sandbox2
        ).passed is False

    def test_absent_file_fails_when_present(self, tmp_path):
        """absent paths must not exist in the sandbox."""
        sandbox = self._sandbox(tmp_path, {"scratch.txt": "temp"})
        result = grade_goal(GoalState(absent=["scratch.txt"]), sandbox)
        assert result.passed is False
        sandbox2 = TaskSandbox(root=tmp_path / "empty")
        assert grade_goal(GoalState(absent=["scratch.txt"]), sandbox2).passed is True

    def test_answer_match(self, tmp_path):
        """The exact-answer goal compares the final response text."""
        sandbox = self._sandbox(tmp_path, {})
        goal = GoalState(answer="42")
        assert grade_goal(goal, sandbox, final_message="42").passed is True
        assert grade_goal(goal, sandbox, final_message="43").passed is False
    def test_answer_variants_match_normalized(self, tmp_path):
        """A list of accepted answers matches case/whitespace-insensitively."""
        sandbox = self._sandbox(tmp_path, {})
        goal = GoalState(answer=["William Shakespeare", "Shakespeare"])
        assert (
            grade_goal(goal, sandbox, final_message="  William   SHAKESPEARE ").passed
            is True
        )
        assert grade_goal(goal, sandbox, final_message="Shakespeare").passed is True
        assert grade_goal(goal, sandbox, final_message="W. Shakespeare").passed is False

    def test_answer_variant_failure_names_accepted_set(self, tmp_path):
        """A variant miss fails and names the accepted answers."""
        sandbox = self._sandbox(tmp_path, {})
        result = grade_goal(
            GoalState(answer=["Paris", "paris"]), sandbox, final_message="London"
        )
        assert result.passed is False
        assert "accepted" in result.failures[0]

    def test_string_answer_stays_exact(self, tmp_path):
        """A plain-string answer keeps exact matching — no normalization."""
        sandbox = self._sandbox(tmp_path, {})
        goal = GoalState(answer="Paris")
        assert grade_goal(goal, sandbox, final_message="Paris").passed is True
        assert grade_goal(goal, sandbox, final_message="paris").passed is False
        assert grade_goal(goal, sandbox, final_message=" Paris ").passed is False

    def test_all_checks_must_pass(self, tmp_path):
        """Any single failure fails the whole goal."""
        sandbox = self._sandbox(tmp_path, {"answer.txt": "42", "scratch.txt": "x"})
        result = grade_goal(
            GoalState(
                files=[GoalFile(path="answer.txt", equals="42")],
                absent=["scratch.txt"],
            ),
            sandbox,
        )
        assert result.passed is False
        assert len(result.failures) == 1
