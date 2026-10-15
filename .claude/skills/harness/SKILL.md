---
description: Entry point of the Harness workflow — design and create steps under phases/ and run them in order with scripts/execute.py. If you only need a progress report, use /harness-status.
argument-hint: "[task-name — empty means start from designing a new task]"
allowed-tools: Bash, Read, Grep, Glob, Edit, Write, Agent
---

This project uses the Harness framework. Follow the workflow below.

---

## Workflow

### A. Explore

Read the documents under `/docs/` (PRD, ARCHITECTURE, ADR, etc.) to understand the project's plan, architecture and design intent. Use Explore agents in parallel when needed.

### B. Discuss

If anything needs to be made concrete or decided technically before implementation, present it to the user and discuss it.

### C. Step design

When the user asks for an implementation plan, write a draft split into several steps and ask for feedback.

Design principles:

1. **Minimal scope** — one step covers one layer or module. If several modules must change at once, split the step.
2. **Self-contained** — each step file runs in an independent Codex session (`codex exec`). External references such as "as discussed in the previous conversation" are forbidden. Put every piece of information it needs inside the file.
3. **Force preparation** — name the relevant document paths and the paths of files created/modified by previous steps, so the session reads the code and understands the context before it works.
4. **Signature-level instructions** — give only the function/class interfaces and leave the internal implementation to the agent. But always state the core rules that must not drift from the design intent (idempotency, security, data integrity, etc.).
5. **AC are executable commands** — not abstract statements like "X should work", but real, runnable verification commands such as `npm run build && npm test`.
6. **Be concrete about cautions** — instead of "be careful", write "Do not do X. Reason: Y".
7. **Naming** — the step name is a kebab-case slug that expresses the step's core module/work in one or two words (e.g. `project-setup`, `api-layer`, `auth-flow`).

### D. File creation

Once the user approves, create the files below.

#### D-1. `phases/index.json` (overall status)

The top-level index that manages multiple tasks. If it already exists, add a new entry to the `phases` array.

```json
{
  "phases": [
    {
      "dir": "0-mvp",
      "status": "pending"
    }
  ]
}
```

- `dir`: task directory name.
- `status`: `"pending"` | `"completed"` | `"error"` | `"blocked"`. execute.py updates it automatically while running.
- Timestamps (`completed_at`, `failed_at`, `blocked_at`) are recorded automatically by execute.py when the status changes. Do not add them at creation.

#### D-2. `phases/{task-name}/index.json` (task detail)

```json
{
  "project": "<project name>",
  "phase": "<task-name>",
  "steps": [
    { "step": 0, "name": "project-setup", "status": "pending" },
    { "step": 1, "name": "core-types", "status": "pending" },
    { "step": 2, "name": "api-layer", "status": "pending" }
  ]
}
```

Field rules:

- `project`: project name (see CLAUDE.md).
- `phase`: task name. Match the directory name.
- `steps[].step`: sequence number starting at 0.
- `steps[].name`: kebab-case slug.
- `steps[].status`: all start as `"pending"`.
- `steps[].timeout_s` (optional): time limit in seconds for the step's Codex session. Defaults to 1800 seconds when omitted. Past the limit the session is killed, counted as a failure, and retried.

Status transitions and auto-recorded fields:

| Transition | Recorded fields | Recorded by |
|------|-------------|----------|
| → `completed` | `completed_at`, `summary` | Codex session (summary), execute.py (timestamp) |
| → `error` | `failed_at`, `error_message` | Codex session (message), execute.py (timestamp) |
| → `blocked` | `blocked_at`, `blocked_reason` | Codex session (reason), execute.py (timestamp) |

`summary` is a one-line summary of what the step produced; execute.py accumulates it and passes it to the next step's prompt as context. So it should carry what is useful to the next step (files created, key decisions, etc.).

`created_at` is recorded once at the task level by execute.py on the first run. The step-level `started_at` is also recorded automatically by execute.py when each step starts. Do not add them at creation.

#### D-3. `phases/{task-name}/step{N}.md` (one per step)

The canonical template is in [`references/step-template.md`](references/step-template.md) — read it when creating a step file and follow its skeleton exactly.

### E. Execution

```bash
python3 scripts/execute.py {task-name}        # run in order
python3 scripts/execute.py {task-name} --push  # push after running
```

What execute.py handles automatically:

- Step execution — runs each step with `codex exec` (non-interactive). The Codex session does the implementation, AC verification and commits.
- Guardrail delivery — **the runner does not inject `CLAUDE.md` or `docs/` documents into the prompt.** A step prompt consists only of a one-line role intro, the summaries of completed steps, the previous error on a retry, the working rules, and the step file. Codex reads `CLAUDE.md` as its project doc through the `AGENTS.md` symlink and follows the links in it to open the documents it needs. Area rules (`.claude/rules/*.md`) are injected by `.codex/hooks/rules-context.py` when a matching file is read or modified. Step-specific files go in the step file's "Files to read" section.
- Codex hooks — `.codex/hooks.json` puts guardrails on every step's Codex session: blocking implementation without tests (TDD), blocking dangerous commands, and lint/build/test verification at stop. execute.py trusts the in-repo hooks with `--dangerously-bypass-hook-trust`.
- Create/check out the `feat-{task-name}` branch
- Context accumulation — passes completed steps' summaries to the next step's prompt
- Self-correction — on failure, retries up to 3 times and feeds the previous error message back into the prompt (running past the time limit also counts as a failure)
- Two-stage commits — commits code changes (`feat`) and metadata (`chore`) separately. A step that has used up all retries commits only metadata, never the code
- Timestamps — records started_at, completed_at, failed_at, blocked_at automatically

Progress reporting (while running in the background):

- Run execute.py with `run_in_background`. You are **notified automatically** when it finishes, so do not build short-interval polling loops like `until grep … sleep 5; done`. Polling loops leave zombie processes and waste context.
- Check intermediate progress with a read-only snapshot: `python3 scripts/execute.py <task> --status` (per-step status table, whether it is running, number of `feat-<task>` commits, roll-up). The `/harness-status` skill wraps this.
- The table cuts each step's summary to its first 60 characters. If you need everything a completed step left, do not parse `index.json` by hand — use `python3 scripts/execute.py <task> --summary [step|N]` (the selector is the 0-based step number shown in the table or the step name; omit it for all completed steps).
- If you need "tell me every N minutes", don't retype the same instruction — hand it to `/loop`: `/loop 3m /harness-status <task>`.

Error recovery:

- **On error**: in `phases/{task-name}/index.json`, set the step's `status` back to `"pending"`, delete `error_message`, and rerun.
- **On blocked**: resolve the cause written in `blocked_reason`, then set `status` back to `"pending"`, delete `blocked_reason`, and rerun.
