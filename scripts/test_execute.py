"""
Safety-net tests for the execute.py refactor.
Verify that behavior is identical before and after refactoring.
"""

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import execute as ex


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_project(tmp_path):
    """Temporary project layout containing phases/, CLAUDE.md and docs/."""
    phases_dir = tmp_path / "phases"
    phases_dir.mkdir()

    claude_md = tmp_path / "CLAUDE.md"
    claude_md.write_text("# Rules\n- rule one\n- rule two")

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "ARCHITECTURE.md").write_text("# Architecture\nSome content")
    (docs_dir / "PRD.md").write_text("# Product\nAnother doc")

    return tmp_path


@pytest.fixture
def phase_dir(tmp_project):
    """Phase directory with three steps."""
    d = tmp_project / "phases" / "0-mvp"
    d.mkdir()

    index = {
        "project": "TestProject",
        "phase": "mvp",
        "steps": [
            {"step": 0, "name": "setup", "status": "completed", "summary": "Project scaffolding done"},
            {"step": 1, "name": "core", "status": "completed", "summary": "Core logic implemented"},
            {"step": 2, "name": "ui", "status": "pending"},
        ],
    }
    (d / "index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False))
    (d / "step2.md").write_text("# Step 2: UI\n\nImplement the UI.")

    return d


@pytest.fixture
def top_index(tmp_project):
    """phases/index.json (top-level)."""
    top = {
        "phases": [
            {"dir": "0-mvp", "status": "pending"},
            {"dir": "1-polish", "status": "pending"},
        ]
    }
    p = tmp_project / "phases" / "index.json"
    p.write_text(json.dumps(top, indent=2))
    return p


@pytest.fixture
def executor(tmp_project, phase_dir):
    """StepExecutor instance for tests. git calls must be mocked separately."""
    with patch.object(ex, "ROOT", tmp_project):
        inst = ex.StepExecutor("0-mvp")
    # Re-point the internal paths at tmp_project
    inst._root = str(tmp_project)
    inst._phases_dir = tmp_project / "phases"
    inst._phase_dir = phase_dir
    inst._phase_dir_name = "0-mvp"
    inst._index_file = phase_dir / "index.json"
    inst._top_index_file = tmp_project / "phases" / "index.json"
    return inst


# ---------------------------------------------------------------------------
# _stamp (formerly now_iso)
# ---------------------------------------------------------------------------

class TestStamp:
    def test_returns_kst_timestamp(self, executor):
        result = executor._stamp()
        assert "+0900" in result

    def test_format_is_iso(self, executor):
        result = executor._stamp()
        dt = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert dt.tzinfo is not None

    def test_is_current_time(self, executor):
        before = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0)
        result = executor._stamp()
        after = datetime.now(ex.StepExecutor.TZ).replace(microsecond=0) + timedelta(seconds=1)
        parsed = datetime.strptime(result, "%Y-%m-%dT%H:%M:%S%z")
        assert before <= parsed <= after


# ---------------------------------------------------------------------------
# _read_json / _write_json
# ---------------------------------------------------------------------------

