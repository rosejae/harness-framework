#!/usr/bin/env python3
"""
Harness Step Executor — runs the steps of a phase in order and self-corrects.

Usage:
    python3 scripts/execute.py <phase-dir> [--push]   # run in order (--push: push when done)
    python3 scripts/execute.py <phase-dir> --status    # read-only progress snapshot, then exit (runs nothing)
    python3 scripts/execute.py <phase-dir> --summary [step|N]   # the full summary of a completed step (not truncated)
"""

import argparse
import contextlib
import json
import os
import subprocess
import sys
import threading
import time
import types
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent


@contextlib.contextmanager
def progress_indicator(label: str):
    """Terminal progress indicator. Used as a with statement; read elapsed time from .elapsed."""
    frames = "◐◓◑◒"
    stop = threading.Event()
    t0 = time.monotonic()

    def _animate():
        idx = 0
        while not stop.wait(0.12):
            sec = int(time.monotonic() - t0)
            sys.stderr.write(f"\r{frames[idx % len(frames)]} {label} [{sec}s]")
            sys.stderr.flush()
            idx += 1
        sys.stderr.write("\r" + " " * (len(label) + 20) + "\r")
        sys.stderr.flush()

    th = threading.Thread(target=_animate, daemon=True)
    th.start()
    info = types.SimpleNamespace(elapsed=0.0)
    try:
        yield info
    finally:
        stop.set()
        th.join()
        info.elapsed = time.monotonic() - t0


