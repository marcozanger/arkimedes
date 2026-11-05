#!/usr/bin/env bash
# Arkimedes statusline: ark badge + queue markers for this repo, then the normal statusline.
INPUT=$(cat)
. "$HOME/.claude/coach/lib.sh"
CWD=$(printf '%s' "$INPUT" | jq -r '.workspace.current_dir // .cwd // empty' 2>/dev/null)
Q=$(coach_queue_file "${CWD:-.}")
o=0; p=0; b=0
if [ -f "$Q" ]; then o=$(grep -c '^- \[ \]' "$Q"); p=$(grep -c '^- \[>\]' "$Q"); b=$(grep -c '^- \[!\]' "$Q"); fi
BADGE=$(printf '\033[1;35m⚙ arkimedes\033[0m queue %s · ▶ %s' "$o" "$p")
[ "$b" -gt 0 ] && BADGE="$BADGE · ⛔ $b"
REST=$(printf '%s' "$INPUT" | bash "$HOME/.claude/hooks/statusline.sh" 2>/dev/null | sed 's/🧭 queue [0-9]* · in flight [0-9]*//')
printf '%s %s' "$BADGE" "$REST"
