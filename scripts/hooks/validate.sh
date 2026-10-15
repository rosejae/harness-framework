#!/bin/bash
# Stop Hook — shared by Claude and Codex. 턴(=step)이 끝날 때 lint/build/test 를 실행해 검증한다.
# 실패하면 결과를 피드백하며 1회 자가교정을 유도하고(decision: block), 무한 루프는 막는다.
#
# 통과 시에는 아무것도 출력하지 않고 0으로 끝낸다(= 정상 종료).
# 종료 코드로는 Stop 을 막을 수 없다(0이 아닌 종료는 보여지기만 하는 non-blocking error).
# 자가교정을 실제로 유도하는 것은 {"decision":"block"} JSON 뿐이다.

INPUT=$(cat)
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")

# 검증할 npm 스크립트가 없으면(예: 템플릿 상태) 건너뛴다.
if [ ! -f "$ROOT/package.json" ]; then
  exit 0
fi

# jq 가 PATH 에 없으면 stop_hook_active 도, block JSON 도 만들 수 없다. 여기서 0으로 끝내면 턴 전체가
# 검증 없이 끝나므로, 검증을 돌리지 않고 사람에게 넘긴다(사유는 정적 heredoc 이라 이스케이프가 필요 없다).
# 단, 무한 자가교정 루프를 막기 위해 stop_hook_active 는 jq 없이 다시 확인한다.
if ! command -v jq >/dev/null 2>&1; then
  if printf '%s' "$INPUT" | grep -q '"stop_hook_active"[[:space:]]*:[[:space:]]*true'; then
    exit 0
  fi
  cat <<'EOF'
{
  "decision": "block",
  "reason": "jq 를 찾을 수 없어 lint/build/test 결과를 보고할 수 없다(PATH 확인 필요). 검증이 돌지 않았으므로 npm run lint && npm run build && npm run test 를 직접 실행해 통과를 확인하라."
}
EOF
  exit 0
fi

STOP_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false' 2>/dev/null)

# 이미 한 번 자가교정을 유도했다면 무한 루프 방지를 위해 그대로 종료한다.
if [ "$STOP_ACTIVE" = "true" ]; then
  exit 0
fi

OUT=$(cd "$ROOT" && npm run lint 2>&1 && npm run build 2>&1 && npm run test 2>&1)
STATUS=$?

if [ "$STATUS" -ne 0 ]; then
  REASON=$(printf 'lint/build/test 검증에 실패했다. 아래 출력을 보고 원인을 수정한 뒤 다시 검증하라:\n\n%s' "$OUT" | tail -c 4000)
  jq -n --arg r "$REASON" '{decision: "block", reason: $r}'
fi

exit 0
