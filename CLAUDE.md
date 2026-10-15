# Project: {project name}

**This file holds only what is true no matter which file you touch.** Area rules live in `.claude/rules/` — Claude Code loads the matching rule by itself when it reads a file in that area, and Codex gets it from `.codex/hooks/rules-context.py`. Codex reads this file as its project doc — `AGENTS.md` is a symbolic link to it, so do not duplicate rules into a second copy.

**Language**: write English everywhere — code, comments, docs, skills, hook messages, commit messages and step files. The only exception is end-user-facing product UI copy (buttons, in-app error messages), which stays Korean.

## Tech stack
- {framework (e.g. Next.js 15)}
- {language (e.g. TypeScript strict mode)}
- {styling (e.g. Tailwind CSS)}

## Architecture rules
- CRITICAL: {rule 1 that must never be broken (e.g. all API logic is handled only in app/api/ route handlers)}
- CRITICAL: {rule 2 that must never be broken (e.g. client components must not call external APIs directly)}
- {general rule (e.g. components go in components/, types are split out into types/)}

## Development process
- CRITICAL: when implementing a new feature, always write the tests first, then write the implementation that makes them pass (TDD)
- Commit messages follow the conventional commits format (feat:, fix:, docs:, refactor:)

## Commands
npm run dev      # dev server
npm run build    # production build
npm run lint     # ESLint
npm run test     # tests

## Area rules (`.claude/rules/`)

Claude Code loads the matching rule by itself when it reads a file in that area. Codex gets it from `.codex/hooks/rules-context.py` — once per agent, and not re-sent after compaction.

| What you touch | Rule |
|---|---|
| {area (e.g. `app/`, `components/`)} | {`.claude/rules/<name>.md` — one-line summary} |

## Canonical documents

Nothing below is in your prompt. Open the ones your change touches before you write.

- [`docs/PRD.md`](./docs/PRD.md) — goal, users, core features, what the MVP leaves out.
- [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — directory structure, patterns, data flow, state management.
- [`docs/ADR.md`](./docs/ADR.md) — technical decisions, their reasons and trade-offs.
- [`docs/UI_GUIDE.md`](./docs/UI_GUIDE.md) — UI design principles, AI-slop anti-patterns, colors.
