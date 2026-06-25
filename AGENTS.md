# AGENTS.md

> 이 파일은 단독 지침서가 아니다. 에이전트 규칙의 **단일 출처(Single Source of Truth)는
> [`CLAUDE.md`](./CLAUDE.md)** 이며, AGENTS.md는 이를 가리키는 포인터일 뿐이다.

## 지침

이 프로젝트의 모든 에이전트 규칙(기술 스택, 아키텍처 규칙, 개발 프로세스, 명령어)은
**[`CLAUDE.md`](./CLAUDE.md) 를 그대로 따른다.**

- 작업을 시작하기 전에 반드시 [`CLAUDE.md`](./CLAUDE.md) 를 읽고 그 규칙을 적용하라.
- 규칙을 이 파일에 중복해서 적지 마라. 변경은 **항상 `CLAUDE.md`에서만** 한다.

Harness 실행(`scripts/execute.py`)은 매 step 프롬프트에 `CLAUDE.md` 내용을 가드레일로 자동 주입한다.
