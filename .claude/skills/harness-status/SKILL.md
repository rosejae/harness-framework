---
description: 백그라운드에서 돌고 있는 harness execute.py 실행의 진행 상황을 읽기 전용으로 보고한다.
argument-hint: "[task-name (기본값: phases/index.json에서 진행 중인 phase, 없으면 0-mvp)]"
allowed-tools: Bash, Read, Grep, Glob
---

**harness `execute.py`(백그라운드 step 실행)의 현재 진행 상황**을 **읽기 전용으로** 보고하라.
step을 직접 실행하지 마라.

## 대상 task 결정 (`$ARGUMENTS`)

- 인자가 있으면 그 task-name을 쓴다.
- 비어 있으면 `phases/index.json`에서 `status`가 `pending`/`error`/`blocked`인 phase의 `dir`을 고르고,
  없으면 마지막 phase를 쓴다 (phase가 하나도 없으면 `0-mvp`).

## 절차

1. 스냅샷을 찍는다:

   ```
   python3 scripts/execute.py <task-name> --status
   ```

   이 한 줄이 **백그라운드 실행이 살아 있는지(`Run:` 줄, pid 포함)** + step별 상태 표 +
   `feat-<task>` 브랜치의 커밋 수 + 요약을 출력한다. 보고에 필요한 것은 이것이 전부다 —
   `index.json`을 직접 읽지 말고, git log를 손으로 조립하지 말고, 살아 있는지 보려고
   `pgrep`/`ps`를 덧붙이지 마라 (`--status`가 단일 출처다).

2. **완료된 step이 무엇을 남겼는지**도 물었다면 한 줄을 더 쓴다:

   ```
   python3 scripts/execute.py <task-name> --summary [step|N]
   ```

   step의 summary를 **자르지 않고 전부** 출력한다 (`--status` 표는 앞 60자만 보여준다) —
   `index.json`을 손으로 파싱하지 마라. 선택자는 **step 번호(0부터 시작 — `--status` 표에 나온 번호)
   또는 step 이름**이고, 생략하면 완료된 step 전부다.

3. 이전 보고와 비교해 **바뀐 점**이 있으면 강조한다: 새로 완료된 step, 새 커밋,
   `error`/`blocked`로의 전이. 첫 보고라면 표 전체를 보여준다.
4. `error`/`blocked` step이 있으면 `/harness` skill §E "에러 복구"의 절차를 한 줄로 안내한다
   (error → status를 `pending`으로 되돌리고 `error_message` 삭제 후 재실행; blocked → 사유를 해결한 뒤
   같은 방법으로 `blocked_reason` 삭제 후 재실행).

## 주기적 업데이트

"N분마다 알려줘"가 필요하면 이 지시를 매번 다시 입력하지 말고 **`/loop`**을 쓴다:

```
/loop 3m /harness-status 0-mvp
```

execute.py를 백그라운드로 실행했다면 완료 시 자동으로 알림이 온다. 기다리려고 짧은 주기의
`until grep … sleep` 폴링 루프를 만들지 마라 (`/harness` skill §E 참고).
