이 프로젝트는 Harness 프레임워크를 사용한다. 아래 워크플로우에 따라 작업을 진행하라.

---

## 워크플로우

### A. 탐색

`/docs/` 하위 문서(PRD, ARCHITECTURE, ADR 등)를 읽고 프로젝트의 기획·아키텍처·설계 의도를 파악한다. 필요시 Explore 에이전트를 병렬로 사용한다.

### B. 논의

구현을 위해 구체화하거나 기술적으로 결정해야 할 사항이 있으면 사용자에게 제시하고 논의한다.

### C. Step 설계

사용자가 구현 계획 작성을 지시하면 여러 step으로 나뉜 초안을 작성해 피드백을 요청한다.

설계 원칙:

1. **Scope 최소화** — 하나의 step에서 하나의 레이어 또는 모듈만 다룬다. 여러 모듈을 동시에 수정해야 하면 step을 쪼갠다.
2. **자기완결성** — 각 step 파일은 독립된 Codex 세션(`codex exec`)에서 실행된다. "이전 대화에서 논의한 바와 같이" 같은 외부 참조는 금지한다. 필요한 정보는 전부 파일 안에 적는다.
3. **사전 준비 강제** — 관련 문서 경로와 이전 step에서 생성/수정된 파일 경로를 명시한다. 세션이 코드를 읽고 맥락을 파악한 뒤 작업하도록 유도한다.
4. **시그니처 수준 지시** — 함수/클래스의 인터페이스만 제시하고 내부 구현은 에이전트 재량에 맡긴다. 단, 설계 의도에서 벗어나면 안 되는 핵심 규칙(멱등성, 보안, 데이터 무결성 등)은 반드시 명시한다.
5. **AC는 실행 가능한 커맨드** — "~가 동작해야 한다" 같은 추상적 서술이 아닌 `npm run build && npm test` 같은 실제 실행 가능한 검증 커맨드를 포함한다.
6. **주의사항은 구체적으로** — "조심해라" 대신 "X를 하지 마라. 이유: Y" 형식으로 적는다.
7. **네이밍** — step name은 kebab-case slug로, 해당 step의 핵심 모듈/작업을 한두 단어로 표현한다 (예: `project-setup`, `api-layer`, `auth-flow`).

### D. 파일 생성

사용자가 승인하면 아래 파일들을 생성한다.

#### D-1. `phases/index.json` (전체 현황)

여러 task를 관리하는 top-level 인덱스. 이미 존재하면 `phases` 배열에 새 항목을 추가한다.

```json
{
  "phases": [
    {
      "dir": "0-mvp",
      "status": "pending"
    }
  ]
}
```

- `dir`: task 디렉토리명.
- `status`: `"pending"` | `"completed"` | `"error"` | `"blocked"`. execute.py가 실행 중 자동으로 업데이트한다.
- 타임스탬프(`completed_at`, `failed_at`, `blocked_at`)는 execute.py가 상태 변경 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-2. `phases/{task-name}/index.json` (task 상세)

```json
{
  "project": "<프로젝트명>",
  "phase": "<task-name>",
  "steps": [
    { "step": 0, "name": "project-setup", "status": "pending" },
    { "step": 1, "name": "core-types", "status": "pending" },
    { "step": 2, "name": "api-layer", "status": "pending" }
  ]
}
```

필드 규칙:

- `project`: 프로젝트명 (CLAUDE.md 참조).
- `phase`: task 이름. 디렉토리명과 일치시킨다.
- `steps[].step`: 0부터 시작하는 순번.
- `steps[].name`: kebab-case slug.
- `steps[].status`: 초기값은 모두 `"pending"`.
- `steps[].timeout_s` (선택): 해당 step의 Codex 세션 제한 시간(초). 생략하면 1800초. 시간을 넘기면 세션을 종료하고 실패로 간주해 재시도한다.

상태 전이와 자동 기록 필드:

| 전이 | 기록되는 필드 | 기록 주체 |
|------|-------------|----------|
| → `completed` | `completed_at`, `summary` | Codex 세션 (summary), execute.py (timestamp) |
| → `error` | `failed_at`, `error_message` | Codex 세션 (message), execute.py (timestamp) |
| → `blocked` | `blocked_at`, `blocked_reason` | Codex 세션 (reason), execute.py (timestamp) |

`summary`는 step 완료 시 산출물을 한 줄로 요약한 것으로, execute.py가 다음 step 프롬프트에 컨텍스트로 누적 전달한다. 따라서 다음 step에 유용한 정보(생성된 파일, 핵심 결정 등)를 담아야 한다.

`created_at`은 execute.py가 최초 실행 시 task 레벨에 한 번만 기록한다. step 레벨의 `started_at`도 execute.py가 각 step 시작 시 자동 기록한다. 생성 시 넣지 않는다.

#### D-3. `phases/{task-name}/step{N}.md` (각 step마다 1개)

```markdown
# Step {N}: {이름}

## 읽어야 할 파일

먼저 아래 파일들을 읽고 프로젝트의 아키텍처와 설계 의도를 파악하라:

- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`
- {이전 step에서 생성/수정된 파일 경로}

