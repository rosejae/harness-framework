---
description: Read-only progress report on a harness execute.py run that is going in the background.
argument-hint: "[task-name (default: the in-progress phase in phases/index.json, else 0-mvp)]"
allowed-tools: Bash, Read, Grep, Glob
---

Report **the current progress of harness `execute.py` (background step execution)**, **read-only**.
Do not run any step yourself.

## Choosing the task (`$ARGUMENTS`)

- If an argument is given, use that task-name.
- If empty, pick the `dir` of the phase in `phases/index.json` whose `status` is `pending`/`error`/`blocked`;
  if there is none, use the last phase (if there are no phases at all, `0-mvp`).

## Procedure

1. Take a snapshot:

   ```
   python3 scripts/execute.py <task-name> --status
   ```

   This one line prints **whether the background run is alive (the `Run:` line, with pid)** + the per-step status table +
   the commit count on the `feat-<task>` branch + a roll-up. That is everything the report needs —
   do not read `index.json` directly, do not assemble a git log by hand, and do not add
   `pgrep`/`ps` to check liveness (`--status` is the single source).

2. If you were also asked **what completed steps left behind**, run one more line:

   ```
   python3 scripts/execute.py <task-name> --summary [step|N]
   ```

   It prints the step summaries **in full, untruncated** (the `--status` table shows only the first 60 characters) —
   do not parse `index.json` by hand. The selector is a **step number (0-based — the number shown in the `--status` table)
   or a step name**; omit it for all completed steps.

3. Compared with the previous report, highlight **what changed**: newly completed steps, new commits,
   transitions to `error`/`blocked`. On the first report, show the whole table.
4. If any step is `error`/`blocked`, give the `/harness` skill §E "Error recovery" procedure in one line
   (error → set status back to `pending`, delete `error_message`, rerun; blocked → resolve the reason, then
   the same way, delete `blocked_reason` and rerun).

## Periodic updates

If you need "tell me every N minutes", don't retype this instruction each time — use **`/loop`**:

```
/loop 3m /harness-status 0-mvp
```

If execute.py was started in the background, you are notified automatically when it finishes. Do not build a
short-interval `until grep … sleep` polling loop to wait (see `/harness` skill §E).
