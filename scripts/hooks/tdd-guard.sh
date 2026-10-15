#!/bin/bash
# TDD Guard Hook — PreToolUse[Edit|Write]
# 구현 코드를 작성하려 할 때, 해당 모듈의 테스트 파일이 먼저 존재하는지 체크.
# 테스트 없이 구현 코드를 작성하려 하면 차단.

INPUT=$(cat)

# 저장소 루트에서 실행한다 — 상대 경로(`lib/foo.ts`)를 그때그때 다른 CWD 기준으로 판정하지 않도록.
# (루트가 아닌 곳에서 훅이 돌면 실제로 있는 테스트를 못 찾고 잘못 차단했다 — .codex 판도 같다)
REPO_ROOT=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] && cd "$REPO_ROOT"

# jq가 실패하면(비-JSON 입력이거나 jq 부재) 판정할 근거가 없다 — 조용히 통과시키지 않고(fail-open)
# 명시적으로 차단한다. 정상 훅 입력은 항상 유효한 JSON이고, `?`는 모양이 다른 tool_input을
# 에러 대신 빈 값으로 바꾸므로 오탐은 없다. 차단은 deny JSON이어야 한다 — exit 2만으로는 표시만 된다.
if ! FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path? // empty' 2>/dev/null); then
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: 훅 입력을 JSON으로 파싱할 수 없어(비-JSON 입력이거나 jq 부재) 테스트 선행 여부를 판정할 수 없습니다. 근거 없이 통과시키지 않습니다."
  }
}
EOF
  echo "TDD GUARD: 훅 입력을 JSON으로 파싱할 수 없습니다." >&2
  exit 2
fi

# 파일 경로가 없으면 통과
if [ -z "$FILE_PATH" ]; then
  exit 0
fi

# 테스트 파일 자체를 수정하는 건 허용
# (*test*/*spec* 같은 넓은 glob은 latestLayout.ts, aspectRatio.ts 같은 무고한 이름까지
#  통과시키므로 실제 테스트 파일 모양만 면제한다)
case "$FILE_PATH" in
  *.test.*|*.spec.*|*/__tests__/*|__tests__/*)
    exit 0
    ;;
esac

# 설정/타입/스타일 파일은 테스트 불필요 — 허용
case "$FILE_PATH" in
  *.json|*.css|*.scss|*.md|*.yml|*.yaml|*.env*|*.config.*|*tailwind*|*postcss*|*next.config*|*tsconfig*)
    exit 0
    ;;
esac

# types/ 폴더는 테스트 불필요 — 허용
case "$FILE_PATH" in
  */types/*|types/*|*/types.ts|*/types.d.ts)
    exit 0
    ;;
esac

# Next.js 프레임워크 파일은 허용 (layout, page, loading, error, not-found, global styles)
case "$FILE_PATH" in
  */layout.tsx|*/layout.ts|*/page.tsx|*/page.ts|*/loading.tsx|*/error.tsx|*/not-found.tsx|*/globals.css)
    exit 0
    ;;
esac

# lib/ 또는 소스 파일이면 테스트 파일 존재 여부 확인
case "$FILE_PATH" in
  *.ts|*.tsx|*.js|*.jsx)
    # 파일명 추출
    DIR=$(dirname "$FILE_PATH")
    BASENAME=$(basename "$FILE_PATH" | sed -E 's/\.(ts|tsx|js|jsx)$//')

    # 테스트 파일 후보: 같은 폴더, 그 폴더의 __tests__, 한 단계 위의 __tests__, 루트의
    # src/__tests__ — vitest 기본 수집 대상인 *.{test,spec}.* 를 모두 인정한다.
    # (src/__tests__ 는 CWD가 저장소 루트이므로 상대 경로로 충분하다)
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
      # 사유 문구에서 payload에서 온 유일한 부분이 파일명이므로 이스케이프한다: 파일명의 `"` 나 `\` 가
      # deny를 잘못된 JSON으로 만들면 하네스에는 비차단 오류일 뿐이라 편집이 그대로 통과한다.
      NAME=${BASENAME//\\/\\\\}
      NAME=${NAME//\"/\\\"}
      NAME=${NAME//[[:cntrl:]]/ }
      cat << EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: '${NAME}'에 대한 테스트 파일이 존재하지 않습니다. 구현 코드를 작성하기 전에 테스트를 먼저 작성하세요. (테스트 파일 예: ${NAME}.test.ts)"
  }
}
EOF
    fi
    ;;
esac

exit 0
