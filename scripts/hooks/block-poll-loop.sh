#!/bin/bash
# Polling Loop Guard — PreToolUse[Bash]. Shared by Claude and Codex.
# Blocks sleep polling that spins waiting for completion (background work already notifies on
# completion — see the progress-reporting part of /harness §E).
#
# Same contract as block-dangerous.sh but a separate file: this is waste, not destruction
# (zombie processes + context burn), so the deny reason and the governing rule differ.
# Input is stdin JSON, blocking is permissionDecision:"deny" — do not revert it to exit 1.
#
# Short sleeps (under 30 seconds) pass. An interval between retries is not polling.
#
# Known limit (not chased with a regex): a delay assembled from a variable (`sleep "$N"`).
#
# The shell removes quotes before it runs a word, so `sleep "60"` and `do "sleep" 1` are the
# same calls as the bare spellings: the scan reads the command with its quote characters gone
# (after ⑥ below, the one reader that needs them).

INPUT=$(cat)

# The C locale for every string pass below: the guard reads ASCII shell syntax, and in a UTF-8
# locale bash's `${s//…}` substitutions slow down past linear — on a 350 KB command they took
# 6.4 s against 0.4 s here. Exported, so grep reads the same bytes; a non-ASCII byte becomes a
# word boundary, which only widens the net.
export LC_ALL=C

# ① sleep N, N ≥ 30  : two digits are [3-9][0-9], three or more digits are all of them.
#    A trailing `s` and a decimal point ride along, so `sleep 45s` and `sleep 30.0` are the same N.
# ② a minute/hour/day suffix : every such delay is already past the line, however small N is.
# ③ a one-line loop header with a delay call inside it : polling regardless of the delay length.
# ④ a loop whose condition IS a process watch : polling even with no delay call to catch.
#    The watch has to be the condition — `while read p; do pgrep …` is a loop over input, not a poll.
# The left word boundary is the same [^[:alnum:]_] as block-dangerous.sh (\b is not trusted).
POLLING='(^|[^[:alnum:]_])sleep[[:space:]]+([3-9][0-9]|[0-9]{3,})(\.[0-9]+)?s?([^[:alnum:]_.]|$)'
POLLING="$POLLING"'|(^|[^[:alnum:]_])sleep[[:space:]]+[0-9]+(\.[0-9]+)?[mhd]([^[:alnum:]_]|$)'
POLLING="$POLLING"'|(^|[^[:alnum:]_])(until|while)[[:space:]].*[^[:alnum:]_]sleep[[:space:]]'
POLLING="$POLLING"'|(^|[^[:alnum:]_])(until|while)[[:space:]]+!?[[:space:]]*pgrep([^[:alnum:]_]|$)'

# ⑤ the loop written across several lines : grep reads line by line, so the keyword and the delay
#    never meet. Match them on a flattened copy instead — and require the `done` to come AFTER the
#    delay, which is what keeps `while read …; done` followed by an unrelated short sleep legal.
MULTILINE='(^|[^[:alnum:]_])(until|while)[[:space:]].*[^[:alnum:]_]sleep[[:space:]].*[^[:alnum:]_]done([^[:alnum:]_]|$)'
# ⑦ a loop whose body is empty (`do :; done`, `do true; done`) : a busy-wait spins on its own
#    condition — a file watch as much as ④'s process watch — with no delay call to catch at all.
#    Read on a flattened copy too (a newline there is the `;` it means to the shell), so the
#    same body written across lines is caught as well.
BUSY_WAIT='(^|[^[:alnum:]_])(until|while)[[:space:]].*[^[:alnum:]_]do[[:space:];]+(:|true)[[:space:];]*done([^[:alnum:]_]|$)'
#    ⑦'s one exception is the read-drain idiom (`… | while read -r _; do :; done`): each pass
#    blocks in `read` until a line arrives and the loop ends at EOF, so it consumes its input
#    rather than spinning. Only `while` + a plain `read` qualifies — `until read`, `while ! read`
#    and a `read` with its own `<` (which reopens the input every pass) spin at EOF and stay ⑦'s.
#    The idiom leaves ⑦'s copy alone: ①-⑤ read the loop as written, so a delay inside it is
#    still a poll.
DRAIN='(^|[^[:alnum:]_])while([[:space:]]+[A-Za-z_][A-Za-z0-9_]*=[^[:space:];&|<]*)*[[:space:]]+read([[:space:]]+[^[:space:];&|<]+)*[[:space:];]+do[[:space:];]+(:|true)[[:space:];]*done([^[:alnum:]_]|$)'

