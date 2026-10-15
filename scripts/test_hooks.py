"""scripts/test_hooks.py — the hooks must actually block, and the rules hook must stay harmless.

Hooks are the one piece of the harness that fails *silently* when it is wrong: a guard that
lets everything through looks exactly like a guard that had nothing to catch. The Claude-side
inline hooks this file replaced did exactly that — they read an environment variable that does
not exist and "blocked" with `exit 1`, so nothing was ever blocked.

So the contract is pinned here, from the outside: feed the hook the stdin JSON the harness
feeds it and assert on what it writes to stdout.

  - PreToolUse blocks with {"hookSpecificOutput": {"permissionDecision": "deny"}}
  - Stop blocks with {"decision": "block"}
  - a non-zero exit is NOT a block (it is a non-blocking error) — never assert on it

The no-jq fixture matters as much as the corpus: a hook that reads its input only through jq
is one PATH away from being a no-op. Every blocking hook must still block when jq is gone.
"""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
BLOCK_HOOK = ROOT / "scripts" / "hooks" / "block-dangerous.sh"
POLL_HOOK = ROOT / "scripts" / "hooks" / "block-poll-loop.sh"
VALIDATE_HOOK = ROOT / "scripts" / "hooks" / "validate.sh"
TDD_HOOKS = [
    ROOT / "scripts" / "hooks" / "tdd-guard.sh",
    ROOT / ".codex" / "hooks" / "tdd-guard.sh",
]
RULES_HOOK = ROOT / ".codex" / "hooks" / "rules-context.py"
RULES_MATCH = ROOT / "scripts" / "rules_match.py"
CLAUDE_SETTINGS = ROOT / ".claude" / "settings.json"
CODEX_HOOKS = ROOT / ".codex" / "hooks.json"


