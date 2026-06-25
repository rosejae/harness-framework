#!/bin/bash
# Stop Hook — Codex. 턴(=step)이 끝날 때 lint/build/test 를 실행해 검증한다.
# 실패하면 결과를 피드백하며 1회 자가교정을 유도하고(decision: block), 무한 루프는 막는다.
#
# Codex의 Stop 훅은 종료 코드 0일 때 출력이 있으면 반드시 JSON 이어야 한다.
# 통과 시에는 아무것도 출력하지 않고 0으로 끝낸다(= 정상 종료).

INPUT=$(cat)
STOP_ACTIVE=$(printf '%s' "$INPUT" | jq -r '.stop_hook_active // false')
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")

# 검증할 npm 스크립트가 없으면(예: 템플릿 상태) 건너뛴다.
if [ ! -f "$ROOT/package.json" ]; then
  exit 0
fi

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