이전 step에서 만들어진 코드를 꼼꼼히 읽고, 설계 의도를 이해한 뒤 작업하라.

## 작업

{구체적인 구현 지시. 파일 경로, 클래스/함수 시그니처, 로직 설명을 포함.
코드 스니펫은 인터페이스/시그니처 수준만 제시하고, 구현체는 에이전트에게 맡겨라.
단, 설계 의도에서 벗어나면 안 되는 핵심 규칙은 명확히 박아넣어라.}

## Acceptance Criteria

```bash
npm run build   # 컴파일 에러 없음
npm test        # 테스트 통과
```

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 아키텍처 체크리스트를 확인한다:
   - ARCHITECTURE.md 디렉토리 구조를 따르는가?
   - ADR 기술 스택을 벗어나지 않았는가?
   - CLAUDE.md CRITICAL 규칙을 위반하지 않았는가?
3. 결과에 따라 `phases/{task-name}/index.json`의 해당 step을 업데이트한다:
   - 성공 → `"status": "completed"`, `"summary": "산출물 한 줄 요약"`
   - 수정 3회 시도 후에도 실패 → `"status": "error"`, `"error_message": "구체적 에러 내용"`
   - 사용자 개입 필요 (API 키, 외부 인증, 수동 설정 등) → `"status": "blocked"`, `"blocked_reason": "구체적 사유"` 후 즉시 중단

## 금지사항

- {이 step에서 하지 말아야 할 것. "X를 하지 마라. 이유: Y" 형식}
- 기존 테스트를 깨뜨리지 마라
```

### E. 실행

```bash
python3 scripts/execute.py {task-name}        # 순차 실행
python3 scripts/execute.py {task-name} --push  # 실행 후 push
```

execute.py가 자동으로 처리하는 것:

- step 실행 — 각 step을 `codex exec`(비대화형)로 실행. 구현·AC 검증·커밋은 Codex 세션이 수행한다.
- 가드레일 전달 — **러너는 `CLAUDE.md`나 `docs/` 문서를 프롬프트에 주입하지 않는다.** step 파일 앞에는 역할 소개 한 줄, 완료된 step의 summary, 재시도 시 이전 에러, 작업 규칙만 붙인다. step 프롬프트에는 작업 규칙, 완료된 step의 summary, step 파일만 들어간다. Codex는 `AGENTS.md` 심볼릭 링크를 통해 `CLAUDE.md`를 프로젝트 문서로 읽고, 거기 적힌 링크를 따라 필요한 문서를 직접 연다. 영역 규칙(`.claude/rules/*.md`)은 `.codex/hooks/rules-context.py`가 해당 파일을 읽거나 수정할 때 주입한다. step에 특화된 파일은 step 파일의 "읽어야 할 파일" 섹션에 적는다.
- Codex 훅 — `.codex/hooks.json`이 매 step의 Codex 세션에 가드레일을 건다: 테스트 없는 구현 차단(TDD), 위험 명령 차단, 종료 시 lint/build/test 검증. execute.py는 `--dangerously-bypass-hook-trust`로 in-repo 훅을 신뢰한다.
- `feat-{task-name}` 브랜치 생성/checkout
- 컨텍스트 누적 — 완료된 step의 summary를 다음 step 프롬프트에 전달
- 자가 교정 — 실패 시 최대 3회 재시도하며, 이전 에러 메시지를 프롬프트에 피드백 (제한 시간 초과도 실패로 취급)
- 2단계 커밋 — 코드 변경(`feat`)과 메타데이터(`chore`)를 분리 커밋. 재시도를 모두 소진한 step은 코드를 커밋하지 않고 메타데이터만 커밋한다
- 타임스탬프 — started_at, completed_at, failed_at, blocked_at 자동 기록

진행 상황 확인 (백그라운드 실행 중):

- execute.py는 `run_in_background`로 실행한다. 완료되면 **자동으로 알림이 오므로** `until grep … sleep 5; done` 같은 짧은 주기의 폴링 루프를 만들지 마라. 폴링 루프는 좀비 프로세스를 남기고 컨텍스트를 낭비한다.
- 중간 진행 상황은 읽기 전용 스냅샷으로 본다: `python3 scripts/execute.py <task> --status` (step별 상태 표, 실행 중 여부, `feat-<task>` 커밋 수, 요약).
- 이 표는 각 step의 summary를 앞 60자로 자른다. 완료된 step이 남긴 내용 전체가 필요하면 `index.json`을 직접 파싱하지 말고 `python3 scripts/execute.py <task> --summary [step|N]`을 쓴다 (선택자는 표에 나온 0부터 시작하는 step 번호나 step 이름, 생략하면 완료된 step 전부).

에러 복구:

- **error 발생 시**: `phases/{task-name}/index.json`에서 해당 step의 `status`를 `"pending"`으로 바꾸고 `error_message`를 삭제한 뒤 재실행한다.
- **blocked 발생 시**: `blocked_reason`에 적힌 사유를 해결한 뒤, `status`를 `"pending"`으로 바꾸고 `blocked_reason`을 삭제한 뒤 재실행한다.
