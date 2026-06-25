#!/bin/bash
# TDD Guard Hook — Codex PreToolUse[apply_patch|Edit|Write]
# 구현 코드를 작성/수정하려 할 때 해당 모듈의 테스트 파일이 먼저 존재하는지 검사한다.
# 테스트 없이 구현 코드를 만들려 하면 차단한다.
#
# Codex의 파일 편집은 apply_patch(tool_input.command)로 들어온다. 따라서 패치 본문의
# "*** Add File:" / "*** Update File:" 라인에서 대상 경로를 추출한다. (Edit/Write 형태로
# tool_input.file_path가 오는 경우도 함께 처리한다.)

INPUT=$(cat)

FILE_PATH=$(printf '%s' "$INPUT" | jq -r '.tool_input.file_path // empty')
CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty')

# 검사 대상 경로 수집
CANDIDATES=""
[ -n "$FILE_PATH" ] && CANDIDATES="$FILE_PATH"
if [ -n "$CMD" ]; then
  EXTRACTED=$(printf '%s\n' "$CMD" | grep -oE '\*\*\* (Add|Update) File: .+' | sed -E 's/^\*\*\* (Add|Update) File: //')
  CANDIDATES=$(printf '%s\n%s' "$CANDIDATES" "$EXTRACTED")
fi

deny() {
  local base="$1"
  cat <<EOF
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "TDD GUARD: '${base}'에 대한 테스트 파일이 존재하지 않습니다. 구현 코드를 작성하기 전에 테스트를 먼저 작성하세요. (테스트 파일 예: ${base}.test.ts)"
  }
}
EOF
  exit 0
}

while IFS= read -r FP; do
  [ -z "$FP" ] && continue

  # 테스트 파일 자체를 수정하는 건 허용
  case "$FP" in
    *test*|*spec*|*.test.*|*.spec.*|*__tests__*) continue ;;
  esac

  # 설정/타입/스타일/문서 파일은 테스트 불필요 — 허용
  case "$FP" in
    *.json|*.css|*.scss|*.md|*.yml|*.yaml|*.env*|*.config.*|*tailwind*|*postcss*|*next.config*|*tsconfig*) continue ;;
  esac

  # types/ 폴더는 테스트 불필요 — 허용
  case "$FP" in
    */types/*|*/types.ts|*/types.d.ts) continue ;;
  esac

  # Next.js 프레임워크 파일은 허용 (layout, page, loading, error, not-found, global styles)
  case "$FP" in
    */layout.tsx|*/layout.ts|*/page.tsx|*/page.ts|*/loading.tsx|*/error.tsx|*/not-found.tsx|*/globals.css) continue ;;
  esac

  # 소스 파일이면 테스트 파일 존재 여부 확인
  case "$FP" in
    *.ts|*.tsx|*.js|*.jsx)
      DIR=$(dirname "$FP")
      BASENAME=$(basename "$FP" | sed -E 's/\.(ts|tsx|js|jsx)$//')
      TEST_FOUND=false

      # 같은 폴더의 .test/.spec
      for EXT in ts tsx js jsx; do
        if [ -f "${DIR}/${BASENAME}.test.${EXT}" ] || [ -f "${DIR}/${BASENAME}.spec.${EXT}" ]; then
          TEST_FOUND=true; break
        fi
      done

      # __tests__ 폴더
      if [ "$TEST_FOUND" = false ]; then
        PARENT=$(dirname "$DIR")
        for EXT in ts tsx js jsx; do
          if [ -f "${PARENT}/__tests__/${BASENAME}.test.${EXT}" ] || [ -f "${DIR}/__tests__/${BASENAME}.test.${EXT}" ]; then
            TEST_FOUND=true; break
          fi
        done
      fi

      # src/__tests__/ 루트 테스트 폴더
      if [ "$TEST_FOUND" = false ]; then
        PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || echo ".")
        for EXT in ts tsx js jsx; do
          if [ -f "${PROJECT_ROOT}/src/__tests__/${BASENAME}.test.${EXT}" ]; then
            TEST_FOUND=true; break
          fi
        done
      fi

      if [ "$TEST_FOUND" = false ]; then
        deny "$BASENAME"
      fi
      ;;
  esac
done <<< "$CANDIDATES"

exit 0
