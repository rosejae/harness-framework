---
description: Review this project's changes against architecture, tech stack, tests, CRITICAL rules and build. Looks at the diff you are working on — not the built-in `/review` for GitHub PRs.
argument-hint: "[review scope (empty means all current changes)]"
allowed-tools: Bash, Read, Grep, Glob
---

Review this project's changes.

First read these documents:
- `/CLAUDE.md`
- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`

Then look at the changed files and verify them against the checklist below:

## Checklist

1. **Architecture compliance**: Does it follow the directory structure defined in ARCHITECTURE.md?
2. **Tech stack compliance**: Does it stay within the technology choices defined in the ADR?
3. **Tests exist**: Are tests written for the new functionality?
4. **CRITICAL rules**: Does it violate no CRITICAL rule in CLAUDE.md?
5. **Builds**: Do the build commands pass without errors?

## Output format

| Item | Result | Notes |
|------|------|------|
| Architecture compliance | ✅/❌ | {details} |
| Tech stack compliance | ✅/❌ | {details} |
| Tests exist | ✅/❌ | {details} |
| CRITICAL rules | ✅/❌ | {details} |
| Builds | ✅/❌ | {details} |

If there are violations, propose concrete fixes.
