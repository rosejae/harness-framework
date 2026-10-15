#!/bin/bash
# Dangerous Command Guard — PreToolUse[Bash]. Shared by Claude and Codex.
# Blocks destructive / hard-to-undo commands.
#
# Both harnesses hand hook input in as stdin JSON, carry the Bash command in tool_input.command,
# and express a PreToolUse block as permissionDecision:"deny" JSON — hence one shared file.
# (Reading the command from an env var, or blocking with exit 1, silently disables it. The
#  Claude-side inline hook this file replaced blocked nothing for exactly those two reasons.)
#
# The net is deliberately wide (the old regex, which only caught literal-adjacent forms, let
# `git push -f`, `rm -fr` and `git -C . reset --hard` all through). A side effect is that the
# same literal inside a commit message or prose is blocked too — an intended trade-off. The corpus is scripts/test_hooks.py.
#
# Known limits (not chased with a regex):
#   - commands assembled from variables / `eval` (`C="rm -rf"; $C x`)

INPUT=$(cat)

# The regexes read the command the way the shell will run it, roughly — not its raw spelling
# (see segments() below): quotes removed, one simple command per line. So `"rm" -rf x` is
# `rm -rf x` (the shell strips the quotes before it runs the word), while in `rm x && ls -R`
# the -R belongs to ls and not to rm.
#
# rm  : a short-flag cluster holding r/R anywhere after the word — before or after the operands,
#       since GNU rm permutes (`-rf`/`-fr`/`-Rf`/`-r -f`/`x -rf`) — or `--r…` (--recursive and
#       its abbreviations). A long option that merely contains an r (`--force`, `--verbose`,
#       `--interactive=never`) is not a recursive flag. rm's left boundary leaves out `-` as well,
#       so docker's `--rm` option is not the command.
# git : skips the global options (`-C .`) before push/reset, then any words — options included,
#       `push -u origin main --force` — before the force or the hard reset. A push forces with a
#       short-flag cluster holding f (`-f`/`-uf`/`-fu`), `--force…`, or a `+refspec`
#       (`origin +main`), which forces that one ref. A push that deletes or overwrites remote refs
#       is held to the same line: `--mirror`, `--delete` and its `-d`
#       in any cluster, `--prune`, and a refspec with an empty source (`origin :branch`,
#       `:refs/tags/x`). A plain `src:dst` refspec and a lone `:` (push the matching branches) pass.
#       git takes any unambiguous prefix of a long option, so `--h`/`--ha`/`--har` are
#       `reset --hard` (git 2.43 resolves all three), and `--m…`, `--de…` and `--pru…` are push's
#       `--mirror`, `--delete` and `--prune` (`--d` and `--pr` are ambiguous with `--dry-run` and
#       `--progress`, and git refuses them). A prefix of `push --force` is ambiguous
#       (`--force-with-lease`, `--force-if-includes`, `--follow-tags`) and refused by git itself.
#       The reset prefixes need a right boundary, or `--help` would read as one.
# SQL : DROP TABLE in any case — SQL keywords are case-insensitive.
# The left word boundary is [^[:alnum:]_] (\b is not trustworthy in BSD grep) — a boundary that
# lets `confirm -rf` through while still catching `/bin/rm` and `;rm`.
DANGEROUS='(^|[^[:alnum:]_-])rm([[:space:]]+[^[:space:]]+)*[[:space:]]+(-[^-[:space:]]*[rR]|--r)'
DANGEROUS="$DANGEROUS"'|(^|[^[:alnum:]_])git([[:space:]]+-[^[:space:]]+([[:space:]]+[^-][^[:space:]]*)?)*[[:space:]]+(push([[:space:]]+[^[:space:]]+)*[[:space:]]+(-[^-[:space:]]*[fd]|--force|--de|--m|--pru|[+:][^[:space:]])|reset([[:space:]]+[^[:space:]]+)*[[:space:]]+(--hard|--h(a|ar)?([[:space:]]|$)))'
DANGEROUS="$DANGEROUS"'|(^|[^[:alnum:]_])[Dd][Rr][Oo][Pp][[:space:]]+[Tt][Aa][Bb][Ll][Ee]'