def run_hook(hook, payload, env=None, cwd=ROOT):
    return subprocess.run(
        [str(hook)],
        input=json.dumps(payload),
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def run_hook_bounded(hook, payload, seconds):
    """run_hook with a deadline that fails the test instead of hanging it. The hook runs in a
    session of its own and the whole group is killed at the deadline: killing the hook alone
    leaves its `$(…)` subshells running as orphans, still burning the CPU after the test."""
    with subprocess.Popen([str(hook)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=True, start_new_session=True) as proc:
        try:
            out, err = proc.communicate(json.dumps(payload), timeout=seconds)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, 9)
            proc.communicate()
            pytest.fail(f"{hook.name} took over {seconds} s")
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def bash_payload(command):
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def edit_payload(file_path):
    return {"tool_name": "Write", "tool_input": {"file_path": file_path}}


def decision(proc):
    """The PreToolUse decision the harness would act on, or None.

    Parsed, not substring-matched: stdout must be one JSON object with the decision under
    hookSpecificOutput for a PreToolUse event. Truncated or mis-nested output is a non-blocking
    error to the harness — the call goes through — so it must not count as a block here either.
    """
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None
    spec = out.get("hookSpecificOutput") if isinstance(out, dict) else None
    if not isinstance(spec, dict) or spec.get("hookEventName") != "PreToolUse":
        return None
    return spec.get("permissionDecision")


def denied(proc):
    """PreToolUse block. The exit code is deliberately not part of this."""
    return decision(proc) == "deny"


@pytest.mark.parametrize("stdout", [
    '{"permissionDecision":"deny"',                    # truncated: not JSON at all
    '{"permissionDecision": "deny"}',                  # not under hookSpecificOutput
    '{"hookSpecificOutput": {"hookEventName": "Stop", "permissionDecision": "deny"}}',
    "",
], ids=["truncated", "wrong-nesting", "wrong-event", "empty"])
def test_denied_counts_only_output_the_harness_can_act_on(stdout):
    assert not denied(SimpleNamespace(stdout=stdout))


def test_denied_accepts_the_real_shape():
    out = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny"}}
    assert denied(SimpleNamespace(stdout=json.dumps(out, indent=2)))


@pytest.fixture
def no_jq_env(tmp_path):
    """A PATH with the hook's coreutils but no jq. A hook that goes quiet here is a no-op hook."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("cat", "grep", "sed"):
        real = shutil.which(tool)
        assert real, f"{tool} must exist to build the no-jq PATH"
        (bindir / tool).symlink_to(real)
    assert shutil.which("jq", path=str(bindir)) is None
    return {"PATH": str(bindir), "HOME": str(tmp_path)}


# --- block-dangerous.sh (PreToolUse[Bash], shared by Claude and Codex) ----
# The corpus is the specification. The BLOCKED entries include every form the old
# literal-adjacent regex (`rm -rf`, `git push --force`, `git reset --hard`, `DROP TABLE`)
# caught, plus the spellings it let through.

BLOCKED_COMMANDS = [
    # what the old regex caught
    "rm -rf x",
    "git push --force",
    "git reset --hard",
    "DROP TABLE t",
    # what it let through
    "rm -fr x",
    "rm -r -f x",
    "rm -Rf x",
    "sudo rm -rf /",
    "git push -f",
    "git push origin main --force",
    "git push --force-with-lease",
    "git -C . reset --hard",
    "git reset --hard HEAD~2",
    # The shell strips quotes before it runs a word, and GNU rm / git take an option after an
    # operand or another option — each of these is the call above in another spelling.
    '"rm" -rf x',
    "'rm' -rf x",
    "rm x -rf",
    "rm --recursive x",
    "rm -r --force x",
    "rm --force -R x",
    "rm \\\n  -rf x",                    # a line continuation is still one command
    "git push -u origin main --force",
    "git push -u -f origin main",
    "git reset -q --hard",
    # A short-flag cluster holding f forces as much as a lone -f does.
    "git push -uf origin main",
    "git push -fu origin main",
    # A plus-refspec forces that one ref — the same overwrite as --force.
    "git push origin +main",
    "git push origin +HEAD:main",
    "git push origin '+main'",
    # git takes any unambiguous prefix of a long option: all three resolve to --hard.
    "git reset --h",
    "git reset --ha HEAD~1",
    "git reset --har",
    # A push that deletes or overwrites remote refs is held to the force push's line.
    "git push --mirror origin",
    "git push --m origin",
    "git push --delete origin feat-x",
    "git push origin --del feat-x",
    "git push -d origin feat-x",
    "git push -ud origin feat-x",
    "git push --prune origin",
    "git -C . push --pru origin",
    "git push origin :feat-x",                # an empty source deletes the destination
    "git push origin ':refs/tags/v1'",
    # SQL keywords are case-insensitive
    "sqlite3 database.db 'drop table t;'",
    "sqlite3 database.db 'Drop Table t;'",
]

ALLOWED_COMMANDS = [
    'rm -f "$PID_FILE"',
    "rm out/x/report.json",
    "rm -i f",
    "git push -u origin feat",
    "git push --follow-tags",
    "git reset --soft",
    "git reset --help",             # the --hard prefixes stop at a word boundary
    "git push origin feat-fix",     # an f inside a ref name is not a flag
    "git merge --ff-only",
    "git branch -d b",
    "git log --oneline -5",
    "npm run build",
    "confirm -rf now",              # left word boundary: not the `rm` command
    # A long option that merely contains an r is not a recursive flag.
    "rm --force stale.pid",
    "rm --verbose out/x/report.json",
    "rm --interactive=never stale.pid",
    # One simple command at a time: a flag that belongs to the NEXT command is not this one's.
    "rm out/x/report.json && ls -R out",
    "git push origin feat && ls -f",
    "docker run --rm img pip install -r requirements.txt",   # --rm is an option, not rm
    "echo backdrop table",          # left word boundary for the case-insensitive DROP TABLE
    "git push origin HEAD:refs/heads/feat-x",   # a plain src:dst refspec
    "git push origin :",            # a lone `:` pushes the matching branches, deletes nothing
    "git push --dry-run origin feat-x",         # --d is ambiguous in git; --dr is not --delete
]


@pytest.mark.parametrize("command", BLOCKED_COMMANDS)
def test_dangerous_commands_are_denied(command):
    proc = run_hook(BLOCK_HOOK, bash_payload(command))
    assert denied(proc), f"not blocked: {command!r}\nstdout={proc.stdout!r}"


@pytest.mark.parametrize("command", ALLOWED_COMMANDS)
def test_ordinary_commands_are_allowed(command):
    proc = run_hook(BLOCK_HOOK, bash_payload(command))
    assert not denied(proc), f"false positive: {command!r}\nstdout={proc.stdout!r}"
    assert proc.stdout.strip() == "", proc.stdout


def test_a_codex_shaped_payload_is_denied_the_same_way():
    """Codex sends more fields than Claude (session_id, cwd, hook_event_name), but carries the
    command in the same tool_input.command and takes the same deny JSON — hence one file."""
    payload = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "cwd": str(ROOT),
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /"},
    }
    assert denied(run_hook(BLOCK_HOOK, payload))


def test_block_hook_never_exits_nonzero():
    """A non-zero exit is a non-blocking error in both harnesses — it would look like the
    hook is broken while the deny JSON is what actually blocks."""
    for command in ("rm -rf x", "git status"):
        assert run_hook(BLOCK_HOOK, bash_payload(command)).returncode == 0


@pytest.mark.parametrize("command", [
    "rm -rf x", "git push --force", "git reset --hard",
    # The raw JSON is decoded before the scan: unread, a closing `"` left `-f` with no right
    # boundary, and an encoded newline put an `n` in front of `rm`.
    "git push -f", "true\nrm -rf x",
])
def test_still_blocks_without_jq(command, no_jq_env):
    """Without jq the command extraction yields nothing. Failing open there would mean the
    guard silently stops guarding the moment PATH changes."""
    proc = run_hook(BLOCK_HOOK, bash_payload(command), env=no_jq_env)
    assert denied(proc), f"jq-less run let {command!r} through\nstderr={proc.stderr!r}"


def test_without_jq_safe_commands_still_run(no_jq_env):
    """The fallback must not be a blanket deny: that would block `command -v jq` and every
    other repair command, leaving only a human able to get out of it."""
    for command in ("command -v jq", "npm run test", "ls out"):
        proc = run_hook(BLOCK_HOOK, bash_payload(command), env=no_jq_env)
        assert not denied(proc), f"jq-less run blocked {command!r}"


def test_a_lone_cat_heredoc_is_text_not_a_command():
    command = "cat -n > notes.md <<'EOF'\nnever run rm -rf / by hand\nEOF"
    assert not denied(run_hook(BLOCK_HOOK, bash_payload(command)))


# --- block-poll-loop.sh ---------------------------------------------------
# Polling is waste, not destruction, so it gets its own hook and its own reason text
# (background work already notifies on completion — /harness §E). A long
# `sleep` and a one-line `until`/`while … sleep` loop are the two spellings that leak
# zombie processes and context; a short sleep between retries stays legal.
#
# The three shapes below the first two lines were measured leaking through: a unit
# suffix carries a long wait in a small number, a `while pgrep` watch spins with no
# `sleep` to catch at all, and a loop written across several lines hides the keyword
# and the delay from a line-by-line grep. Each one is a real command out of the
# reference harness's transcripts, not an invented spelling.

BLOCKED_POLL_COMMANDS = [
    "sleep 60",
    "until false; do sleep 5; done",
    "while true; do sleep 10; done",
    # ① unit suffix: two minutes is four times over the line, written in one digit
    "sleep 2m",
    "sleep 1h",
    # ② a process watch is the loop condition — there is no delay call to match on
    'while pgrep -f run_tests.py >/dev/null; do :; done',
    'until ! pgrep -f nest_sweep >/dev/null 2>&1; do :; done',
    # ③ the keyword and the delay live on different lines of one command string
    'cd /srv/repo\nwhile true; do\n  echo waiting\n  sleep 20\ndone',
    # ④ the prose exemption below must never become the way through. Each of these carries
    #    an exempt carrier AND a live loop: only the carrier's own span is dropped, so the
    #    loop beside it — or the one handed to an owner that RUNS its argument — still lands.
    "git commit -m 'docs: ban polling' && until [ -f out/report.json ]; do sleep 20; done",
    "grep -c 'poll' scripts/hooks/ ; bash -c 'while true; do sleep 60; done'",
    "git commit -F - <<'MSG'\ndocs: the guard denies `while pgrep …` now\nMSG\nuntil pgrep -f build_layout; do sleep 30; done",
    # a heredoc into a FILE is deliberately not exempt: the file can be run by a later call
    "cat > /tmp/poll.sh <<'EOF'\nwhile true; do sleep 60; done\nEOF",
    # a double-quoted span with a substitution in it is not inert text — it executes
    'git commit -m "$(until true; do sleep 60; done)"',
    # quote removal: the shell strips quotes before it runs a word
    'sleep "60"',
    "sleep '60'",
    'until [ -f out/report.json ]; do "sleep" 1; done',
    # ⑥'s quoted heredoc is inert only at its own shell level: inside a double quote that is
    #   still open, the OUTER shell expands the body's substitution first
    "bash -c \" grep <<'MSG'\n$(sleep 60)\nMSG\"",
    "bash -c \" grep <<'MSG'\n$(sleep 60)\nMSG\n\"",
    "echo \" grep <<'MSG'\n$(sleep 60)\nMSG\n\"",
    # ⑦ an empty body busy-waits on its condition — a file watch spins like a process watch
    "until [ -f out/report.json ]; do :; done",
    "while [ ! -f out/report.json ]; do true; done",
    "until test -s out/x/report.json; do :; done; cat out/x/report.json",
    "until [ -f out/report.json ]\ndo\n  :\ndone",
    # ⑦'s read-drain exception covers only a `while read` that consumes its input. Each of these
    # spins at EOF instead — or carries a delay, which ③ judges on the loop as written.
    "until read -r x; do :; done",
    "while ! read -r x; do :; done",
    "while read -r x < out/list.txt; do :; done",      # the read reopens the file every pass
    "while read -r _; do sleep 5; done",
    "ls | while read -r _; do :; done; until [ -f out/report.json ]; do :; done",
    "ls | while read -r _\ndo\n  :\ndone\nuntil [ -f out/report.json ]\ndo\n  :\ndone",
    # rg's --pre runs its argument, so rg owns no inert text once it carries one
    "rg --pre 'sh -c \"while true; do sleep 60; done\"' x f",
    "rg -n x f --pre='sh -c \"while true; do sleep 60; done\"'",
    # a heredoc into `cat` is text only while nothing can run what cat prints
    "cat <<'EOF' | bash\nwhile true; do sleep 60; done\nEOF",
    "bash -c \"$(cat <<'EOF'\nwhile true; do sleep 60; done\nEOF\n)\"",
    "{ cat <<'EOF'\nwhile true; do sleep 60; done\nEOF\n} | bash",
    "cat <<'EOF' \\\n| bash\nwhile true; do sleep 60; done\nEOF",
    "cat -v>/tmp/p.sh <<'EOF'\nwhile true; do sleep 60; done\nEOF",   # cat's flags are option letters
    "cat -n;bash <<'EOF'\nwhile true; do sleep 60; done\nEOF",       # …or bash owns the heredoc
    # an opener line continued by `\` is still the command: the next line runs, the body follows
    "git commit -F - <<'MSG' \\\n&& bash -c 'while true; do sleep 60; done'\nfix: x\nMSG",
    "git commit -F - <<'MSG' \\\n&& until [ -f out/report.json ]; do :; done\nfix: x\nMSG",
    # grep/rg print what they match, so a pipe after them can hand the body to a shell
    "grep -e. <<'EOF' | bash\nwhile true; do sleep 60; done\nEOF",
    "{ grep -e. <<'EOF'\nwhile true; do sleep 60; done\nEOF\n} | bash",
    "rg -N . <<'EOF' | sh\nuntil [ -f out/report.json ]; do :; done\nEOF",
    # <<- ends the body at a tab-indented delimiter, before the first exact one
    "cat <<-'EOF'\nx\n\tEOF\nwhile true; do sleep 60; done\nEOF",
    "git commit -F - <<-'MSG'\nx\n\tMSG\nwhile true; do sleep 60; done\nMSG",
]

ALLOWED_POLL_COMMANDS = [
    "sleep 2",                      # a legitimate gap between retries
    "sleep 5s",                     # the same gap with the unit spelled out
    "npm run test",
    # a one-shot settle inside an unrelated command — the shape the guard must not eat
    "kill 12345 2>/dev/null; sleep 2; pgrep -c node",
    # a `while` loop that is not a poll, with an unrelated short sleep after it
    'while read -r f; do echo "$f"; done < list.txt\nsleep 2',
    # Text ABOUT the shapes above is not a poll. The guard matches the raw command string,
    # so describing a shape used to be as blocked as running it — the commit that added
    # the three shapes above was denied for naming them. `git commit` stores its message and
    # `grep` matches its pattern; neither ever runs what it is handed, so those two spans are
    # dropped before the scan. Nothing else is.
    "git commit -F - <<'MSG'\nfix(harness): close the shapes the guard missed\n\n- `while pgrep …` as the loop condition, which spins with no delay call\n- a header written across several lines, hiding `until … sleep 20; done`\nMSG",
    "git commit -m 'docs: explain why while true; do sleep 60; done is banned'",
    'git commit -m "fix(harness): deny sleep 2m and while pgrep watches"',
    "grep -rn 'until .* sleep 20; done' scripts/hooks/",
    'rg "while pgrep" docs/',
    "git commit -m 'fix(harness): deny until [ -f x ]; do :; done busy-waits'",
    "for f in a b; do :; done",     # a loop over a list is not a wait on a condition
    # The read-drain idiom: each pass blocks in `read` and the loop ends at EOF — it consumes
    # its input, it does not spin on a condition.
    "while read -r _; do :; done",
    "npm run build 2>&1 | while IFS= read -r _; do :; done",
    "while read -r -d '' _; do true; done < <(find out -print0)",
    "ls | while read -r _\ndo\n  :\ndone",
    # A lone `cat` of a quoted heredoc only prints it: nothing on the line can run it.
    "cat <<'EOF'\nuntil [ -f out/report.json ]; do :; done\nEOF",
    "cat <<'EOF'\nwhile true; do sleep 60; done\nEOF\n",
    # grep with nothing after it to run what it prints; a commit followed by an unrelated command
    "grep -e. <<'EOF'\nwhile true; do sleep 60; done\nEOF",
    "git commit -F - <<'MSG' && git log --oneline -1\nfix: deny while true; do sleep 60; done\nMSG",
]


@pytest.mark.parametrize("command", BLOCKED_POLL_COMMANDS)
def test_poll_loops_are_denied(command):
    proc = run_hook(POLL_HOOK, bash_payload(command))
    assert denied(proc), f"not blocked: {command!r}\nstdout={proc.stdout!r}"


@pytest.mark.parametrize("command", ALLOWED_POLL_COMMANDS)
def test_short_sleeps_and_ordinary_commands_are_allowed(command):
    proc = run_hook(POLL_HOOK, bash_payload(command))
    assert not denied(proc), f"false positive: {command!r}\nstdout={proc.stdout!r}"


def test_poll_hook_names_the_three_alternatives():
    """The deny is only useful if it says what to do instead — otherwise the next move
    is to retry the same loop with a different spelling."""
    out = run_hook(POLL_HOOK, bash_payload("sleep 60")).stdout
    assert "run_in_background" in out, out
    assert "Monitor" in out, out
    assert "--status" in out, out
    # and the carrier that is exempt, so a denied commit message does not get retried by
    # writing it to a file first
    assert "git commit -F -" in out, out


@pytest.mark.parametrize("command", BLOCKED_POLL_COMMANDS)
def test_poll_hook_still_blocks_without_jq(command, no_jq_env):
    proc = run_hook(POLL_HOOK, bash_payload(command), env=no_jq_env)
    assert denied(proc), f"jq-less run let {command!r} through\nstderr={proc.stderr!r}"


def test_poll_hook_without_jq_still_allows_safe_commands(no_jq_env):
    """Same reason as block-dangerous.sh: a blanket deny would block the repair."""
    for command in ("command -v jq", "sleep 2", "npm run test"):
        proc = run_hook(POLL_HOOK, bash_payload(command), env=no_jq_env)
        assert not denied(proc), f"jq-less run blocked {command!r}"


@pytest.mark.parametrize("command, deny", [
    # a file written through a heredoc, a quote pair on every line
    ("cat > /tmp/x.py <<'EOF'\n" + "x = 'a' + \"b\"\n" * 6300 + "EOF", False),
    # every span dropped, so the text before the next one ends in an ever longer run of blanks
    ("grep -e " + "'a' " * 22000, False),
    # a long commit message: the search for its delimiter line re-matched every prefix
    ("git commit -F - <<'MSG'\n" + "it's \"quoted\": until [ -f x ]; do :; done\n" * 2100 + "MSG", False),
    # more read-drains than ⑦'s capped passes drop: the rest are read as busy-waits
    ("ls | while read -r _; do :; done; " * 2600, True),
], ids=["file-heredoc", "dropped-spans", "commit-heredoc", "drain-loops"])
def test_poll_hook_reads_an_88_kb_command_in_linear_time(command, deny):
    """A hook that runs past its timeout lets the call through, and this one runs on every Bash
    call. Re-reading the whole prefix for each quoted span made ⑥ quadratic (cubic when the
    spans are dropped), and so did the delimiter search and, in a UTF-8 locale, the global
    substitutions: 88 KB took from seconds to far past any hook timeout, by shape. Linear, it is
    about a second on a loaded machine; the bound is far above that, so load does not flake it,
    and far below what the quadratic walks need."""
    assert len(command) > 85_000
    proc = run_hook_bounded(POLL_HOOK, bash_payload(command), seconds=15)
    assert denied(proc) == deny, proc.stdout


# --- tdd-guard.sh (Claude: scripts/hooks, Codex: .codex/hooks) -------------
# The one hook kept as two editions — Codex edits arrive as an apply_patch body, Claude's as a
# file_path — so every shared case runs against both. Each edition takes its root from
# `git -C <its own dir>`, so a case copies it into a throwaway repo holding only the files the
# case lays out: the verdict cannot depend on what this repo happens to contain.

HOOK_ID = lambda p: p.parts[-3]  # noqa: E731 — "scripts" (Claude) / ".codex" (Codex)


def tdd_repo(tmp_path, hook, files=()):
    """A git repo with a copy of `hook` at its real relative path, plus empty `files`.
    Returns (repo, the copied hook)."""
    repo = tmp_path / "repo"
    rel = Path(*hook.parts[-3:])
    (repo / rel).parent.mkdir(parents=True)
    shutil.copy2(hook, repo / rel)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    for f in files:
        (repo / f).parent.mkdir(parents=True, exist_ok=True)
        (repo / f).write_text("")
    return repo, repo / rel


def tdd_verdict(tmp_path, hook, source, files=(), cwd=None):
    repo, copy = tdd_repo(tmp_path, hook, files)
    return run_hook(copy, edit_payload(source), cwd=cwd or repo)


@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
def test_tdd_guard_denies_unparsable_input(hook):
    """Unparsable input leaves no basis to judge the edit. The old guard read it as "no path"
    and let the edit through; it must block explicitly — with the deny JSON, not only exit 2."""
    proc = subprocess.run(
        [str(hook)], input="not json at all", cwd=ROOT,
        capture_output=True, text=True, timeout=60,
    )
    assert denied(proc), f"{hook} failed open on garbage input\nstdout={proc.stdout!r}"


@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
def test_tdd_guard_denies_without_jq(hook, no_jq_env):
    """jq is the guard's only reader. One PATH change away from a guard that passes everything."""
    proc = run_hook(hook, edit_payload("src/lib/untested.ts"), env=no_jq_env)
    assert denied(proc), f"jq-less run let the edit through\nstderr={proc.stderr!r}"


@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
@pytest.mark.parametrize("path", [
    "docs/ADR.md",
    "package.json",
    "tailwind.config.ts",
    "src/app/page.tsx",
    "src/app/layout.tsx",
    "src/types/nest.ts",
    "types/nest.ts",            # a root-relative types/ path, as Codex sends it
])
def test_tdd_guard_allows_files_that_need_no_test(tmp_path, hook, path):
    """The opposite regression: a guard that denies everything."""
    assert not denied(tdd_verdict(tmp_path, hook, path))


@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
@pytest.mark.parametrize("path", [
    "lib/foo.ts",
    "src/lib/nest/placer.ts",
    "src/components/SheetView.tsx",
])
def test_tdd_guard_denies_an_untested_source(tmp_path, hook, path):
    assert denied(tdd_verdict(tmp_path, hook, path))


@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
@pytest.mark.parametrize("path", [
    "src/lib/sheetSpec.ts",
    "src/lib/partSpec.ts",
    "src/lib/latestLayout.ts",       # "la-test-Layout"
    "src/lib/nest/shortestEdge.ts",  # "shor-test-Edge"
    "src/lib/aspectRatio.ts",        # "a-spec-tRatio"
    "src/lib/inspector.ts",          # "in-spec-tor"
])
def test_a_name_that_merely_contains_test_or_spec_is_not_a_test(tmp_path, hook, path):
    """The old `*test*|*spec*` exemption let the lowercase ones skip the guard (the glob is
    case-sensitive, so `*Spec.ts` was never caught by it — pinned so it stays that way)."""
    assert denied(tdd_verdict(tmp_path, hook, path))


@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
@pytest.mark.parametrize("test_file, source", [
    ("lib/foo.test.ts", "lib/foo.ts"),
    ("lib/foo.spec.ts", "lib/foo.ts"),
    ("lib/__tests__/foo.test.ts", "lib/foo.ts"),
    ("lib/__tests__/foo.spec.ts", "lib/foo.ts"),
    ("__tests__/foo.spec.tsx", "lib/foo.ts"),
    ("src/__tests__/foo.test.ts", "src/lib/foo.ts"),
    ("src/__tests__/foo.test.ts", "src/lib/nest/foo.ts"),
    ("src/__tests__/foo.spec.ts", "src/lib/nest/foo.ts"),
    ("src/app/api/sheets/route.spec.ts", "src/app/api/sheets/route.ts"),
    ("src/app/api/sheets/route.test.tsx", "src/app/api/sheets/route.ts"),
], ids=["colocated-test", "colocated-spec", "dunder-test", "dunder-spec", "parent-dunder-spec",
        "src-dunder", "src-dunder-deep", "src-dunder-deep-spec", "api-spec", "api-tsx"])
def test_tdd_guard_finds_every_test_vitest_collects(tmp_path, hook, test_file, source):
    """vitest's default include is *.{test,spec}.* anywhere, so a spec counts wherever a test
    does — a `.spec` in `__tests__/` was denied as missing. The central `src/__tests__/` folder
    of the src/ layout counts at any depth. No app/api special case: that one mirrored another
    project's vitest config, and here it would deny a route's real tests."""
    proc = tdd_verdict(tmp_path, hook, source, files=[test_file])
    assert not denied(proc), proc.stdout


@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
def test_tdd_guard_resolves_a_relative_path_from_the_repo_root(tmp_path, hook):
    """A relative path (Codex sends `lib/foo.ts`) was judged against whatever the CWD was, so an
    existing test was missed from anywhere but the root and the edit was denied."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    proc = tdd_verdict(tmp_path, hook, "lib/foo.ts", files=["lib/foo.test.ts"], cwd=elsewhere)
    assert not denied(proc), proc.stdout


@pytest.mark.parametrize(
    "name", ['lib/untested"module.ts', "lib/back\\slash.ts"], ids=["quote", "backslash"]
)
@pytest.mark.parametrize("hook", TDD_HOOKS, ids=HOOK_ID)
def test_tdd_deny_stays_json_for_any_filename(tmp_path, hook, name):
    """The basename is interpolated into the reason; unescaped, a `"` or `\\` made stdout
    invalid JSON — a non-blocking error, i.e. the edit went through."""
    assert denied(tdd_verdict(tmp_path, hook, name))


def codex_patch(body):
    command = "apply_patch <<'EOF'\n*** Begin Patch\n" + body + "*** End Patch\nEOF"
    return {"tool_name": "apply_patch", "tool_input": {"command": command}}


def codex_tdd_verdict(tmp_path, body, files=()):
    repo, copy = tdd_repo(tmp_path, TDD_HOOKS[1], files)
    return run_hook(copy, codex_patch(body), cwd=repo)


TESTED = ["lib/reducers.ts", "lib/reducers.test.ts"]


@pytest.mark.parametrize("source", [
    "lib/untested.test.ts",   # a test renamed onto an implementation name
    "lib/reducers.ts",        # a tested module renamed onto an untested name
], ids=["test-to-impl", "tested-to-untested"])
def test_codex_tdd_guard_checks_a_move_destination(tmp_path, source):
    """`*** Move to:` is where a rename writes. Checking only the `Update File:` source let
    implementation land under a name with no test."""
    body = f"*** Update File: {source}\n*** Move to: lib/untested.ts\n@@\n-a\n+a\n"
    assert denied(codex_tdd_verdict(tmp_path, body, files=TESTED))


def test_codex_tdd_guard_lets_a_tested_move_through(tmp_path):
    body = "*** Update File: lib/old.ts\n*** Move to: lib/reducers.ts\n@@\n-a\n+a\n"
    proc = codex_tdd_verdict(tmp_path, body, files=TESTED + ["lib/old.ts", "lib/old.test.ts"])
    assert not denied(proc), proc.stdout


def test_codex_tdd_deny_stays_json_for_a_patched_filename(tmp_path):
    body = '*** Add File: lib/untested"module.ts\n+export const x = 1;\n'
    assert denied(codex_tdd_verdict(tmp_path, body))


# --- wiring: .claude/settings.json and .codex/hooks.json ------------------
# A correct script wired the wrong way blocks nothing either. These run the command string
# exactly as each harness would, through a shell.

def _claude_hook_commands(event=None):
    settings = json.loads(CLAUDE_SETTINGS.read_text())
    for name, groups in settings["hooks"].items():
        if event and name != event:
            continue
        for group in groups:
            for hook in group["hooks"]:
                yield group.get("matcher", ""), hook["command"]


def _codex_groups(event):
    return json.loads(CODEX_HOOKS.read_text())["hooks"][event]


def test_every_claude_hook_command_quotes_the_project_dir():
    """Unquoted, $CLAUDE_PROJECT_DIR splits under a project path with a space, the script is
    never found (exit 127, no JSON) — a non-blocking error, and the call proceeds."""
    for _, command in _claude_hook_commands():
        assert '"$CLAUDE_PROJECT_DIR/' in command, command


BASH_GUARDS = [("block-dangerous.sh", "rm -rf x"), ("block-poll-loop.sh", "sleep 60")]


def test_the_wired_claude_bash_guards_block():
    commands = [c for m, c in _claude_hook_commands("PreToolUse") if m == "Bash"]
    assert commands == [f'bash "$CLAUDE_PROJECT_DIR/scripts/hooks/{s}"' for s, _ in BASH_GUARDS]
    for command, (_, bad) in zip(commands, BASH_GUARDS):
        proc = subprocess.run(
            ["sh", "-c", command], input=json.dumps(bash_payload(bad)), cwd=ROOT,
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)},
        )
        assert denied(proc), f"{command}: rc={proc.returncode} stderr={proc.stderr!r}"


def test_the_wired_codex_bash_guards_block():
    """The poll guard is appended as handler 1 of the Bash group: Codex keys trust by group and
    handler index, so block-dangerous keeps (1, 0) and its approval."""
    groups = _codex_groups("PreToolUse")
    assert groups[1]["matcher"] == "Bash", "group index 1 is the Bash guard's trust key"
    hooks = groups[1]["hooks"]
    assert len(hooks) == len(BASH_GUARDS), hooks
    assert hooks[1]["statusMessage"] == "Checking for polling loops"
    for hook, (script, bad) in zip(hooks, BASH_GUARDS):
        assert f"scripts/hooks/{script}" in hook["command"], hook
        proc = subprocess.run(
            ["sh", "-c", hook["command"]], input=json.dumps(bash_payload(bad)), cwd=ROOT,
            capture_output=True, text=True, timeout=60,
        )
        assert denied(proc), f"{script}: rc={proc.returncode} stderr={proc.stderr!r}"


HANGUL = re.compile("[\uac00-\ud7a3\u3131-\u318e]")


@pytest.mark.parametrize(
    "path",
    [*TDD_HOOKS, BLOCK_HOOK, POLL_HOOK, VALIDATE_HOOK, RULES_HOOK, CODEX_HOOKS],
    ids=lambda p: str(p.relative_to(ROOT)),
)
def test_hook_files_are_english_only(path):
    """Code, comments and agent-facing messages are English; only end-user UI text is Korean."""
    hits = [
        f"{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if HANGUL.search(line)
    ]
    assert not hits, "\n".join(hits)


def test_both_stop_hooks_run_the_shared_validate_script():
    claude = [c for _, c in _claude_hook_commands("Stop")]
    assert claude == ['bash "$CLAUDE_PROJECT_DIR/scripts/hooks/validate.sh"'], claude
    codex = [h["command"] for g in _codex_groups("Stop") for h in g["hooks"]]
    assert len(codex) == 1 and "scripts/hooks/validate.sh" in codex[0], codex


def test_every_wired_script_exists():
    wired = [c for _, c in _claude_hook_commands()]
    wired += [h["command"] for groups in json.loads(CODEX_HOOKS.read_text())["hooks"].values()
              for g in groups for h in g["hooks"]]
    for command in wired:
        rel = command.split(")/", 1)[-1].split("$CLAUDE_PROJECT_DIR/", 1)[-1].strip('"')
        assert (ROOT / rel).is_file(), command


def test_the_rules_hook_is_appended_after_the_existing_codex_groups():
    """Codex keys a handler's trust by event, group index and handler index, so the new group
    goes last — inserting it earlier would shift the guards' indices and drop their trust."""
    groups = _codex_groups("PreToolUse")
    assert [g["matcher"] for g in groups] == [
        "apply_patch|Edit|Write", "Bash", "Bash|apply_patch|Edit|Write"
    ]
    (hook,) = groups[2]["hooks"]
    assert hook["command"] == 'python3 "$(git rev-parse --show-toplevel)/.codex/hooks/rules-context.py"'
    assert hook["timeout"] == 5
    assert hook["additionalContextLimit"] == 32768


# --- validate.sh (Stop, shared by Claude and Codex) -----------------------
# Driven through a throwaway repo, because validate.sh takes its root from the CWD — that keeps
# the case independent of whether this repo has a package.json yet.

@pytest.fixture
def stop_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    return repo


def write_manifest(repo, lint="true"):
    scripts = {"lint": lint, "build": "true", "test": "true"}
    (repo / "package.json").write_text(json.dumps({"name": "t", "scripts": scripts}))


def stop_in(repo, stop_hook_active=False, env=None):
    return run_hook(VALIDATE_HOOK, {"stop_hook_active": stop_hook_active}, env=env, cwd=repo)


def test_no_manifest_has_nothing_to_verify(stop_repo):
    """The template state: no package.json, so no lint/build/test to run. The old inline hook
    ran npm anyway and errored on every Stop."""
    proc = stop_in(stop_repo)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout


def test_no_manifest_needs_no_jq_either(stop_repo, no_jq_env):
    proc = stop_in(stop_repo, env=no_jq_env)
    assert proc.stdout.strip() == "", proc.stdout


needs_npm = pytest.mark.skipif(shutil.which("npm") is None, reason="npm not on PATH")


@needs_npm
def test_a_green_chain_ends_the_turn_silently(stop_repo):
    write_manifest(stop_repo)
    proc = stop_in(stop_repo)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout


@needs_npm
def test_a_failing_chain_blocks_with_its_output(stop_repo):
    write_manifest(stop_repo, lint="echo LINT-BROKE && exit 1")
    out = json.loads(stop_in(stop_repo).stdout)
    assert out["decision"] == "block", out
    assert "LINT-BROKE" in out["reason"], out


@needs_npm
def test_the_retry_is_not_blocked_again(stop_repo):
    """stop_hook_active means we already blocked once; blocking again is an infinite loop."""
    write_manifest(stop_repo, lint="exit 1")
    assert stop_in(stop_repo, stop_hook_active=True).stdout.strip() == ""


def test_stop_hook_blocks_when_it_cannot_report(stop_repo, no_jq_env):
    """With a manifest but no jq the chain result cannot be reported back. Exiting 0 there ends
    the turn with no verification at all, so it must block and say why."""
    write_manifest(stop_repo)
    out = json.loads(stop_in(stop_repo, env=no_jq_env).stdout)
    assert out["decision"] == "block" and "jq" in out["reason"], out


def test_stop_hook_does_not_loop_forever_without_jq(stop_repo, no_jq_env):
    write_manifest(stop_repo)
    proc = stop_in(stop_repo, stop_hook_active=True, env=no_jq_env)
    assert proc.stdout.strip() == "", proc.stdout


# --- rules-context.py (Codex only) ----------------------------------------
# Claude Code loads a `.claude/rules/*.md` file by itself the moment a session reads a file that
# rule's `paths:` glob claims. Codex has no such auto-load; this hook is that delivery path. It
# resolves literal file reads and edit/patch paths, and injects the governing rules as
# `additionalContext` at PreToolUse.
#
# Its contract is the mirror image of the guards above:
#   - it makes no permission decision; the only `allow` it sends is the pair an `updatedInput`
#     (the relative-read cwd guard) requires
#   - it is fail-OPEN — garbage in means silence and exit 0
#   - it is idempotent per agent context — a rule is delivered once
#   - with no `.claude/rules/` at all (this repo today) it is a pure no-op
#
# Every case runs against a throwaway repo holding a copy of the hook and the matcher, so the
# cases do not depend on which rules this repo has.

WEB_RULE = """---
paths: ["app/**", "lib/**"]
---
# Web app rule
WEB-RULE-BODY
"""

API_RULE = """---
paths:
  - "app/api/**"
---
# API route rule
API-RULE-BODY
"""


@pytest.fixture
def rules_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".codex" / "hooks").mkdir(parents=True)
    (repo / "scripts").mkdir()
    shutil.copy2(RULES_HOOK, repo / ".codex" / "hooks" / "rules-context.py")
    shutil.copy2(RULES_MATCH, repo / "scripts" / "rules_match.py")
    for rel in ("app/api/route.ts", "app/page.tsx", "lib/nest.ts", "README.md"):
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x\n")
    rules = repo / ".claude" / "rules"
    rules.mkdir(parents=True)
    (rules / "web-app.md").write_text(WEB_RULE)
    (rules / "api-routes.md").write_text(API_RULE)
    return repo


