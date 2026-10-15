#!/bin/bash
# Stop Hook — shared by Claude and Codex. Runs lint/build/test when a turn (= step) ends.
# On failure it feeds the output back to prompt one self-correction (decision: block), and it
# stops an endless loop.
#
# On success it prints nothing and exits 0 (= normal stop).
# An exit code cannot block Stop (a non-zero exit is a non-blocking error that is only shown).
# Only the {"decision":"block"} JSON actually prompts self-correction.

INPUT=$(cat)
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")

# Skip when there are no npm scripts to check (e.g. the template state).
if [ ! -f "$ROOT/package.json" ]; then
  exit 0
fi

# Without jq on PATH neither stop_hook_active nor the block JSON can be produced. Exiting 0 here
# would end the whole turn unchecked, so hand it to a human instead of running the checks (the
# reason is a static heredoc, so it needs no escaping).
# stop_hook_active is still re-checked without jq, to stop an endless self-correction loop.
if ! command -v jq >/dev/null 2>&1; then
  if printf '%s' "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
    exit 0
  fi
  cat <<'EOF'
{
  "decision": "block",
  "reason": "jq was not found, so the lint/build/test result cannot be reported (check PATH). The checks did not run: run npm run lint && npm run build && npm run test yourself and confirm they pass."
}
EOF
  exit 0
fi

STOP_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)

# If self-correction was already prompted once, stop here to prevent an endless loop.
if [ "$STOP_ACTIVE" = "true" ]; then
  exit 0
fi

OUT=$(cd "$ROOT" && npm run lint 2>&1 && npm run build 2>&1 && npm run test 2>&1)
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  REASON=$(printf 'lint/build/test failed. Read the output below, fix the cause, then check again:\n\n%s' "$OUT" | tail -c 4000)
  jq -n --arg r "$REASON" '{decision: "block", reason: $r}'
fi

exit 0