# ⑥ Text ABOUT polling is not polling. The scan is a string match on the raw command, so a
#    commit message, a `grep` pattern or a `git commit -F -` heredoc that NAMES the shapes
#    above was denied for describing them — it blocked the very commit that added ①-⑤.
#    So one span, and only one, is dropped from the scanned copy. It has to pass both tests:
#      (a) the shell itself expands nothing in it — single quotes, double quotes holding no
#          `$` and no backtick, a heredoc whose delimiter is quoted; and
#      (b) the word that OWNS it — the last word before it, skipping over -flags — is
#          `grep`/`rg` or `git commit`. Those two match text and store text; neither ever
#          runs what it is handed, and neither leaves it anywhere a later call could run it.
#          rg only without `--pre`, which runs its argument as a preprocessor on every file.
#    Everything outside that span is still scanned, so a loop standing next to a commit
#    message, and a loop handed to `bash -c '…'` or written into a file by `cat > f <<'EOF'`
#    (owners that DO run their argument), are denied exactly as before. One more heredoc owner
#    is inert: a `cat` that is the whole command, so all it can do with the body is print it.

# (b): the owner is running state, fed the text before each span left to right, so a span is
# judged in constant time. Walking back over the whole prefix for every span made a quote-heavy
# command quadratic (cubic with dropped spans, whose blanks were trimmed one at a time).
#   OWN_W    the last finished word that is not a -flag, OWN_PREV the one before it
#            (a `git … commit` needs both — every word between them is a flag);
#   OWN_PRE  set when a `--pre` flag came after OWN_W;
#   OWN_CUR  the word still open at the end of the text fed so far. A word only matters while it
#            is short (an owner's name) or at its two ends (a flag, `--pre=`, a `…/git` path), so
#            one that runs on across many spans keeps just its ends — copying it whole on every
#            span would be the quadratic again.
owner_reset() { OWN_W= OWN_PREV= OWN_PRE= OWN_CUR=; }

owner_word() {
  if [[ $1 == -* ]]; then
    [[ $1 == --pre || $1 == --pre=* ]] && OWN_PRE=1
  elif [[ -n $1 ]]; then
    OWN_PREV=$OWN_W OWN_W=$1 OWN_PRE=
  fi
}

owner_feed() {
  local - IFS=$' \t\n' i n
  local -a w
  if [[ $1 == *[[:space:]]* ]]; then
    set -f
    w=($1)
    n=${#w[@]}
    if [[ $1 == [[:space:]]* ]]; then owner_word "$OWN_CUR"; OWN_CUR=; fi
    ((n)) || return 0
    w[0]=$OWN_CUR${w[0]}
    for ((i = 0; i < n - 1; i++)); do owner_word "${w[i]}"; done
    OWN_CUR=${w[n-1]}
    if [[ $1 == *[[:space:]] ]]; then owner_word "$OWN_CUR"; OWN_CUR=; return 0; fi
  else
    OWN_CUR+=$1
  fi
  ((${#OWN_CUR} > 40)) && OWN_CUR=${OWN_CUR:0:16}$'\x1f'${OWN_CUR: -16}
  return 0
}

owner_reads_text() {
  local own=$OWN_W prev=$OWN_PREV pre=$OWN_PRE
  if [[ -n $OWN_CUR && $OWN_CUR != -* ]]; then
    own=$OWN_CUR prev=$OWN_W pre=
  elif [[ $OWN_CUR == --pre || $OWN_CUR == --pre=* ]]; then
    pre=1
  fi
  case $own in
    grep|egrep|fgrep) return 0 ;;
    rg) [[ -z $pre ]] ;;
    commit) [[ $prev == git || $prev == *[^[:alnum:]_-]git ]] ;;
    *) return 1 ;;
  esac
}

# A text owner whose output reaches a pipe: grep/rg print what they match, so `| bash` would run
# it. Read off the opener line's tail and what follows the delimiter line (the caller's `tail`
# and `post`); the owner is the word owner_reads_text judged.
owner_output_piped() {
  local own=$OWN_W
  [[ -n $OWN_CUR && $OWN_CUR != -* ]] && own=$OWN_CUR
  [[ $own != commit && ( $tail == *'|'* || $post == *'|'* ) ]]
}