def rules_hook(repo):
    return repo / ".codex" / "hooks" / "rules-context.py"


def rules_env(tmp_path):
    """A private TMPDIR, so a case's per-agent markers never collide with another case's — or
    with the real /tmp the hook writes to in a live session."""
    tmpdir = tmp_path / "tmpdir"
    tmpdir.mkdir(exist_ok=True)
    return {**os.environ, "TMPDIR": str(tmpdir)}


def rules_payload(command=None, file_path=None, tool_name=None, session="s1", agent_id=None,
                  cwd=None):
    tool_input = {}
    if command is not None:
        tool_input["command"] = command
    if file_path is not None:
        tool_input["file_path"] = str(file_path)
    payload = {
        "session_id": session,
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name or ("Bash" if command is not None else "Write"),
        "tool_input": tool_input,
    }
    if agent_id is not None:
        payload["agent_id"] = agent_id
    if cwd is not None:
        payload["cwd"] = str(cwd)
    return payload


def hook_output(proc):
    """The whole hookSpecificOutput — empty when the hook stayed silent."""
    if not proc.stdout.strip():
        return {}
    return json.loads(proc.stdout)["hookSpecificOutput"]


def injected(proc):
    return hook_output(proc).get("additionalContext", "")


def run_rules(repo, tmp_path, payload):
    return run_hook(rules_hook(repo), payload, env=rules_env(tmp_path), cwd=repo)