class TestJsonHelpers:
    def test_roundtrip(self, tmp_path):
        data = {"key": "값", "nested": [1, 2, 3]}
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, data)
        loaded = ex.StepExecutor._read_json(p)
        assert loaded == data

    def test_save_ensures_ascii_false(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"한글": "테스트"})
        raw = p.read_text()
        assert "한글" in raw
        assert "\\u" not in raw

    def test_save_indented(self, tmp_path):
        p = tmp_path / "test.json"
        ex.StepExecutor._write_json(p, {"a": 1})
        raw = p.read_text()
        assert "\n" in raw

    def test_load_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ex.StepExecutor._read_json(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# _build_step_context
# ---------------------------------------------------------------------------

class TestBuildStepContext:
    def test_includes_completed_with_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "Step 0 (setup): Project scaffolding done" in result
        assert "Step 1 (core): Core logic implemented" in result

    def test_excludes_pending(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert "ui" not in result

    def test_excludes_completed_without_summary(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        del index["steps"][0]["summary"]
        result = ex.StepExecutor._build_step_context(index)
        assert "setup" not in result
        assert "core" in result

    def test_empty_when_no_completed(self):
        index = {"steps": [{"step": 0, "name": "a", "status": "pending"}]}
        result = ex.StepExecutor._build_step_context(index)
        assert result == ""

    def test_has_header(self, phase_dir):
        index = json.loads((phase_dir / "index.json").read_text())
        result = ex.StepExecutor._build_step_context(index)
        assert result.startswith("## Previous Step Output")


# ---------------------------------------------------------------------------
# _build_preamble
# ---------------------------------------------------------------------------

class TestBuildPreamble:
    def test_includes_project_name(self, executor):
        result = executor._build_preamble("")
        assert "TestProject" in result

    def test_includes_step_context(self, executor):
        ctx = "## Previous Step Output\n\n- Step 0: done"
        result = executor._build_preamble(ctx)
        assert "Previous Step Output" in result

    def test_includes_commit_example(self, executor):
        result = executor._build_preamble("")
        assert "feat(mvp):" in result

    def test_includes_rules(self, executor):
        result = executor._build_preamble("")
        assert "Working Rules" in result
        assert "AC" in result

    def test_no_retry_section_by_default(self, executor):
        result = executor._build_preamble("")
        assert "Previous attempt failed" not in result

    def test_retry_section_with_prev_error(self, executor):
        result = executor._build_preamble("", prev_error="TypeError raised")
        assert "Previous attempt failed" in result
        assert "TypeError raised" in result

    def test_includes_max_retries(self, executor):
        result = executor._build_preamble("")
        assert str(ex.StepExecutor.MAX_RETRIES) in result

    def test_includes_index_path(self, executor):
        result = executor._build_preamble("")
        assert "/phases/0-mvp/index.json" in result


# ---------------------------------------------------------------------------
# _update_top_index
# ---------------------------------------------------------------------------

class TestUpdateTopIndex:
    def test_completed(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "completed"
        assert "completed_at" in mvp

    def test_error(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("error")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "error"
        assert "failed_at" in mvp

    def test_blocked(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("blocked")
        data = json.loads(top_index.read_text())
        mvp = next(p for p in data["phases"] if p["dir"] == "0-mvp")
        assert mvp["status"] == "blocked"
        assert "blocked_at" in mvp

    def test_other_phases_unchanged(self, executor, top_index):
        executor._top_index_file = top_index
        executor._update_top_index("completed")
        data = json.loads(top_index.read_text())
        polish = next(p for p in data["phases"] if p["dir"] == "1-polish")
        assert polish["status"] == "pending"

    def test_nonexistent_dir_is_noop(self, executor, top_index):
        executor._top_index_file = top_index
        executor._phase_dir_name = "no-such-dir"
        original = json.loads(top_index.read_text())
        executor._update_top_index("completed")
        after = json.loads(top_index.read_text())
        for p_before, p_after in zip(original["phases"], after["phases"]):
            assert p_before["status"] == p_after["status"]

    def test_no_top_index_file(self, executor, tmp_path):
        executor._top_index_file = tmp_path / "nonexistent.json"
        executor._update_top_index("completed")  # should not raise


# ---------------------------------------------------------------------------
# _checkout_branch (mocked)
# ---------------------------------------------------------------------------

class TestCheckoutBranch:
    def _mock_git(self, executor, responses):
        call_idx = {"i": 0}
        def fake_git(*args):
            idx = call_idx["i"]
            call_idx["i"] += 1
            if idx < len(responses):
                return responses[idx]
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

    def test_already_on_branch(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="feat-mvp\n", stderr=""),
        ])
        executor._checkout_branch()  # should return without checkout

    def test_branch_exists_checkout(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_branch_not_exists_create(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="not found"),
            MagicMock(returncode=0, stdout="", stderr=""),
        ])
        executor._checkout_branch()

    def test_checkout_fails_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=0, stdout="main\n", stderr=""),
            MagicMock(returncode=1, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="dirty tree"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1

    def test_no_git_exits(self, executor):
        self._mock_git(executor, [
            MagicMock(returncode=1, stdout="", stderr="not a git repo"),
        ])
        with pytest.raises(SystemExit) as exc_info:
            executor._checkout_branch()
        assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _commit_step (mocked)
# ---------------------------------------------------------------------------

class TestCommitStep:
    def test_two_phase_commit(self, executor):
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        commit_calls = [c for c in calls if c[0] == "commit"]
        assert len(commit_calls) == 2
        assert "feat(mvp):" in commit_calls[0][2]
        assert "chore(mvp):" in commit_calls[1][2]

    def test_retry_exhaustion_commits_no_implementation(self, executor, tmp_project):
        """A step that never reached `completed` gets its failure record committed, and nothing
        else: a `feat(...)` commit of the failed attempt read in the log like a step that landed,
        and a timeout-killed attempt never committed that code itself. Real git, in tmp_path; no
        session is spawned (`_invoke_codex` is a fake that leaves broken code behind)."""
        def git(*args):
            return subprocess.run(["git", *args], cwd=tmp_project, check=True,
                                  capture_output=True, text=True).stdout

        git("init", "-q", ".")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        git("add", "-A")
        git("commit", "-qm", "baseline")
        broken = tmp_project / "lib" / "broken.ts"

        def failing_session(step, preamble):
            broken.parent.mkdir(exist_ok=True)
            broken.write_text("export const answer: number = 'not a number';\n")
            return {"step": step["step"], "name": step["name"], "exitCode": 1, "stdout": "", "stderr": ""}

        with patch.object(executor, "_invoke_codex", side_effect=failing_session), \
             patch.object(executor, "_update_top_index"):
            with pytest.raises(SystemExit) as exc:
                executor._execute_single_step({"step": 2, "name": "ui"})
        assert exc.value.code == 1

        subjects = git("log", "--format=%s").splitlines()
        committed = git("log", "--format=", "--name-only").split()
        assert not any(s.startswith("feat(") for s in subjects), subjects
        assert "lib/broken.ts" not in committed, (subjects, committed)
        assert "phases/0-mvp/index.json" in committed, committed
        assert broken.exists()          # left in the working tree for the operator

    def test_no_code_changes_skips_feat_commit(self, executor):
        call_count = {"diff": 0}
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[:2] == ("diff", "--cached"):
                call_count["diff"] += 1
                if call_count["diff"] == 1:
                    return MagicMock(returncode=0)
                return MagicMock(returncode=1)
            return MagicMock(returncode=0, stdout="", stderr="")
        executor._run_git = fake_git

        executor._commit_step(2, "ui")

        commit_msgs = [c[2] for c in calls if c[0] == "commit"]
        assert len(commit_msgs) == 1
        assert "chore" in commit_msgs[0]


# ---------------------------------------------------------------------------
# _invoke_codex (mocked)
# ---------------------------------------------------------------------------

class TestInvokeCodex:
    def test_invokes_codex_with_correct_args(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"result": "ok"}', stderr="")
        step = {"step": 2, "name": "ui"}
        preamble = "PREAMBLE\n"

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            output = executor._invoke_codex(step, preamble)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "codex"
        assert "exec" in cmd
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--dangerously-bypass-hook-trust" in cmd
        assert "--json" in cmd
        assert "PREAMBLE" in cmd[-1]
        assert "Implement the UI." in cmd[-1]

    def test_saves_output_json(self, executor):
        mock_result = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result):
            executor._invoke_codex(step, "preamble")

        output_file = executor._phase_dir / "step2-output.json"
        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["step"] == 2
        assert data["name"] == "ui"
        assert data["exitCode"] == 0

    def test_nonexistent_step_file_exits(self, executor):
        step = {"step": 99, "name": "nonexistent"}
        with pytest.raises(SystemExit) as exc_info:
            executor._invoke_codex(step, "preamble")
        assert exc_info.value.code == 1

    def test_timeout_is_1800(self, executor):
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_codex(step, "preamble")

        assert mock_run.call_args[1]["timeout"] == 1800

    def test_stdin_is_devnull(self, executor):
        """The child must not inherit our stdin, or a non-interactive run hangs.

        `codex exec` treats an open non-TTY stdin as additional prompt input
        ("Reading additional input from stdin...") and blocks on it forever.
        `capture_output=True` redirects stdout/stderr only, so without an explicit
        stdin every run launched without a TTY — CI, a pipe, an agent shell — burns
        the whole `timeout_s` (1800s by default) and the step fails with no output.
        """
        mock_result = MagicMock(returncode=0, stdout="{}", stderr="")
        step = {"step": 2, "name": "ui"}

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            executor._invoke_codex(step, "preamble")

        assert mock_run.call_args[1]["stdin"] is subprocess.DEVNULL


# ---------------------------------------------------------------------------
# progress_indicator (formerly Spinner)
# ---------------------------------------------------------------------------

class TestProgressIndicator:
    def test_context_manager(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.15)
        assert pi.elapsed >= 0.1

    def test_elapsed_increases(self):
        import time
        with ex.progress_indicator("test") as pi:
            time.sleep(0.2)
        assert pi.elapsed > 0


class TestNoGuardrailInjection:
    def test_prompt_carries_no_claude_md_or_docs(self, executor, tmp_project):
        """Codex reads AGENTS.md (a symlink to CLAUDE.md) as its project doc and opens the docs
        it needs from there; the runner must not paste CLAUDE.md or docs/*.md into the prompt."""
        executor._checkout_branch = lambda: None
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        prompts = []

        def succeed(step, preamble):
            prompts.append(preamble)
            index = json.loads(executor._index_file.read_text())
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "completed"
                    s["summary"] = "done"
            executor._index_file.write_text(json.dumps(index))
            return {"step": step["step"], "name": step["name"], "exitCode": 0, "stdout": "", "stderr": ""}

        with patch.object(ex, "ROOT", tmp_project), \
             patch.object(executor, "_invoke_codex", side_effect=succeed):
            executor.run()

        assert len(prompts) == 1
        assert "TestProject" in prompts[0]
        for injected in ("rule one", "# Architecture", "Some content", "Another doc"):
            assert injected not in prompts[0]


class TestRerunAfterFailure:
    def test_a_failed_rerun_does_not_finalize_the_phase(self, executor, top_index):
        """Reopen step 0 after steps 1-2 completed, let it fail three times, and invoke the
        executor again: it must stop on the error, not finalize the phase over it."""
        executor._checkout_branch = lambda: None
        executor._run_git = MagicMock(return_value=MagicMock(returncode=0, stdout="", stderr=""))
        index = json.loads(executor._index_file.read_text())
        for step, status in zip(index["steps"], ["pending", "completed", "completed"]):
            step["status"] = status
        executor._index_file.write_text(json.dumps(index))

        def failing(step, preamble):
            return {"step": step["step"], "name": step["name"], "exitCode": 1, "stdout": "", "stderr": ""}

        with patch.object(executor, "_invoke_codex", side_effect=failing):
            with pytest.raises(SystemExit) as first:
                executor.run()
        assert first.value.code == 1

        with patch.object(executor, "_invoke_codex", side_effect=AssertionError("nothing to run")):
            with pytest.raises(SystemExit) as second:
                executor.run()
        assert second.value.code == 1
        index = json.loads(executor._index_file.read_text())
        phase = next(p for p in json.loads(top_index.read_text())["phases"] if p["dir"] == "0-mvp")
        assert "completed_at" not in index
        assert phase["status"] == "error"


class TestStepDuration:
    def test_the_step_line_reports_the_real_duration(self, executor, capsys):
        """`pi.elapsed` is set when the progress context exits; read inside it, every line said 0s."""
        import time

        def slow_success(step, preamble):
            time.sleep(1.2)
            index = json.loads(executor._index_file.read_text())
            for s in index["steps"]:
                if s["step"] == step["step"]:
                    s["status"] = "completed"
                    s["summary"] = "done"
            executor._index_file.write_text(json.dumps(index))
            return {"step": step["step"], "name": step["name"], "exitCode": 0, "stdout": "", "stderr": ""}

        with patch.object(executor, "_invoke_codex", side_effect=slow_success), \
             patch.object(executor, "_commit_step"):
            assert executor._execute_single_step({"step": 2, "name": "ui"}) is True
        assert "✓ Step 2: ui [1s]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# main() CLI parsing (mocked)
# ---------------------------------------------------------------------------

class TestMainCli:
    def test_no_args_exits(self):
        with patch("sys.argv", ["execute.py"]):
            with pytest.raises(SystemExit) as exc_info:
                ex.main()
            assert exc_info.value.code == 2  # argparse exits with 2

    def test_invalid_phase_dir_exits(self):
        with patch("sys.argv", ["execute.py", "nonexistent"]):
            with patch.object(ex, "ROOT", Path("/tmp/fake_nonexistent")):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1

    def test_missing_index_exits(self, tmp_project):
        (tmp_project / "phases" / "empty").mkdir()
        with patch("sys.argv", ["execute.py", "empty"]):
            with patch.object(ex, "ROOT", tmp_project):
                with pytest.raises(SystemExit) as exc_info:
                    ex.main()
                assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# _check_blockers (formerly the main() error/blocked check)
# ---------------------------------------------------------------------------

class TestCheckBlockers:
    def _make_executor_with_steps(self, tmp_project, steps):
        d = tmp_project / "phases" / "test-phase"
        d.mkdir(exist_ok=True)
        index = {"project": "T", "phase": "test", "steps": steps}
        (d / "index.json").write_text(json.dumps(index))

        with patch.object(ex, "ROOT", tmp_project):
            inst = ex.StepExecutor.__new__(ex.StepExecutor)
        inst._root = str(tmp_project)
        inst._phases_dir = tmp_project / "phases"
        inst._phase_dir = d
        inst._phase_dir_name = "test-phase"
        inst._index_file = d / "index.json"
        inst._top_index_file = tmp_project / "phases" / "index.json"
        inst._phase_name = "test"
        inst._total = len(steps)
        return inst

    def test_error_step_exits_1(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "bad", "status": "error", "error_message": "fail"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    @pytest.mark.parametrize("statuses", [
        ["error", "completed"],                  # a reopened step that failed again
        ["completed", "error", "completed"],
        ["in_progress", "completed"],            # a status this executor never writes
    ])
    def test_a_blocker_behind_a_completed_step_still_stops(self, tmp_project, statuses):
        """The reverse scan stopped at the first non-pending step from the end, so a trailing
        `completed` hid an earlier error and `_finalize` marked the phase completed over it."""
        steps = [{"step": i, "name": f"s{i}", "status": st} for i, st in enumerate(statuses)]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 1

    def test_blocked_step_exits_2(self, tmp_project):
        steps = [
            {"step": 0, "name": "ok", "status": "completed"},
            {"step": 1, "name": "stuck", "status": "blocked", "blocked_reason": "API key"},
        ]
        inst = self._make_executor_with_steps(tmp_project, steps)
        with pytest.raises(SystemExit) as exc_info:
            inst._check_blockers()
        assert exc_info.value.code == 2


class TestStatus:
    """--status: read-only progress snapshot (no git repo, no execution)."""

    def test_status_prints_table_and_rollup(self, executor, capsys):
        executor.status()
        out = capsys.readouterr().out
        # step names and the per-status roll-up appear; nothing executes.
        assert "setup" in out and "core" in out and "ui" in out
        assert "2 completed" in out and "1 pending" in out
        assert "3 steps" in out
        # tmp_project is not a git repo, so the branch is reported as absent.
        assert "not created yet" in out

    def test_fmt_elapsed_units(self, executor):
        from datetime import timedelta
        now = datetime.now(ex.StepExecutor.TZ)
        assert executor._fmt_elapsed((now - timedelta(seconds=30)).isoformat()).endswith("s")
        assert executor._fmt_elapsed((now - timedelta(minutes=5)).isoformat()).endswith("m")
        assert "h" in executor._fmt_elapsed((now - timedelta(hours=2)).isoformat())
        assert executor._fmt_elapsed("not-a-date") == "?"


class TestLiveness:
    """--status must answer "is the background run still alive?" itself, so the
    manual pgrep/ps that kept getting bolted onto it is no longer needed."""

    LINES = [
        "1001 python3 scripts/execute.py 0-mvp",
        "1002 python3 scripts/execute.py 0-mvp --push",
        "1003 python3 scripts/execute.py 0-mvp --status",   # a status call, not a run
        "1004 python3 scripts/execute.py 1-polish",         # a different phase
        "1005 vim scripts/execute.py 0-mvp",                # not an interpreter run
    ]

    def test_finds_real_runs_of_this_phase(self):
        pids = ex.StepExecutor._live_run_pids(self.LINES, "0-mvp", own_pid=9999)
        assert pids == [1001, 1002]

    def test_excludes_own_status_process(self):
        pids = ex.StepExecutor._live_run_pids(self.LINES, "0-mvp", own_pid=1001)
        assert 1001 not in pids

    def test_other_phase_not_reported(self):
        assert ex.StepExecutor._live_run_pids(self.LINES, "1-polish", own_pid=9999) == [1004]

    def test_no_matches(self):
        assert ex.StepExecutor._live_run_pids(self.LINES, "2-ship", own_pid=9999) == []

    def test_tolerates_garbage_lines(self):
        assert ex.StepExecutor._live_run_pids(["", "notapid foo", "  "], "0-mvp", 1) == []

    def test_status_reports_liveness_line(self, executor, capsys):
        with patch.object(ex.StepExecutor, "_live_run_pids", return_value=[4242]):
            executor.status()
        out = capsys.readouterr().out
        assert "4242" in out and "running" in out.lower()

    def test_status_reports_not_running(self, executor, capsys):
        with patch.object(ex.StepExecutor, "_live_run_pids", return_value=[]):
            executor.status()
        assert "not running" in capsys.readouterr().out.lower()


class TestSummary:
    """--summary: read a completed step's summary in full, untruncated.

    The --status table shows a summary cut to the first 60 characters of its first line —
    so reading what a step actually produced meant parsing index.json by hand. This path
    prints the whole text.
    """

    LONG = (
        "First line — on its own this one line already runs well past the 60-character truncation --status applies.\n"
        "Second line: the evidence and the measurements.\n"
        "Third line: what is left."
    )

    def _make_summary_long(self, executor):
        idx = json.loads(executor._index_file.read_text())
        idx["steps"][1]["summary"] = self.LONG
        executor._index_file.write_text(json.dumps(idx, indent=2, ensure_ascii=False))

    def test_no_arg_prints_every_completed_step(self, executor, capsys):
        executor.summary()
        out = capsys.readouterr().out
        assert "Project scaffolding done" in out
        assert "Core logic implemented" in out
        assert "ui" not in out  # a pending step is left out

    def test_name_selects_one_step(self, executor, capsys):
        executor.summary("core")
        out = capsys.readouterr().out
        assert "Core logic implemented" in out
        assert "Project scaffolding done" not in out

    def test_index_selects_one_step(self, executor, capsys):
        executor.summary("0")
        out = capsys.readouterr().out
        assert "Project scaffolding done" in out
        assert "Core logic implemented" not in out

    def test_multiline_summary_is_not_truncated(self, executor, capsys):
        """The core regression test: no 60-character cut, no first-line-only printing."""
        self._make_summary_long(executor)
        executor.summary("core")
        out = capsys.readouterr().out
        for line in self.LONG.splitlines():
            assert line in out

    def test_status_table_still_truncates(self, executor, capsys):
        """--summary is a new path — the one-line --status table is left as it is."""
        self._make_summary_long(executor)
        executor.status()
        out = capsys.readouterr().out
        assert "Second line: the evidence and the measurements." not in out

    def test_unknown_step_errors(self, executor, capsys):
        with pytest.raises(SystemExit) as exc_info:
            executor.summary("no-such-step")
        assert exc_info.value.code == 1
        out = capsys.readouterr().out
        assert "no-such-step" in out
        assert "core" in out  # it tells you which steps exist

    def test_cli_flag_without_value(self, tmp_project, phase_dir, capsys):
        with patch("sys.argv", ["execute.py", "0-mvp", "--summary"]):
            with patch.object(ex, "ROOT", tmp_project):
                ex.main()
        assert "Core logic implemented" in capsys.readouterr().out

    def test_cli_flag_with_step_selector(self, tmp_project, phase_dir, capsys):
        with patch("sys.argv", ["execute.py", "0-mvp", "--summary", "core"]):
            with patch.object(ex, "ROOT", tmp_project):
                ex.main()
        out = capsys.readouterr().out
        assert "Core logic implemented" in out
        assert "Project scaffolding done" not in out


# ---------------------------------------------------------------------------
# Codex step timeout -> ordinary step failure (not a traceback)
# ---------------------------------------------------------------------------

class TestCodexTimeout:
    def test_invoke_codex_does_not_raise_on_timeout(self, executor):
        step = {"step": 2, "name": "ui", "timeout_s": 90}
        boom = subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=90)

        with patch("subprocess.run", side_effect=boom):
            output = executor._invoke_codex(step, "preamble")

        assert "90" in output["timeoutMessage"]
        assert "uncommitted" in output["timeoutMessage"]
        data = json.loads((executor._phase_dir / "step2-output.json").read_text())
        assert data["step"] == 2

    def test_timeout_message_is_fed_back_to_the_retry(self, executor):
        step = {"step": 2, "name": "ui", "timeout_s": 90}
        boom = subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=90)
        preambles = []

        def fake_invoke(s, preamble):
            preambles.append(preamble)
            with patch("subprocess.run", side_effect=boom):
                return ex.StepExecutor._invoke_codex(executor, s, preamble)

        with patch.object(executor, "_invoke_codex", side_effect=fake_invoke), \
             patch.object(executor, "_commit_step"), \
             patch.object(executor, "_update_top_index"):
            with pytest.raises(SystemExit) as exc_info:
                executor._execute_single_step(step)

        assert exc_info.value.code == 1
        assert len(preambles) == executor.MAX_RETRIES
        assert "90" in preambles[1]
        assert "Previous attempt failed" in preambles[1]

    def test_timeout_ends_as_error_with_failed_at(self, executor):
        step = {"step": 2, "name": "ui", "timeout_s": 90}
        boom = subprocess.TimeoutExpired(cmd=["codex", "exec"], timeout=90)

        with patch("subprocess.run", side_effect=boom), \
             patch.object(executor, "_commit_step"), \
             patch.object(executor, "_update_top_index"):
            with pytest.raises(SystemExit) as exc_info:
                executor._execute_single_step(step)

        assert exc_info.value.code == 1
        index = json.loads(executor._index_file.read_text())
        s2 = next(s for s in index["steps"] if s["step"] == 2)
        assert s2["status"] == "error"
        assert "failed_at" in s2
        assert "90" in s2["error_message"]
