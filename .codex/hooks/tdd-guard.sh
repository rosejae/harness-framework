#!/bin/bash
# TDD Guard Hook — Codex PreToolUse[apply_patch|Edit|Write]
# Before implementation code is written or changed, checks that the module's test file exists
# first. Writing implementation code without a test is denied.
#
# Codex edits files through apply_patch (tool_input.command), so the target paths are taken from
# the patch body's "*** Add File:" / "*** Update File:" lines and from the rename destination
# "*** Move to:" — moving a test onto an implementation name, or a tested module onto an untested
# name, is the same as writing implementation code. (An Edit/Write-shaped tool_input.file_path is
# handled too.)

INPUT=$(cat)

# Run from the repo root, so a relative path (`lib/foo.ts`) is not judged against whatever the
# CWD happens to be. (Run from elsewhere, the hook missed tests that existed and denied wrongly.)
REPO_ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] && cd "$REPO_ROOT"

# If jq fails (non-JSON input, or no jq) there is nothing to judge by — deny explicitly instead
# of silently letting the edit through (fail-open). The block has to be deny JSON — exit 2 alone
# is only displayed.
if ! FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path? // empty' 2>/dev/null); then
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: the hook input could not be parsed as JSON (non-JSON input, or jq is missing), so whether a test exists first cannot be judged. The edit is not let through without evidence."
  }
}
EOF
  echo "TDD GUARD: the hook input could not be parsed as JSON." >&2
  exit 2
fi
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command? // empty' 2>/dev/null)

# Collect the paths to check
CANDIDATES=""
[ -n "$FILE_PATH" ] && CANDIDATES="$FILE_PATH"
if [ -n "$CMD" ]; then
  EXTRACTED=$(printf '%s\n' "$CMD" | grep -oE '\*\*\* ((Add|Update) File|Move to): .+' | sed -E 's/^\*\*\* ((Add|Update) File|Move to): //')
  CANDIDATES=$(printf '%s\n%s' "$CANDIDATES" "$EXTRACTED")
fi

# The file name is the only part of the reason that comes from the payload, so escape it: a `"`
# or `\` in the name would turn the deny into invalid JSON, which the harness treats as a
# non-blocking error — the edit would go straight through.
deny() {
  local base="$1"
  base=${base//\\/\\\\}
  base=${base//\"/\\\"}
  base=${base//[[:cntrl:]]/ }
  cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: no test file exists for '${base}'. Write the test before writing the implementation code. (Example test file: ${base}.test.ts)"
  }
}
EOF
  exit 0
}

while IFS= read -r FP; do
  [ -z "$FP" ] && continue

  # Editing a test file itself is allowed
  # (a broad glob like *test*/*spec* would also pass innocent names such as latestLayout.ts or
  #  aspectRatio.ts, so only real test-file shapes are exempt)
  case "$FP" in
    *.test.*|*.spec.*|*/__tests__/*|__tests__/*) continue ;;
  esac

  # Config/type/style/doc files need no test — allowed
  case "$FP" in
    *.json|*.css|*.scss|*.md|*.yml|*.yaml|*.env*|*.config.*|*tailwind*|*postcss*|*next.config*|*tsconfig*) continue ;;
  esac

  # The types/ folder needs no test — allowed
  case "$FP" in
    */types/*|types/*|*/types.ts|*/types.d.ts) continue ;;
  esac

  # Next.js framework files are allowed (layout, page, loading, error, not-found, global styles)
  case "$FP" in
    */layout.tsx|*/layout.ts|*/page.tsx|*/page.ts|*/loading.tsx|*/error.tsx|*/not-found.tsx|*/globals.css) continue ;;
  esac

  # For a source file, check that a test file exists
  case "$FP" in
    *.ts|*.tsx|*.js|*.jsx)
      DIR=$(dirname "$FP")
      BASENAME=$(basename "$FP" | sed -E 's/\.(ts|tsx|js|jsx)$//')
      # Test file candidates: the same folder, its __tests__, the __tests__ one level up, and the
      # root src/__tests__ — every *.{test,spec}.* that vitest collects by default counts.
      # (src/__tests__ can stay relative because the CWD is the repo root)
      TEST_FOUND=false
      PARENT=$(dirname "$DIR")
      for T in "$DIR" "$DIR/__tests__" "$PARENT/__tests__" "src/__tests__"; do
        for KIND in test spec; do
          for EXT in ts tsx js jsx; do
            if [ -f "${T}/${BASENAME}.${KIND}.${EXT}" ]; then
              TEST_FOUND=true
              break 3
            fi
          done
        done
      done

      if [ "$TEST_FOUND" = false ]; then
        deny "$BASENAME"
      fi
      ;;
  esac
done <<< "$CANDIDATES"

exit 0