def test_an_edit_path_injects_its_area_rule(rules_repo, tmp_path):
    context = injected(run_rules(rules_repo, tmp_path,
                                 rules_payload(file_path=rules_repo / "lib" / "nest.ts")))
    assert 'path=".claude/rules/web-app.md" matched="lib/nest.ts"' in context, context[:400]
    assert "WEB-RULE-BODY" in context
    assert "api-routes.md" not in context
    assert "paths:" not in context, "the frontmatter is stripped"


def test_an_apply_patch_add_file_resolves_before_the_file_exists(rules_repo, tmp_path):
    """The rule has to arrive *before* the new file exists — after it is written, the decision
    the rule governs is already made."""
    patch = "*** Begin Patch\n*** Add File: app/api/new.ts\n+export const x = 1;\n*** End Patch"
    proc = run_rules(rules_repo, tmp_path,
                     rules_payload(command=patch, tool_name="apply_patch", cwd=rules_repo))
    context = injected(proc)
    assert "API-RULE-BODY" in context and "WEB-RULE-BODY" in context, context[:400]
    assert "updatedInput" not in hook_output(proc)


def test_the_specific_rule_precedes_the_broad_one(rules_repo, tmp_path):
    """Specific-first is the order that survives the byte cap."""
    command = f"cat {rules_repo}/app/api/route.ts"
    context = injected(run_rules(rules_repo, tmp_path, rules_payload(command=command)))
    assert context.index("api-routes.md") < context.index("web-app.md"), context[:400]


