#!/usr/bin/env python3
"""Area-rule context injection — Codex tool hooks. Claude-side counterpart: none needed.

Claude Code loads `.claude/rules/<area>.md` by itself the moment a session reads a file that
rule's `paths:` glob claims. Codex has no such auto-load, so a Codex session edits an area
with none of its rules in front of it and only `CLAUDE.md` (via the `AGENTS.md` symlink) to go
on. This hook is that delivery path: at a tool read, it works out which files the call touches,
asks `scripts/rules_match.py::rules_for_paths` which rules govern them, and hands them back as
`additionalContext`.

CONTRACT — a third category next to the repo's two hook kinds (blocking guards are
fail-closed; reporting guards block and state the reason):

  - With no `.claude/rules/` directory it returns before doing anything: no context, no cwd
    guard, no marker state. Until the first rule exists the hook is a pure no-op.

  - Context delivery decides no permissions. Codex's Bash `tool_input` carries only
    `command` (the model's exec `workdir` is dropped) and the payload's `cwd` is the session's
    `-C` folder, not the shell's (codex-cli 0.161.0; a shell standing elsewhere was
    reproduced). So a relative Bash read with omitted workdir is resolved against the payload
    cwd, its rules go out at once, and the same answer carries `allow` + an `updatedInput`
    that guards the command: `[ "$(pwd -P)" = <cwd> ] || { echo <GUARD> >&2; exit 1; }; …`.
    Where the shell stands there, the read runs untouched; anywhere else it does not run at
    all, and the model re-reads by absolute path. Codex takes `allow`, `updatedInput` and
    `additionalContext` from one answer. That `allow` is only `updatedInput`'s required pair,
    not an approval: per the 0.161.0 source the normal approval and sandbox still apply after
    it (not measured interactively), and under `approval_policy=never` it did not grant an
    escalation Codex refused.
  - It is FAIL-OPEN, and always exits 0. The whole body sits in one `try/except Exception:
    pass`. This is the deliberate opposite of a blocking guard: a guard has something to
    judge and must not pass without a basis, while this hook only adds context, so a failure
    here must cost the call nothing rather than stop it.
  - It is IDEMPOTENT per agent context. Subagents share the parent's session id, so markers
    also use the agent id (or transcript path) to avoid suppressing a sibling's delivery.
    Re-sending a whole rule on every `cat` would cost more context than it delivers; markers live
    outside the tree (see "session state" below).
  - It needs no `jq`: stdin is parsed with Python's `json`.
  - Patch headers are extracted regardless of `tool_name`: Codex sometimes routes an
    `apply_patch` through the shell, so a `tool_name == "Bash"` patch payload is real.

KNOWN LIMITS (in the convention of `scripts/hooks/block-dangerous.sh`'s "Known limits" — none of these is
a block, every one of them is over-delivery or silence; the guard is the one place a call
fails, and only when the shell is not where its relative paths were resolved):
  - a directory argument resolves to nothing (`grep -rn x lib/` injects no rule), and a
    relative one is guarded all the same
  - when the guard stops a call, the rules picked against the payload cwd were already sent
    and marked, so they may govern a file that was never read; the absolute re-read still
    gets its own area's rule unless that one was already sent
  - a variable or a shell-expanded glob is invisible (`cat lib/*.ts`, `cat "$F"`)
  - dynamic shell expansion, wrappers, heredocs and directory changes inside a command are
    not interpreted; use a literal read with tool workdir or an absolute path
  - a context compaction that drops an already-injected rule is undetectable from here: the
    agent's marker says delivered and the rule is not sent again
  - `*** Move to:` and `*** Delete File:` patch headers are not matched, so a move's
    destination area gets no rule from the move
Codex trusts each handler on its own: the hash covers the event, the matcher group and that
handler's settings, keyed by its event, group index and handler index. Editing this script's
BODY, or another event's group, is free; changing this handler's entry in `.codex/hooks.json`,
or shifting its index, needs re-approval.
"""

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

# Session cwd and shell workdir may differ; the repository root is anchored to this script.
ROOT = Path(__file__).resolve().parents[2]

# UTF-8 byte cap, including the preamble. A 109,522-byte injection arrived intact under the
# declared additionalContextLimit 32768 (codex-cli 0.160.1), so that limit is not a 32 KiB
# byte cap; its unit was not established. Raise this ceiling if the rules tree outgrows it.
MAX_BYTES = 131072

