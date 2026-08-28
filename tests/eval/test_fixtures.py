"""Tests for the eval YAML fixture loader (ADR-0014 task schema)."""

from __future__ import annotations

import pytest

from cortex.eval.fixtures import GoalFile, load_suite


class TestLoadSuite:
    """Loading a well-formed suite from YAML."""

    def test_loads_suite_metadata(self, sample_suite):
        """Suite name and version come from the YAML header."""
        assert sample_suite.name == "sample"
        assert sample_suite.version == "1.0.0"

    def test_loads_all_tasks(self, sample_suite):
        """Every task in the YAML is loaded, in order."""
        assert [t.name for t in sample_suite.tasks] == ["write-answer", "leave-marker"]

    def test_loads_scripted_turns(self, sample_suite):
        """Each task carries its scripted user turns."""
        assert sample_suite.tasks[0].turns == [
            "Read data/input.txt and write the sum of the numbers to answer.txt"
        ]

    def test_loads_sandbox_files(self, sample_suite):
        """Sandbox setup files are loaded with path and content."""
        assert sample_suite.tasks[0].sandbox[0].path == "data/input.txt"
        assert sample_suite.tasks[0].sandbox[0].content == "40\n2"

    def test_loads_goal_state(self, sample_suite):
        """Annotated goal state (files/absent/answer) is loaded."""
        goal = sample_suite.tasks[0].goal
        assert goal.files == [GoalFile(path="answer.txt", contains="42")]
        assert goal.absent == []
        assert goal.answer is None

    def test_goal_state_with_absent_and_answer(self, tmp_path):
        """absent paths and exact answers parse too."""
        path = tmp_path / "suite.yaml"
        path.write_text(
            """
suite:
  name: s
  version: 2.0.0
tasks:
  - name: t
    turns: ["hello"]
    goal:
      absent: ["tmp/scratch.txt"]
      answer: "42"
""",
            encoding="utf-8",
        )
        suite = load_suite(path)
        assert suite.tasks[0].goal.absent == ["tmp/scratch.txt"]
        assert suite.tasks[0].goal.answer == "42"
    def test_goal_answer_accepts_variants_list(self, tmp_path):
        """goal.answer may be a list of accepted answer strings."""
        path = tmp_path / "suite.yaml"
        path.write_text(
            """
suite:
  name: s
  version: 2.0.0
tasks:
  - name: t
    turns: ["hello"]
    goal:
      answer: ["Paris", "paris"]
""",
            encoding="utf-8",
        )
        suite = load_suite(path)
        assert suite.tasks[0].goal.answer == ["Paris", "paris"]

    def test_goal_answer_rejects_invalid_variants(self, tmp_path):
        """Non-string entries and empty variant lists are rejected."""
        for bad in ("[1, 2]", "[]"):
            path = tmp_path / "suite.yaml"
            path.write_text(
                "suite:\n  name: s\n  version: 1.0.0\n"
                f"tasks:\n  - name: t\n    turns: ['hi']\n    goal:\n      answer: {bad}\n",
                encoding="utf-8",
            )
            with pytest.raises(ValueError, match="answer"):
                load_suite(path)


class TestLoadSuiteErrors:
    """Malformed fixtures fail loudly at load time."""

    def _write(self, tmp_path, yaml_text: str) -> str:
        path = tmp_path / "suite.yaml"
        path.write_text(yaml_text, encoding="utf-8")
        return str(path)

    def test_missing_file_raises(self, tmp_path):
        """Loading a nonexistent fixture raises."""
        with pytest.raises(OSError):
            load_suite(tmp_path / "nope.yaml")

    def test_missing_suite_key(self, tmp_path):
        path = self._write(tmp_path, "tasks: []\n")
        with pytest.raises(ValueError, match="suite"):
            load_suite(path)

    def test_missing_suite_name(self, tmp_path):
        path = self._write(tmp_path, "suite:\n  version: 1.0.0\ntasks: []\n")
        with pytest.raises(ValueError, match="name"):
            load_suite(path)

    def test_missing_suite_version(self, tmp_path):
        path = self._write(tmp_path, "suite:\n  name: s\ntasks: []\n")
        with pytest.raises(ValueError, match="version"):
            load_suite(path)

    def test_no_tasks(self, tmp_path):
        path = self._write(
            tmp_path, "suite:\n  name: s\n  version: 1.0.0\ntasks: []\n"
        )
        with pytest.raises(ValueError, match="tasks"):
            load_suite(path)

    def test_task_without_turns(self, tmp_path):
        path = self._write(
            tmp_path,
            "suite:\n  name: s\n  version: 1.0.0\ntasks:\n  - name: t\n"
            "    goal:\n      files:\n        - path: a\n",
        )
        with pytest.raises(ValueError, match="turns"):
            load_suite(path)

    def test_task_without_goal(self, tmp_path):
        path = self._write(
            tmp_path,
            "suite:\n  name: s\n  version: 1.0.0\ntasks:\n  - name: t\n"
            "    turns: ['hi']\n",
        )
        with pytest.raises(ValueError, match="goal"):
            load_suite(path)

    def test_empty_goal_state(self, tmp_path):
        """A goal with no checks is rejected — it would pass trivially."""
        path = self._write(
            tmp_path,
            "suite:\n  name: s\n  version: 1.0.0\ntasks:\n  - name: t\n"
            "    turns: ['hi']\n    goal: {}\n",
        )
        with pytest.raises(ValueError, match="goal"):
            load_suite(path)

    def test_goal_file_without_path(self, tmp_path):
        path = self._write(
            tmp_path,
            "suite:\n  name: s\n  version: 1.0.0\ntasks:\n  - name: t\n"
            "    turns: ['hi']\n    goal:\n      files:\n        - contains: x\n",
        )
        with pytest.raises(ValueError, match="path"):
            load_suite(path)