def test_an_absolute_bash_read_needs_no_guard(rules_repo, tmp_path):
    command = f"head -n 1 {rules_repo}/app/page.tsx"
    out = hook_output(run_rules(rules_repo, tmp_path, rules_payload(command=command)))
    assert "WEB-RULE-BODY" in out["additionalContext"]
    assert "updatedInput" not in out and "permissionDecision" not in out, out


def test_a_rule_is_injected_once_per_agent(rules_repo, tmp_path):
    payload = rules_payload(file_path=rules_repo / "lib" / "nest.ts")
    assert "WEB-RULE-BODY" in injected(run_rules(rules_repo, tmp_path, payload))
    assert injected(run_rules(rules_repo, tmp_path, payload)) == ""
    # A subagent shares the parent's session id but has a context window of its own.
    sibling = {**payload, "agent_id": "agent-a"}
    assert "WEB-RULE-BODY" in injected(run_rules(rules_repo, tmp_path, sibling))
    # And a new session starts empty.
    other = {**payload, "session_id": "s2"}
    assert "WEB-RULE-BODY" in injected(run_rules(rules_repo, tmp_path, other))


def test_a_relative_read_gets_its_rules_and_a_cwd_guard(rules_repo, tmp_path):
    """Codex never tells the hook where the shell stands, so a relative read is resolved against
    the payload cwd and the command is rewritten to run only where the shell stands there."""
    command = "cat lib/nest.ts"
    out = hook_output(run_rules(rules_repo, tmp_path, rules_payload(command=command,
                                                                    cwd=rules_repo)))
    assert out["permissionDecision"] == "allow"
    assert "WEB-RULE-BODY" in out["additionalContext"]
    guarded = out["updatedInput"]["command"]
    assert guarded.endswith(command)
    here = subprocess.run(["bash", "-c", guarded], cwd=rules_repo, capture_output=True, text=True)
    assert (here.returncode, here.stdout) == (0, "x\n"), here.stderr
    elsewhere = subprocess.run(["bash", "-c", guarded], cwd=tmp_path, capture_output=True,
                               text=True)
    assert (elsewhere.returncode, elsewhere.stdout) == (1, ""), elsewhere.stderr


