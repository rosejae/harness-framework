#!/usr/bin/env python3
"""scripts/rules_match.py — path -> `.claude/rules/` resolver.

The single matcher `.codex/hooks/rules-context.py` asks which rules govern a set of repo-relative paths.
Claude Code's `paths:` semantics: `*`/`?`/`[seq]` match inside one segment, only a whole-segment `**`
spans directories, `{a,b}` braces are expanded. A missing `.claude/rules/` directory yields no rules.
"""

import fnmatch
import re
from pathlib import Path

RULES_DIR = Path(".claude") / "rules"


def rule_files(root):
    """Every rule, recursively. `.claude/rules/` is a tree, and a subdirectory is not a new
    tier: Claude Code discovers `**/*.md` under it with no opt-out, so every machine that reads
    the directory must read it the same way. A non-recursive glob here would leave a nested
    rule loaded for Claude while Codex never sees it."""
    return sorted((root / RULES_DIR).rglob("*.md"))


def rule_name(root, path):
    """A rule's name is its path relative to the rules dir, without the suffix — so a flat
    `worm.md` and a nested `adr/worm.md` register as two distinct rules."""
    return path.relative_to(root / RULES_DIR).with_suffix("").as_posix()


def split_inline_list(body):
    """The items of an inline `[a, "b", 'c{d,e}']` list, read the way YAML reads it closely
    enough for this repo: a comma inside quotes or `{}` does not split, and one pair of quotes
    around an item is dropped. Claude Code reads a rule's `paths:` as YAML, so a bare `split(",")`
    left `"app/**"` quoted (no file matches it) and cut `"lib/{a,b}.ts"` in two."""
    items, cur, quote, depth = [], [], None, 0
    for ch in body:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "{}":
            depth = depth + 1 if ch == "{" else max(depth - 1, 0)
        elif ch == "," and depth == 0:
            items.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    items.append("".join(cur))
    out = []
    for item in (i.strip() for i in items):
        if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
            item = item[1:-1]
        if item:
            out.append(item)
    return out


def parse_front_matter(text):
    """The minimal YAML subset: `key: value` / `key: [a, b]` only."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end < 0:
        return {}
    out = {}
    for line in text[4:end].splitlines():
        if not line.strip() or line.startswith((" ", "\t", "#")) or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            out[key.strip()] = split_inline_list(raw[1:-1])
        else:
            out[key.strip()] = raw.strip('"').strip("'")
    return out


def rule_globs(text):
    """The `paths:` glob list from a rule's frontmatter. Inline (`paths: [a, b]`) and block form both."""
    value = parse_front_matter(text).get("paths")
    if isinstance(value, list):
        return value
    if value is None:
        return []
    globs, seen = [], False
    for line in text.split("\n---", 1)[0].splitlines():
        if line.rstrip() == "paths:":
            seen = True
        elif seen:
            item = re.match(r"\s+-\s*(.+)", line)
            if not item:
                break
            globs.append(item.group(1).strip().strip('"').strip("'"))
    return globs


def expand_braces(pattern: str) -> list[str]:
    """`fnmatch` translates only `*`, `?` and `[seq]` — it does not expand `{a,b}`, which a
    rule glob may use (`lib/{a,b}.ts`). Expand them here or that rule silently maps onto zero
    files."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    out = []
    for alt in match.group(1).split(","):
        out.extend(expand_braces(pattern[: match.start()] + alt + pattern[match.end() :]))
    return out


def glob_full_match(pattern: str, path: str) -> bool:
    """Claude Code's `paths:` semantics, not bare `fnmatch`'s, whose `*` crosses `/` and would
    make a root-only `*.md` claim every nested markdown file. `*`, `?` and `[seq]` match inside
    one path segment (fnmatch, applied a segment at a time), and only a `**` that is a whole
    segment spans directories — zero or more, so `lib/**` claims `lib/a/b.ts` and
    `**/*.test.ts` claims a root `a.test.ts`."""

    def match(parts, segments):
        if not parts:
            return not segments
        if parts[0] == "**":
            return any(match(parts[1:], segments[i:]) for i in range(len(segments) + 1))
        return (
            bool(segments)
            and fnmatch.fnmatchcase(segments[0], parts[0])
            and match(parts[1:], segments[1:])
        )

    return match(pattern.split("/"), path.split("/"))


def glob_matches(pattern: str, paths) -> list[str]:
    alts = expand_braces(pattern)
    return [p for p in paths if any(glob_full_match(a, p) for a in alts)]


def glob_specificity(pattern):
    """How literal a glob is: the length of its text before the first wildcard, measured
    **after** brace expansion. Unexpanded, `lib/{nestParams,nestSolver}.ts` scores 4 — the
    `lib/` ahead of its `{` — and ties with `lib/**`, which costs the narrow rule its place in
    front of the broad one. Expanded, its longest alternative scores 17."""
    return max(len(re.split(r"[*?\[]", alt, maxsplit=1)[0]) for alt in expand_braces(pattern))


def rules_for_paths(root, relpaths):
    """Every rule whose `paths:` glob matches one of these repo-relative paths, most specific
    first. This is the only matcher: anything else that needs the answer imports it."""
    ranked = []
    for path in rule_files(root):
        globs = rule_globs(path.read_text(encoding="utf-8"))
        matched = [g for g in globs if glob_matches(g, relpaths)]
        if matched:
            ranked.append((-max(glob_specificity(g) for g in matched), rule_name(root, path), path))
    return [path for _, _, path in sorted(ranked)]