class StepExecutor:
    """The harness that runs the steps in a phase directory in order."""

    MAX_RETRIES = 3
    FEAT_MSG = "feat({phase}): step {num} — {name}"
    CHORE_MSG = "chore({phase}): step {num} output"
    TIMEOUT_MSG = (
        "The previous attempt was killed after it ran past its {seconds}s time limit, so it "
        "never reported a status. Finish inside the limit this time: run `git status` / `git diff` "
        "first — the killed session may have left finished work uncommitted in the working tree — "
        "then do the smallest thing that still satisfies the AC."
    )
    TZ = timezone(timedelta(hours=9))

    def __init__(self, phase_dir_name: str, *, auto_push: bool = False):
        self._root = str(ROOT)
        self._phases_dir = ROOT / "phases"
        self._phase_dir = self._phases_dir / phase_dir_name
        self._phase_dir_name = phase_dir_name
        self._top_index_file = self._phases_dir / "index.json"
        self._auto_push = auto_push

        if not self._phase_dir.is_dir():
            print(f"ERROR: {self._phase_dir} not found")
            sys.exit(1)

        self._index_file = self._phase_dir / "index.json"
        if not self._index_file.exists():
            print(f"ERROR: {self._index_file} not found")
            sys.exit(1)

        idx = self._read_json(self._index_file)
        self._project = idx.get("project", "project")
        self._phase_name = idx.get("phase", phase_dir_name)
        self._total = len(idx["steps"])

    def run(self):
        self._print_header()
        self._check_blockers()
        self._checkout_branch()
        self._ensure_created_at()
        self._execute_all_steps()
        self._finalize()

    # --- status (read-only) ---

    def status(self):
        """Print a progress snapshot without running anything: whether a
        background run is still alive, the per-step status table, branch
        commits, and a roll-up. Replaces the hand-rolled "read index.json +
        tail output + git log + pgrep" report that was retyped every few
        minutes while a background run() was in flight — the liveness line is
        here so that last piece stops being bolted on by hand."""
        idx = self._read_json(self._index_file)
        steps = idx.get("steps", [])

        top_status = "?"
        if self._top_index_file.exists():
            for ph in self._read_json(self._top_index_file).get("phases", []):
                if ph.get("dir") == self._phase_dir_name:
                    top_status = ph.get("status", "?")
                    break

        print(f"Phase: {self._phase_name}  (project: {self._project})  top-status: {top_status}")

        pids = self._live_run_pids(self._pgrep_execute(), self._phase_dir_name, os.getpid())
        if pids:
            print(f"Run: running — pid {', '.join(str(p) for p in pids)}")
        else:
            print("Run: not running (no background execute.py for this phase)")

        branch = f"feat-{self._phase_name}"
        if self._run_git("rev-parse", "--verify", branch).returncode == 0:
            log = self._run_git("log", "--oneline", f"main..{branch}")
            commits = [ln for ln in log.stdout.splitlines() if ln.strip()]
            print(f"Branch {branch}: {len(commits)} commit(s) ahead of main")
            for ln in commits:
                print(f"  {ln}")
        else:
            print(f"Branch {branch}: not created yet")

        print("\nSteps:")
        counts = {}
        for s in steps:
            st = s.get("status", "pending")
            counts[st] = counts.get(st, 0) + 1
            note = ""
            if st == "in_progress" and s.get("started_at"):
                note = f"(running {self._fmt_elapsed(s['started_at'])})"
            elif st == "completed" and s.get("summary"):
                note = s["summary"].splitlines()[0][:60]
            elif st == "error" and s.get("error_message"):
                note = "ERR: " + s["error_message"].splitlines()[0][:54]
            elif st == "blocked" and s.get("blocked_reason"):
                note = "BLOCKED: " + s["blocked_reason"].splitlines()[0][:50]
            print(f"  {s.get('step', '?'):>2}  {s.get('name', ''):<22} {st:<12} {note}")

        roll = ", ".join(f"{n} {st}" for st, n in sorted(counts.items()))
        print(f"\nSummary: {len(steps)} steps — {roll}")

    @staticmethod
    def _pgrep_execute() -> list:
        """`pgrep -af execute.py` output as lines ([] if pgrep is absent/finds nothing)."""
        try:
            r = subprocess.run(["pgrep", "-af", "execute.py"],
                               capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return []
        return r.stdout.splitlines()

    @staticmethod
    def _live_run_pids(lines, phase_dir_name: str, own_pid: int) -> list:
        """Pids of real background runs of this phase, from `pgrep -af` lines.

        Excludes our own process and any `--status` invocation — otherwise this
        very call would report itself as a live run.
        """
        pids = []
        for line in lines:
            parts = line.split()
            if len(parts) < 2 or not parts[0].isdigit():
                continue
            pid, argv = int(parts[0]), parts[1:]
            if pid == own_pid or "--status" in argv:
                continue
            # A python run of the script, not an editor/grep that merely names it:
            # either `python3 …/execute.py …` or a shebang `./scripts/execute.py …`.
            exe = os.path.basename(argv[0])
            direct = exe.endswith("execute.py")
            via_python = exe.startswith("python") and any(
                a.endswith("execute.py") for a in argv[1:])
            if not (direct or via_python):
                continue
            if phase_dir_name in argv:
                pids.append(pid)
        return pids

    def _fmt_elapsed(self, started_at: str) -> str:
        try:
            start = datetime.fromisoformat(started_at)
            secs = int((datetime.now(self.TZ) - start).total_seconds())
        except Exception:
            return "?"
        if secs < 60:
            return f"{secs}s"
        if secs < 3600:
            return f"{secs // 60}m"
        return f"{secs // 3600}h{(secs % 3600) // 60}m"

    # --- summary (read-only) ---

    def summary(self, selector: Optional[str] = None):
        """Print step summaries in full. The --status table cuts each summary to
        its first 60 characters, so reading what a step actually produced meant
        parsing index.json by hand — this is the path that prints the whole
        text. Without a selector: every completed step; with one (step number or
        step name): just that step."""
        idx = self._read_json(self._index_file)
        steps = idx.get("steps", [])

        if selector:
            step = self._find_step(steps, selector)
            if step is None:
                known = ", ".join(f"{s.get('step', '?')} {s.get('name', '')}" for s in steps)
                print(f"ERROR: step '{selector}' not found in this phase.")
                print(f"  Hint: give a step number or name — {known}")
                sys.exit(1)
            targets = [step]
        else:
            targets = [s for s in steps if s.get("status") == "completed"]

        print(f"Phase: {self._phase_name}  (project: {self._project})")
        print(f"\nSummaries: {len(targets)} of {len(steps)} steps\n")
        for s in targets:
            print(f"  {s.get('step', '?'):>2}  {s.get('name', '')}  {s.get('status', 'pending')}")
            body = s.get("summary")
            for line in body.splitlines() if body else ["(no summary)"]:
                print(f"      {line}")
            print()

    @staticmethod
    def _find_step(steps, selector: str):
        """Step matched by its `step` number or its name (None if neither hits)."""
        sel = selector.strip()
        for s in steps:
            if str(s.get("step")) == sel or s.get("name") == sel:
                return s
        return None

    # --- timestamps ---

    def _stamp(self) -> str:
        return datetime.now(self.TZ).strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- JSON I/O ---

    @staticmethod
    def _read_json(p: Path) -> dict:
        return json.loads(p.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json(p: Path, data: dict):
        p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    # --- git ---

    def _run_git(self, *args) -> subprocess.CompletedProcess:
        cmd = ["git"] + list(args)
        return subprocess.run(cmd, cwd=self._root, capture_output=True, text=True)

    def _checkout_branch(self):
        branch = f"feat-{self._phase_name}"

        r = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if r.returncode != 0:
            print("  ERROR: git is unavailable, or this is not a git repo.")
            print(f"  {r.stderr.strip()}")
            sys.exit(1)

        if r.stdout.strip() == branch:
            return

        r = self._run_git("rev-parse", "--verify", branch)
        r = self._run_git("checkout", branch) if r.returncode == 0 else self._run_git("checkout", "-b", branch)

        if r.returncode != 0:
            print(f"  ERROR: checkout of branch '{branch}' failed.")
            print(f"  {r.stderr.strip()}")
            print("  Hint: stash or commit your changes, then try again.")
            sys.exit(1)

        print(f"  Branch: {branch}")

    def _commit_step(self, step_num: int, step_name: str, *, failed: bool = False):
        output_rel = f"phases/{self._phase_dir_name}/step{step_num}-output.json"
        index_rel = f"phases/{self._phase_dir_name}/index.json"

        if failed:
            # Retry exhaustion commits the failure record and nothing else. A `feat(...)` commit
            # of code that never reached `completed` read in the log like a step that landed (and
            # a timeout-killed attempt never committed that code itself); it stays in the working
            # tree for the operator, next to the error the index now carries.
            rels = [rel for rel in (index_rel, output_rel) if (Path(self._root) / rel).exists()]
            self._run_git("add", "--", *rels)
            if self._run_git("diff", "--cached", "--quiet", "--", *rels).returncode != 0:
                msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num)
                r = self._run_git("commit", "-m", msg, "--", *rels)
                if r.returncode != 0:
                    print(f"  WARN: housekeeping commit failed: {r.stderr.strip()}")
            return

        self._run_git("add", "-A")
        self._run_git("reset", "HEAD", "--", output_rel)
        self._run_git("reset", "HEAD", "--", index_rel)

        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.FEAT_MSG.format(phase=self._phase_name, num=step_num, name=step_name)
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  Commit: {msg}")
            else:
                print(f"  WARN: code commit failed: {r.stderr.strip()}")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = self.CHORE_MSG.format(phase=self._phase_name, num=step_num)
            r = self._run_git("commit", "-m", msg)
            if r.returncode != 0:
                print(f"  WARN: housekeeping commit failed: {r.stderr.strip()}")

    # --- top-level index ---

    def _update_top_index(self, status: str):
        if not self._top_index_file.exists():
            return
        top = self._read_json(self._top_index_file)
        ts = self._stamp()
        for phase in top.get("phases", []):
            if phase.get("dir") == self._phase_dir_name:
                phase["status"] = status
                ts_key = {"completed": "completed_at", "error": "failed_at", "blocked": "blocked_at"}.get(status)
                if ts_key:
                    phase[ts_key] = ts
                break
        self._write_json(self._top_index_file, top)

    # --- context ---

    @staticmethod
    def _build_step_context(index: dict) -> str:
        lines = [
            f"- Step {s['step']} ({s['name']}): {s['summary']}"
            for s in index["steps"]
            if s["status"] == "completed" and s.get("summary")
        ]
        if not lines:
            return ""
        return "## Previous Step Output\n\n" + "\n".join(lines) + "\n\n"

    def _build_preamble(self, step_context: str, prev_error: Optional[str] = None) -> str:
        commit_example = self.FEAT_MSG.format(
            phase=self._phase_name, num="N", name="<step-name>"
        )
        retry_section = ""
        if prev_error:
            retry_section = (
                f"\n## ⚠ Previous attempt failed — read the error below and fix it\n\n"
                f"{prev_error}\n\n---\n\n"
            )
        return (
            f"You are a developer on the {self._project} project. Carry out the step below.\n\n"
            f"{step_context}{retry_section}"
            f"## Working Rules\n\n"
            f"1. Review the code written in earlier steps and stay consistent with it.\n"
            f"2. Do only the work this step specifies. Do not add extra features or files.\n"
            f"3. Do not break existing tests.\n"
            f"4. Run the AC (Acceptance Criteria) verification yourself.\n"
            f"5. Update this step's status in /phases/{self._phase_dir_name}/index.json:\n"
            f"   - AC passes → \"completed\" + a one-line summary of this step's output in \"summary\"\n"
            f"   - still failing after {self.MAX_RETRIES} fix attempts → \"error\" + record \"error_message\"\n"
            f"   - user intervention needed (API key, auth, manual setup) → \"blocked\" + record \"blocked_reason\", then stop immediately\n"
            f"6. Commit every change:\n"
            f"   {commit_example}\n\n---\n\n"
        )

    # --- Codex invocation ---

    def _invoke_codex(self, step: dict, preamble: str) -> dict:
        step_num, step_name = step["step"], step["name"]
        step_file = self._phase_dir / f"step{step_num}.md"

        if not step_file.exists():
            print(f"  ERROR: {step_file} not found")
            sys.exit(1)

        prompt = preamble + step_file.read_text()
        # --dangerously-bypass-hook-trust: this is a non-interactive run, so the /hooks trust review
        # cannot happen; bypass trust so the version-controlled in-repo .codex/hooks.json guardrails actually run.
        timeout_message = None
        try:
            result = subprocess.run(
                ["codex", "exec",
                 "--dangerously-bypass-approvals-and-sandbox",
                 "--dangerously-bypass-hook-trust",
                 "--json", prompt],
                cwd=self._root, capture_output=True, text=True,
                # stdin=DEVNULL: capture_output redirects stdout/stderr only, and `codex exec` reads an
                # inherited non-TTY stdin as additional prompt input — a run without a TTY (CI, a pipe,
                # an agent shell) would then block until timeout_s and produce nothing.
                stdin=subprocess.DEVNULL,
                timeout=step.get("timeout_s", 1800),
            )
        except subprocess.TimeoutExpired as exc:
            # The session was killed mid-flight and never wrote a status, so hand the retry loop an
            # ordinary step failure — an escaping exception would kill the whole phase run instead.
            timeout_message = self.TIMEOUT_MSG.format(seconds=int(exc.timeout))
            result = subprocess.CompletedProcess(exc.cmd, -1, stdout="", stderr=timeout_message)

        if result.returncode != 0:
            print(f"\n  WARN: Codex exited abnormally (code {result.returncode})")
            if result.stderr:
                print(f"  stderr: {result.stderr[:500]}")

        output = {
            "step": step_num, "name": step_name,
            "exitCode": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr,
        }
        if timeout_message:
            output["timeoutMessage"] = timeout_message
        out_path = self._phase_dir / f"step{step_num}-output.json"
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        return output

    # --- header & verification ---

    def _print_header(self):
        print(f"\n{'='*60}")
        print("  Harness Step Executor")
        print(f"  Phase: {self._phase_name} | Steps: {self._total}")
        if self._auto_push:
            print("  Auto-push: enabled")
        print(f"{'='*60}")

    def _check_blockers(self):
        """Every step is read. A scan from the end that stopped at the first non-pending step let a
        trailing `completed` hide an earlier error — a step reopened to `pending` after later ones
        completed, then failed again — and `_finalize` marked the phase completed over it."""
        index = self._read_json(self._index_file)
        for s in index["steps"]:
            if s["status"] == "error":
                print(f"\n  ✗ Step {s['step']} ({s['name']}) failed.")
                print(f"  Error: {s.get('error_message', 'unknown')}")
                print(f"  Fix and reset status to 'pending' to retry.")
                sys.exit(1)
            if s["status"] == "blocked":
                print(f"\n  ⏸ Step {s['step']} ({s['name']}) blocked.")
                print(f"  Reason: {s.get('blocked_reason', 'unknown')}")
                print(f"  Resolve and reset status to 'pending' to retry.")
                sys.exit(2)
            if s["status"] not in ("pending", "completed"):
                print(f"\n  ✗ Step {s['step']} ({s['name']}) has status '{s['status']}', which this executor never writes.")
                print("  Reset it to 'pending' to retry, or to 'completed' if it is done.")
                sys.exit(1)

    def _ensure_created_at(self):
        index = self._read_json(self._index_file)
        if "created_at" not in index:
            index["created_at"] = self._stamp()
            self._write_json(self._index_file, index)

    # --- execution loop ---

    def _execute_single_step(self, step: dict) -> bool:
        """Run a single step (retries included). True when completed, False on failure or block."""
        step_num, step_name = step["step"], step["name"]
        done = sum(1 for s in self._read_json(self._index_file)["steps"] if s["status"] == "completed")
        prev_error = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            index = self._read_json(self._index_file)
            step_context = self._build_step_context(index)
            preamble = self._build_preamble(step_context, prev_error)

            tag = f"Step {step_num}/{self._total - 1} ({done} done): {step_name}"
            if attempt > 1:
                tag += f" [retry {attempt}/{self.MAX_RETRIES}]"

            with progress_indicator(tag) as pi:
                codex_output = self._invoke_codex(step, preamble)
            elapsed = int(pi.elapsed)  # set when the context exits — inside it, still 0.0

            index = self._read_json(self._index_file)
            status = next((s.get("status", "pending") for s in index["steps"] if s["step"] == step_num), "pending")
            ts = self._stamp()

            if status == "completed":
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["completed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name)
                print(f"  ✓ Step {step_num}: {step_name} [{elapsed}s]")
                return True

            if status == "blocked":
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["blocked_at"] = ts
                self._write_json(self._index_file, index)
                reason = next((s.get("blocked_reason", "") for s in index["steps"] if s["step"] == step_num), "")
                print(f"  ⏸ Step {step_num}: {step_name} blocked [{elapsed}s]")
                print(f"    Reason: {reason}")
                self._update_top_index("blocked")
                sys.exit(2)

            err_msg = codex_output.get("timeoutMessage") or next(
                (s.get("error_message", "Step did not update status") for s in index["steps"] if s["step"] == step_num),
                "Step did not update status",
            )

            if attempt < self.MAX_RETRIES:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "pending"
                        s.pop("error_message", None)
                self._write_json(self._index_file, index)
                prev_error = err_msg
                print(f"  ↻ Step {step_num}: retry {attempt}/{self.MAX_RETRIES} — {err_msg}")
            else:
                for s in index["steps"]:
                    if s["step"] == step_num:
                        s["status"] = "error"
                        s["error_message"] = f"[failed after {self.MAX_RETRIES} attempts] {err_msg}"
                        s["failed_at"] = ts
                self._write_json(self._index_file, index)
                self._commit_step(step_num, step_name, failed=True)
                print(f"  ✗ Step {step_num}: {step_name} failed after {self.MAX_RETRIES} attempts [{elapsed}s]")
                print(f"    Error: {err_msg}")
                self._update_top_index("error")
                sys.exit(1)

    def _execute_all_steps(self):
        while True:
            index = self._read_json(self._index_file)
            pending = next((s for s in index["steps"] if s["status"] == "pending"), None)
            if pending is None:
                print("\n  All steps completed!")
                return

            step_num = pending["step"]
            for s in index["steps"]:
                if s["step"] == step_num and "started_at" not in s:
                    s["started_at"] = self._stamp()
                    self._write_json(self._index_file, index)
                    break

            self._execute_single_step(pending)

    def _finalize(self):
        index = self._read_json(self._index_file)
        index["completed_at"] = self._stamp()
        self._write_json(self._index_file, index)
        self._update_top_index("completed")

        self._run_git("add", "-A")
        if self._run_git("diff", "--cached", "--quiet").returncode != 0:
            msg = f"chore({self._phase_name}): mark phase completed"
            r = self._run_git("commit", "-m", msg)
            if r.returncode == 0:
                print(f"  ✓ {msg}")

        if self._auto_push:
            branch = f"feat-{self._phase_name}"
            r = self._run_git("push", "-u", "origin", branch)
            if r.returncode != 0:
                print(f"\n  ERROR: git push failed: {r.stderr.strip()}")
                sys.exit(1)
            print(f"  ✓ Pushed to origin/{branch}")

        print(f"\n{'='*60}")
        print(f"  Phase '{self._phase_name}' completed!")
        print(f"{'='*60}")


def main():
    parser = argparse.ArgumentParser(description="Harness Step Executor")
    parser.add_argument("phase_dir", help="Phase directory name (e.g. 0-mvp)")
    parser.add_argument("--push", action="store_true", help="Push branch after completion")
    parser.add_argument("--status", action="store_true",
                        help="Print a read-only progress snapshot and exit (no execution)")
    parser.add_argument("--summary", nargs="?", const="", metavar="STEP",
                        help="Print full step summaries and exit (no execution). "
                             "Optionally a step number or name; default: all completed steps")
    args = parser.parse_args()

    executor = StepExecutor(args.phase_dir, auto_push=args.push)
    if args.status:
        executor.status()
    elif args.summary is not None:
        executor.summary(args.summary)
    else:
        executor.run()


if __name__ == "__main__":
    main()