def test_a_path_no_rule_claims_injects_nothing(rules_repo, tmp_path):
    proc = run_rules(rules_repo, tmp_path, rules_payload(command=f"cat {rules_repo}/README.md"))
    assert proc.stdout.strip() == "", proc.stdout


@pytest.mark.parametrize("make_payload", [
    lambda repo: rules_payload(file_path=repo / "lib" / "nest.ts"),
    lambda repo: rules_payload(command=f"cat {repo}/app/page.tsx"),
    lambda repo: rules_payload(command="cat lib/nest.ts", cwd=repo),
    lambda repo: rules_payload(command="*** Begin Patch\n*** Add File: app/x.ts\n+x\n"
                                       "*** End Patch", tool_name="apply_patch", cwd=repo),
], ids=["edit", "absolute-read", "relative-read", "apply-patch"])
def test_with_no_rules_dir_the_hook_is_a_pure_no_op(rules_repo, tmp_path, make_payload):
    """This repo has no `.claude/rules/` yet. With nothing to deliver, the hook must not touch
    the call at all — in particular it must not rewrite a relative read with the cwd guard, and
    it must leave no marker state behind."""
    shutil.rmtree(rules_repo / ".claude" / "rules")
    proc = run_rules(rules_repo, tmp_path, make_payload(rules_repo))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout
    assert not list((tmp_path / "tmpdir").iterdir())


