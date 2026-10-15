# Canonical template for `phases/{task-name}/step{N}.md`

A step file is **read on its own in an independent Codex session** — there is no conversation context, so everything it needs must be in the file. Use the skeleton below as is and fill in only the `{}` parts (design principles: `SKILL.md` §C).

````markdown
# Step {N}: {name}

## Files to read

First read the files below to understand the project's architecture and design intent:

- `/docs/ARCHITECTURE.md`
- `/docs/ADR.md`
- {paths of files created/modified by previous steps}

Read the code produced by previous steps carefully, and work only after you understand the design intent.

## Task

{Concrete implementation instructions, including file paths, class/function signatures and a description of the logic.
Give code snippets only at the interface/signature level and leave the implementation to the agent.
But state clearly the core rules that must not drift from the design intent.}

## Acceptance Criteria

```bash
npm run build   # no compile errors
npm test        # tests pass
```

## Verification

1. Run the AC commands above.
2. Check the architecture checklist:
   - Does it follow the ARCHITECTURE.md directory structure?
   - Does it stay within the ADR tech stack?
   - Does it violate no CLAUDE.md CRITICAL rule?
3. Depending on the result, update this step in `phases/{task-name}/index.json`:
   - Success → `"status": "completed"`, `"summary": "one-line summary of the output"`
   - Still failing after 3 fix attempts → `"status": "error"`, `"error_message": "concrete error details"`
   - User intervention needed (API key, external auth, manual setup, etc.) → `"status": "blocked"`, `"blocked_reason": "concrete reason"`, then stop immediately

## Don'ts

- {What not to do in this step, in the form "Do not do X. Reason: Y"}
- Do not break existing tests
````
