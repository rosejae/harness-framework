#!/bin/bash
# Dangerous Command Guard — Codex PreToolUse[Bash]
# 파괴적/되돌리기 어려운 명령을 차단한다.
# Codex는 Bash tool_input.command 에 실행할 명령 문자열을 담아 전달한다.

INPUT=$(cat)
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')

if printf '%s' "$CMD" | grep -qE 'rm[[:space:]]+-rf|git[[:space:]]+push[[:space:]]+--force|git[[:space:]]+reset[[:space:]]+--hard|DROP[[:space:]]+TABLE'; then
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "위험한 명령어가 감지되어 차단되었습니다 (rm -rf / git push --force / git reset --hard / DROP TABLE). 꼭 필요하면 사람이 직접 실행하세요."
  }
}
EOF
fi

exit 0