def test_a_missing_matcher_fails_open(rules_repo, tmp_path):
    (rules_repo / "scripts" / "rules_match.py").unlink()
    proc = run_rules(rules_repo, tmp_path,
                     rules_payload(file_path=rules_repo / "lib" / "nest.ts"))
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout


def test_rules_context_fails_open_on_garbage_input(rules_repo, tmp_path):
    proc = subprocess.run(
        [str(rules_hook(rules_repo))], input="not json at all", cwd=rules_repo,
        capture_output=True, text=True, timeout=60, env=rules_env(tmp_path),
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "", proc.stdout


def test_a_post_tool_use_call_is_ignored(rules_repo, tmp_path):
    payload = {**rules_payload(file_path=rules_repo / "lib" / "nest.ts"),
               "hook_event_name": "PostToolUse"}
    proc = run_rules(rules_repo, tmp_path, payload)
    assert proc.returncode == 0 and proc.stdout.strip() == "", proc.stdout


@pytest.mark.parametrize("command", ["cat lib/nest.ts", "cat {repo}/lib/nest.ts"])
def test_rules_context_never_blocks(rules_repo, tmp_path, command):
    """It never denies or asks: the one permissionDecision it sends is the `allow` that an
    `updatedInput` requires, and only together with one."""
    proc = run_rules(rules_repo, tmp_path,
                     rules_payload(command=command.format(repo=rules_repo), cwd=rules_repo))
    assert proc.returncode == 0, proc.stderr
    out = hook_output(proc)
    assert out.get("permissionDecision") in (None, "allow"), proc.stdout
    assert ("permissionDecision" in out) == ("updatedInput" in out), proc.stdout


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
