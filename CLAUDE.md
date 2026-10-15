# 프로젝트: {프로젝트명}

**이 파일에는 어떤 파일을 건드리든 항상 참인 내용만 둔다.** 영역별 규칙은 `.claude/rules/`에 있다 — Claude Code는 해당 영역의 파일을 읽을 때 일치하는 규칙을 스스로 로드하고, Codex에는 `.codex/hooks/rules-context.py`가 주입한다. Codex는 이 파일을 프로젝트 문서로 읽는다 — `AGENTS.md`는 이 파일의 심볼릭 링크이므로 규칙을 두 번째 사본에 중복하지 마라.

## 기술 스택
- {프레임워크 (예: Next.js 15)}
- {언어 (예: TypeScript strict mode)}
- {스타일링 (예: Tailwind CSS)}

## 아키텍처 규칙
- CRITICAL: {절대 지켜야 할 규칙 1 (예: 모든 API 로직은 app/api/ 라우트 핸들러에서만 처리)}
- CRITICAL: {절대 지켜야 할 규칙 2 (예: 클라이언트 컴포넌트에서 직접 외부 API를 호출하지 말 것)}
- {일반 규칙 (예: 컴포넌트는 components/ 폴더에, 타입은 types/ 폴더에 분리)}

## 개발 프로세스
- CRITICAL: 새 기능 구현 시 반드시 테스트를 먼저 작성하고, 테스트가 통과하는 구현을 작성할 것 (TDD)
- 커밋 메시지는 conventional commits 형식을 따를 것 (feat:, fix:, docs:, refactor:)

## 명령어
npm run dev      # 개발 서버
npm run build    # 프로덕션 빌드
npm run lint     # ESLint
npm run test     # 테스트

## 영역 규칙 (`.claude/rules/`)

Claude Code는 해당 영역의 파일을 읽을 때 일치하는 규칙을 스스로 로드한다. Codex에는 `.codex/hooks/rules-context.py`가 주입한다 — 에이전트당 1회이며, compaction 후에는 다시 보내지 않는다.

| 건드리는 곳 | 규칙 |
|---|---|
| {영역 (예: `app/`, `components/`)} | {`.claude/rules/<name>.md` — 한 줄 요약} |

## 기준 문서

아래 문서는 프롬프트에 들어 있지 않다. 변경이 닿는 문서를 작성 전에 직접 열어라.

- [`docs/PRD.md`](./docs/PRD.md) — 목표, 사용자, 핵심 기능, MVP 제외 사항.
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — 디렉토리 구조, 패턴, 데이터 흐름, 상태 관리.
- [`docs/ADR.md`](./docs/ADR.md) — 기술 결정과 그 이유·트레이드오프.
- [`docs/UI_GUIDE.md`](./docs/UI_GUIDE.md) — UI 디자인 원칙, AI 슬롭 안티패턴, 색상.