# The shell's own reading, closely enough to judge a word by: quotes removed, one simple command
# per line.
#   - A quote character goes, as the shell removes it before it runs the word.
#   - A separator INSIDE an inert quoted span — single quotes, or double quotes holding no `$(`
#     and no backtick — is text, so it becomes a blank rather than a line break: `grep -E
#     "a|b"` stays one command. A double-quoted span that holds a substitution stays
#     live, so `"$(rm -rf x)"` still splits off as a command of its own. An unterminated
#     quote leaves the rest live (fail-closed).
#   - `;` `&` `|` `(` `)` a backtick and a newline end a simple command; a `\`+newline does not.
# The walk is linear in the command's length — a heredoc of a few hundred KB stays well inside
# the hook timeout, and a timed-out hook lets the call through. So it never re-slices the rest
# of the string per quote: the command is cut into tokens once (each quote character a token of
# its own, \x1e as the cut) and the spans are rebuilt from those, in the C locale.
segments() {
  local LC_ALL=C
  local s=${1//$'\\\n'/ } q i j n inert
  local -a tok piece=() body
  s=${s//$'\x1e'/ }
  s=${s//\'/$'\x1e'\'$'\x1e'}
  s=${s//\"/$'\x1e'\"$'\x1e'}
  IFS=$'\x1e' read -rd '' -a tok <<< "$s"
  local IFS=
  n=${#tok[@]}
  for ((i = 0; i < n; i++)); do
    q=${tok[i]}
    if [[ $q != "'" && $q != '"' ]]; then piece+=("$q"); continue; fi
    body=()
    inert=1
    for ((j = i + 1; j < n; j++)); do
      [[ ${tok[j]} == "$q" ]] && break
      body+=("${tok[j]}")
      [[ $q == '"' && ( ${tok[j]} == *'$('* || ${tok[j]} == *'`'* ) ]] && inert=
    done
    if ((j < n)) && [[ -n $inert ]]; then
      s="${body[*]}"
      piece+=("${s//[;&|()\`$'\n']/ }")
    else
      piece+=("${body[@]}")
    fi
    i=$j
  done
  s="${piece[*]}"
  printf '%s\n' "${s//[;&|()\`$'\n']/$'\n'}"
}

# A quoted heredoc that a lone `cat` prints, or writes to a file, runs nothing: the shell expands
# nothing in it and cat only copies it out. So when the WHOLE command is that — `cat`, option
# letters, at most one `>`/`>>` to a plain file name on either side of a `<<'D'` opener, the body,
# and the first `D` line with nothing after it — only the opener line is read. Anything else
# keeps the body in the scan: an unquoted delimiter (it expands `$(…)`), `<<-` (a tab-indented
# `D` ends the body earlier than the first exact one), a `\` continuing the opener line, a pipe,
# a second command before or after it, or a target that is not a plain name (`>(bash)`).
lone_cat_heredoc() {
  local LC_ALL=C first rest probe body after delim
  local to='([[:space:]]*>>?[[:space:]]*[A-Za-z0-9_./~+@%,:=-]+)?'
  local re="^[[:space:]]*cat([[:space:]]+-[[:alnum:]-]*)*${to}[[:space:]]*<<[[:space:]]*('([A-Za-z_][A-Za-z0-9_]*)'|\"([A-Za-z_][A-Za-z0-9_]*)\")${to}[[:space:]]*\$"
  [[ $1 == *$'\n'* ]] || return 1
  first=${1%%$'\n'*}
  [[ $first =~ $re ]] || return 1
  delim=${BASH_REMATCH[4]}${BASH_REMATCH[5]}
  rest=${1:${#first}+1}
  probe=$'\n'$rest
  body=${probe%%$'\n'"$delim"$'\n'*}
  if [[ $body != "$probe" ]]; then
    after=${probe:${#body}+${#delim}+2}
  elif [[ $probe == *$'\n'"$delim" ]]; then
    after=
  else
    return 1
  fi
  [[ $after != *[^[:space:]]* ]]
}

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

# If jq is not on PATH CMD comes back empty
# and every dangerous command passes with no signal. Failing closed with a static deny would
# block `command -v jq` too and become a deadlock only a human can break — so read the raw stdin
# instead, decoded just enough for the same reading: an unescaped `"` delimits a JSON string, so
# it ends a line like any separator (`git push -f"}` would otherwise have no right boundary); an
# escaped `\"` is a quote inside the command; `\n` is a newline and `\t` a blank. The other JSON
# fields become lines of their own, so detection holds and only the net widens.
SCAN="$CMD"
if [ -z "$CMD" ]; then
  SCAN=${INPUT//\\\\/$'\x02'}
  SCAN=${SCAN//\\\"/$'\x03'}
  SCAN=${SCAN//\"/$'\n'}
  SCAN=${SCAN//$'\x03'/\"}
  SCAN=${SCAN//\\n/$'\n'}
  SCAN=${SCAN//\\t/ }
  SCAN=${SCAN//$'\x02'/\\}
  command -v jq >/dev/null 2>&1 || \
    echo "block-dangerous: jq not found; scanning the raw stdin instead (the net gets a little wider)." >&2
elif lone_cat_heredoc "$CMD"; then
  SCAN=${CMD%%$'\n'*}
fi

SEGMENTS=$(segments "$SCAN")

if printf '%s\n' "$SEGMENTS" | grep -qE "$DANGEROUS"; then
  cat <<'EOF'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "A dangerous command was detected and blocked (recursive delete / force push · remote ref deletion (--delete, :ref, --mirror, --prune) / hard reset / DROP TABLE). If it matched only because the literal appears in prose (a commit message, etc.) rather than as a command, reword it; a destructive operation that is really needed must be run by a human."
  }
}
EOF
fi

exit 0