READERS = {"cat", "sed", "head", "tail", "less", "rg", "grep", "wc"}
PATCH_FILE = r"^\*\*\* (?:Add|Update) File: (.+)$"

PREAMBLE = (
    "These are the .claude/rules/ area rules that govern the files this tool call touches.\n"
    "Codex has no .claude/rules/ auto-load, so this hook is the delivery path; each rule is "
    "sent once per agent context.\n"
)
GUARD = (
    "Not run: the shell's cwd is not {cwd}, the session folder the area-rule hook resolved "
    "this command's relative paths against. Read the same file again by absolute path."
)

SESSION_TTL_SECONDS = 24 * 60 * 60


def rules_match_module():
    """`scripts/rules_match.py` imported in-process. It owns the only path -> rule matcher in
    the repo; a second copy here would drift from it and the two would disagree."""
    spec = importlib.util.spec_from_file_location(
        "rules_match_for_rules_context", ROOT / "scripts" / "rules_match.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def relativize(token, cwd=ROOT):
    """An absolute path under the repo root, made repo-relative — the rule globs are written
    against repo-relative paths. A session often reads with an absolute path, so this is a
    normal shape here, not an edge case."""
    try:
        return (cwd / token).resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None


def read_tokens(cmd):
    """Literal reader arguments only; an echoed path or grep pattern is not a read target.
    posix=False keeps a quoted `;` one word but not `\\"`, so posix=True is the fallback; a
    quote neither can balance yields no tokens, never an exception that loses patch headers."""
    if cmd.lstrip().startswith("*** Begin Patch") or "<<" in cmd:
        return []
    for posix in (False, True):
        lexer = shlex.shlex(cmd, posix=posix, punctuation_chars=";&|\n")
        lexer.whitespace = " \t\r"
        lexer.whitespace_split = True
        segments, words = [], []
        try:
            for token in [*lexer, ";"]:
                if token and all(char in ";&|\n" for char in token):
                    if words:
                        segments.append(words)
                        words = []
                else:
                    words.extend([token] if posix else shlex.split(token))
            break
        except ValueError:
            segments = []
    if any(Path(words[0]).name == "cd" for words in segments):
        return []
    targets = []
    for words in segments:
        reader = Path(words[0]).name
        if reader not in READERS:
            continue
        args = words[1:]
        skip_pattern = reader in {"rg", "grep", "sed"} and "--files" not in args
        value_options = {"-n", "-c", "--lines", "--bytes"} if reader in {"head", "tail"} else {
            "-g", "--glob", "--iglob", "-m", "--max-count", "-A", "-B", "-C",
            "--after-context", "--before-context", "--context", "--max-depth",
        }
        skip_value = False
        for arg in args:
            if skip_value:
                skip_value = False
                continue
            if arg in value_options:
                skip_value = True
                continue
            if arg in {"-e", "--regexp", "-f"}:
                skip_pattern = True
                continue
            if arg.startswith("-"):
                if arg.startswith(("--regexp=", "-e")):
                    skip_pattern = False
                continue
            if skip_pattern:
                skip_pattern = False
                continue
            if not any(char in arg for char in "$`*?["):
                targets.append(arg)
    return targets


def paths_from_tokens(tokens, cwd):
    found = []
    for token in tokens:
        rel = relativize(token, cwd)
        if rel and (ROOT / rel).is_file():
            found.append(rel)
    return found


def paths_from_command(cmd, cwd=ROOT):
    """Literal reads plus patch headers; Add File deliberately needs no existing file."""
    found = paths_from_tokens(read_tokens(cmd), cwd)
    for raw in re.findall(PATCH_FILE, cmd, re.M):
        rel = relativize(raw.strip(), cwd)
        if rel:
            found.append(rel)
    return found


def strip_frontmatter(text):
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).lstrip("\n")


def marker_base():
    """State that belongs to the run, not to the tree, so it lives under `${TMPDIR:-/tmp}`.
    A cache under `.codex/` would turn up in `git status` — which is also why there is no
    `.gitignore` line to go with this."""
    return Path(os.environ.get("TMPDIR", "/tmp")) / "codex-rules-context"


