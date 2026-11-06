#!/usr/bin/env bash
# Arkimedes SessionStart: hand the orchestrator its state so it doesn't read files to find it.
# Runner processes (workers, reviewers, ticks) get no session context: their brief is all they need (§10).
[ -n "$ARK_ROLE" ] && exit 0
. "$HOME/.claude/coach/lib.sh"
A="$HOME/.claude/arkimedes"
input=$(cat)
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty'); cwd=${cwd:-$PWD}
repo=$(coach_repo_key "$cwd")

queue_block() { # $1 file
  [ -f "$1" ] || { echo "  (no file)"; return; }
  local o p b
  o=$(grep -c '^- \[ \]' "$1"); p=$(grep -c '^- \[>\]' "$1"); b=$(grep -c '^- \[!\]' "$1")
  echo "  open $o · in flight $p · blocked $b"
  grep -n '^- \[[ >!]\]' "$1" | head -15 | sed 's/^/  /'
}

ctx=$(
  echo "# Arkimedes mode — session state (injected by hook; coach hooks are off in this mode)"
  echo "cwd: $cwd · repo: $repo"
  echo; echo "## Queue $repo ($(coach_queue_file "$cwd"))"; queue_block "$(coach_queue_file "$cwd")"
  if [ "$repo" != global ]; then echo; echo "## Queue global"; queue_block "$COACH_DIR/queue/global.md"; fi
  echo; echo "## Unfinished runs"
  n=0; for r in "$A"/runs/*.md; do [ -f "$r" ] || continue
    grep -q '^Status: closed' "$r" || { echo "  $r"; n=$((n+1)); }; done
  [ "$n" = 0 ] && echo "  none"
  echo; echo "## Log (last 20 jobs: kind tier_start→tier_end outcome)"
  if [ -s "$A/log.jsonl" ]; then
    tail -20 "$A/log.jsonl" | jq -r '"  \(.kind) \(.tier_start)→\(.tier_end) \(.outcome) (esc \(.escalations))"' 2>/dev/null
  else echo "  empty"; fi
  inf=$(coach_inflight "$cwd")
  echo; echo "## Other worktrees in flight"; if [ -n "$inf" ]; then printf '%s\n' "$inf" | sed 's/^/  - /'; else echo "  none"; fi
)
jq -nc --arg c "$ctx" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$c}}'