# (a) for a heredoc: the delimiter must be QUOTED (<<'MSG'). An unquoted <<MSG expands, and
# so is left to the scan. Only the body goes — the rest of the opener line stays, or a
# `<<'MSG' && while …` tail would leave with it. An unterminated body stays too (fail-closed).
# A quoted delimiter is inert only at its own shell level: an opener behind an odd number of `"`
# sits inside a double quote still open (`bash -c " grep <<'MSG' … $(…) … MSG"`), where the OUTER
# shell expands the body before any inner shell sees a heredoc — so that body stays as well.
# Three more shapes keep the body, each because the shell would not end or keep it where the
# cut does:
#   - an opener line ending in `\`: the line continues, and the next line (`&& bash -c '…'`) is
#     still the command, with the body starting after it;
#   - `<<-`: a tab-indented delimiter line ends the body too, so the first exact one is not the end;
#   - a grep/rg whose output is piped (`grep -e. <<'EOF' | bash`, `{ grep …; } | bash`): it
#     prints the matching lines, and a pipe can hand them to a shell. A `|` after the opener or
#     after the delimiter line keeps the body; `git commit` prints no message, so it keeps its pass.
# The lone `cat` is judged on the whole command, not on a word: nothing before it (no `$(`, no
# `{`, no `exec >f`), nothing between it and the opener but option letters (no `>f`, no `;bash`),
# nothing after the opener on its line (no `| bash`, no `> f`, no `\` continuing the line) and
# nothing after the delimiter line (no `} | bash`). Its output goes to the transcript only.
strip_quoted_heredoc() {
  local s=$1 opener tok delim pre rest tail probe post dq
  local re="<<-?[[:space:]]*('[A-Za-z_][A-Za-z0-9_]*'|\"[A-Za-z_][A-Za-z0-9_]*\")"
  if [[ $s =~ $re ]]; then
    opener=${BASH_REMATCH[0]}
    tok=${BASH_REMATCH[1]}
    delim=${tok:1:${#tok}-2}
    # Cut at the earliest match with `%%…*` and slice by offset: `#*…` re-matches every prefix
    # of the string, which is quadratic in a long heredoc body.
    pre=${s%%"$opener"*}
    rest=${s:${#pre}+${#opener}}
    dq=${pre//[^\"]/}
    if (( ${#dq} % 2 == 0 )) && [[ $opener != '<<-'* && $rest == *$'\n'* ]]; then
      tail=${rest%%$'\n'*}
      probe=$'\n'${rest:${#tail}+1}
      post=${probe%%$'\n'"$delim"$'\n'*}
      if [[ $post != "$probe" ]]; then
        post=${probe:${#post}+${#delim}+2}
      elif [[ $probe == *$'\n'"$delim" ]]; then
        post=
      fi
      owner_reset
      owner_feed "$pre"
      if [[ $post != "$probe" && $tail != *\\ ]] && { owner_reads_text && ! owner_output_piped ||
           { [[ $pre =~ ^[[:space:]]*cat([[:space:]]+-[[:alnum:]-]*)*[[:space:]]*$ ]] &&
             [[ $tail != *[^[:space:]]* && $post != *[^[:space:]]* ]]; }; }; then
        s="$pre $tail"$'\n'"$post"
      fi
    fi
  fi
  printf '%s' "$s"
}

# (a) for an argument: single quotes are literal by definition; double quotes only when no
# expansion operator is inside them. Spans are taken left to right, so a live one that follows
# an exempt one is still weighed on its own owner. Linear, like block-dangerous.sh's segments():
# the command is cut into tokens once (each quote character a token of its own, \x1e as the cut)
# and the spans are rebuilt from those, never by re-slicing the rest of the string per quote.
strip_inert_quotes() {
  local s=$1 q i j n
  local -a tok out=() body
  s=${s//$'\x1e'/ }
  s=${s//\'/$'\x1e'\'$'\x1e'}
  s=${s//\"/$'\x1e'\"$'\x1e'}
  IFS=$'\x1e' read -rd '' -a tok <<< "$s"
  local IFS=
  n=${#tok[@]}
  ((n)) && tok[n-1]=${tok[n-1]%$'\n'}
  owner_reset
  for ((i = 0; i < n; i++)); do
    q=${tok[i]}
    if [[ $q != "'" && $q != '"' ]]; then out+=("$q"); owner_feed "$q"; continue; fi
    body=()
    for ((j = i + 1; j < n; j++)); do
      [[ ${tok[j]} == "$q" ]] && break
      body+=("${tok[j]}")
    done
    if ((j == n)); then out+=("$q" "${body[@]}"); break; fi
    s="${body[*]}"
    if { [[ $q == "'" ]] || [[ $s != *'$'* && $s != *'`'* ]]; } && owner_reads_text; then
      s=" "
    else
      s="$q$s$q"
    fi
    out+=("$s")
    owner_feed "$s"
    i=$j
  done
  printf '%s' "${out[*]}"
}

CMD=$(printf '%s' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)

# Without jq CMD comes back empty and every poll passes with no signal.
# Same prescription as block-dangerous.sh: scan the raw stdin with the same regex (fail-closed).
# ⑥ is not applied there — the raw JSON is not a command, so its quoting cannot be read as
# quoting. The jq-less net stays the wider one, which is the safe direction to be wrong in.
SCAN="$CMD"
if [ -z "$CMD" ]; then
  SCAN="$INPUT"
  command -v jq >/dev/null 2>&1 || \
    echo "block-poll-loop: jq 를 찾을 수 없어 원본 stdin 을 훑는다(그물이 조금 넓어진다)." >&2
else
  SCAN=$(strip_quoted_heredoc "$CMD")
  SCAN=$(strip_inert_quotes "$SCAN")
fi
# Quote removal (see the top): escaped quotes first — the jq-less raw JSON is full of them.
SCAN=${SCAN//\\\"/}
SCAN=${SCAN//[\"\']/}

# The flattened copy folds real newlines and the literal \n of the jq-less raw-JSON path alike —
# without the second one the fallback reads `\nwhile` as one word and the boundary never matches.
# Bash expansion, not tr: the jq-less PATH is down to a handful of tools and must not need one more.
FLAT=${SCAN//$'\n'/ }
FLAT=${FLAT//\\n/ }
# ⑦'s copy: a newline is the list separator it is to the shell, so a `read`'s arguments stop at
# its own line, and each read-drain loop is dropped. Each pass reads the whole copy, so the passes
# are capped: a command holding more drains than that keeps the rest, and ⑦ reads them as
# busy-waits — the wider net, and a hook that cannot run long on a pathological command.
LOOPS=${SCAN//$'\n'/; }
LOOPS=${LOOPS//\\n/; }
for ((k = 0; k < 16; k++)); do
  [[ $LOOPS =~ $DRAIN ]] || break
  LOOPS=${LOOPS/"${BASH_REMATCH[0]}"/ : }
done

if printf '%s' "$SCAN" | grep -qE "$POLLING" || printf '%s' "$FLAT" | grep -qE "$MULTILINE" \
   || printf '%s' "$LOOPS" | grep -qE "$BUSY_WAIT"; then
  cat <<'JSON'
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "폴링 루프가 감지되어 차단되었습니다 (30초 이상 sleep — sleep 2m 처럼 단위를 붙인 것도 포함 / until…sleep / while…sleep / while pgrep 같은 프로세스 감시 루프 / do :; done 처럼 본문이 빈 대기 루프 / 줄을 나눠 쓴 루프도 같습니다). 좀비 프로세스와 컨텍스트 낭비를 만듭니다. 오래 걸리는 작업은 Bash 의 run_in_background 로 띄우세요 — 완료되면 자동으로 알림이 옵니다. 조건이 충족될 때까지 기다려야 하면 Monitor 도구를 쓰고, 하네스 진행 상황만 알고 싶으면 python3 scripts/execute.py <phase> --status 또는 /harness-status 를 쓰세요. 재시도 사이 간격이 필요한 것이라면 30초 미만 sleep 은 그대로 통과합니다. 폴링을 설명하는 글은 막지 않습니다 — grep 패턴과 git commit 메시지(-m, git commit -F - 히어독)는 통과하니, 커밋 메시지를 파일에 먼저 쓰지 말고 git commit -F - 로 바로 넘기세요."
  }
}
JSON
fi

exit 0