def sweep(base):
    """Session directories older than a day, removed. A session never outlives one, and
    nothing else ever deletes these — so the sweep runs on every call, not only on the ones
    that inject."""
    cutoff = time.time() - SESSION_TTL_SECONDS
    with contextlib.suppress(OSError):
        for entry in os.scandir(base):
            with contextlib.suppress(OSError):
                if entry.is_dir() and entry.stat().st_mtime < cutoff:
                    shutil.rmtree(entry.path)


def session_dir(payload):
    raw = str(payload.get("session_id") or "")
    sid = re.sub(r"[^A-Za-z0-9_-]", "", raw)[:64] or "nosession"
    directory = marker_base() / sid
    identity = payload.get("agent_id") or payload.get("transcript_path")
    if identity:
        directory /= hashlib.sha256(str(identity).encode("utf-8")).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def claim(marker):
    """Atomic: two tool calls running in parallel must not both inject the same rule."""
    try:
        os.close(os.open(str(marker), os.O_CREAT | os.O_EXCL, 0o644))
        return True
    except OSError:
        return False


def rule_context(payload, paths):
    """The rules these paths call for that this agent has not been sent yet, as one
    `additionalContext` string — empty when there are none."""
    if not paths:
        return ""

    matcher = rules_match_module()
    rules = matcher.rules_for_paths(ROOT, paths)
    if not rules:
        return ""

    # Which input path each rule is here for. Resolved one path at a time so the attribution is
    # the first path that rule actually claims, not just the first path on the line.
    first_match = {}
    for path in paths:
        for rule in matcher.rules_for_paths(ROOT, [path]):
            first_match.setdefault(rule, path)

    directory = session_dir(payload)
    total = len(PREAMBLE.encode("utf-8"))
    blocks = []
    for rule in rules:
        marker = directory / re.sub(r"[^A-Za-z0-9_.-]", "_", matcher.rule_name(ROOT, rule))
        if marker.exists():
            continue
        body = strip_frontmatter(rule.read_text(encoding="utf-8"))
        block = (
            f'<area-rule path="{rule.relative_to(ROOT).as_posix()}" '
            f'matched="{first_match.get(rule, paths[0])}">\n{body}\n</area-rule>\n'
        )
        size = len(block.encode("utf-8"))
        # Whole rules only, and the fit is decided BEFORE the claim: a rule that does not fit
        # is not marked as sent, so the next call delivers it.
        if total + size > MAX_BYTES:
            continue
        if not claim(marker):
            continue
        blocks.append(block)
        total += size

    return PREAMBLE + "".join(blocks) if blocks else ""


def main():
    payload = json.loads(sys.stdin.read())
    if (payload.get("hook_event_name") or "PreToolUse") != "PreToolUse":
        return
    if not (ROOT / ".claude" / "rules").is_dir():
        return
    sweep(marker_base())
    tool_input = payload.get("tool_input") or {}
    session_cwd = Path(payload.get("cwd") or ROOT).resolve()
    workdir = tool_input.get("workdir")
    cwd = session_cwd / (workdir or ".")

    raw_cmd = tool_input.get("command") or ""
    cmd = " ".join(str(part) for part in raw_cmd) if isinstance(raw_cmd, list) else str(raw_cmd)

    candidates = paths_from_command(cmd, cwd)
    file_path = tool_input.get("file_path")
    if file_path:
        rel = relativize(str(file_path), cwd)
        if rel:
            candidates.append(rel)

    output = {"hookEventName": "PreToolUse"}
    # The hook never learns where the shell stands, so a relative read resolved against the
    # payload cwd runs only if the shell stands there. `~` is the shell's, not a relative path.
    # A list command is left alone: an updatedInput string would change the call's shape.
    relative_read = any(
        not Path(token).is_absolute() and not token.startswith("~") for token in read_tokens(cmd)
    )
    if payload.get("tool_name") == "Bash" and isinstance(raw_cmd, str) and not workdir \
            and relative_read:
        here = shlex.quote(str(session_cwd))
        message = shlex.quote(GUARD.format(cwd=session_cwd))
        output["permissionDecision"] = "allow"
        output["updatedInput"] = {
            "command": f'[ "$(pwd -P)" = {here} ] || {{ echo {message} >&2; exit 1; }}; {cmd}'
        }
    context = rule_context(payload, list(dict.fromkeys(candidates)))
    if context:
        output["additionalContext"] = context
    if len(output) > 1:
        print(json.dumps({"hookSpecificOutput": output}))


try:
    main()
except Exception:  # fail-open, always: this hook adds context, it never withholds a call
    pass
sys.exit(0)
