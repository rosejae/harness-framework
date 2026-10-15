#!/bin/bash
# TDD Guard Hook — PreToolUse[Edit|Write]
# Before implementation code is written, checks that the module's test file exists first.
# Writing implementation code without a test is denied.

INPUT=$(cat)

# Run from the repo root, so a relative path (`lib/foo.ts`) is not judged against whatever the
# CWD happens to be. (Run from elsewhere, the hook missed tests that existed and denied wrongly —
# the .codex edition is the same.)
REPO_ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] && cd "$REPO_ROOT"

# If jq fails (non-JSON input, or no jq) there is nothing to judge by — deny explicitly instead
# of silently letting the edit through (fail-open). Real hook input is always valid JSON, and `?`
# turns a differently shaped tool_input into an empty value instead of an error, so there are no
# false positives. The block has to be deny JSON — exit 2 alone is only displayed.
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

# No file path — allowed
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# Editing a test file itself is allowed
# (a broad glob like *test*/*spec* would also pass innocent names such as latestLayout.ts or
#  aspectRatio.ts, so only real test-file shapes are exempt)
case "$FILE_PATH" in
  *.test.*|*.spec.*|*/__tests__/*|__tests__/*)
    exit 0
    ;;
esac

# Config/type/style files need no test — allowed
case "$FILE_PATH" in
  *.json|*.css|*.scss|*.md|*.yml|*.yaml|*.env*|*.config.*|*tailwind*|*postcss*|*next.config*|*tsconfig*)
    exit 0
    ;;
esac

# The types/ folder needs no test — allowed
case "$FILE_PATH" in
  */types/*|types/*|*/types.ts|*/types.d.ts)
    exit 0
    ;;
esac

# Next.js framework files are allowed (layout, page, loading, error, not-found, global styles)
case "$FILE_PATH" in
  */layout.tsx|*/layout.ts|*/page.tsx|*/page.ts|*/loading.tsx|*/error.tsx|*/not-found.tsx|*/globals.css)
    exit 0
    ;;
esac

# For lib/ or any other source file, check that a test file exists
case "$FILE_PATH" in
  *.ts|*.tsx|*.js|*.jsx)
    # Extract the file name
    DIR=$(dirname "$FILE_PATH")
    BASENAME=$(basename "$FILE_PATH" | sed -E 's/\.(ts|tsx|js|jsx)$//')

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
      # The file name is the only part of the reason that comes from the payload, so escape it: a
      # `"` or `\` in the name would turn the deny into invalid JSON, which the harness treats as a
      # non-blocking error — the edit would go straight through.
      NAME=${BASENAME//\\/\\\\}
      NAME=${NAME//\"/\\\"}
      NAME=${NAME//[[:cntrl:]]/ }
      cat << EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: no test file exists for '${NAME}'. Write the test before writing the implementation code. (Example test file: ${NAME}.test.ts)"
  }
}
EOF
    fi
    ;;
esac

exit 0
