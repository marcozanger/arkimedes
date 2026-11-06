#!/bin/bash
# Stop hook for runner workers (§6): while the job gate is red the worker may not stop; the gate
# output goes back to it. After 3 blocks, or when its last message ends with STATUS: FAILED or
# BLOCKED (as lib/ark/brief.py reads it), it may end. Earlier text does not count: the brief and
# the guards' own messages quote those lines. Does nothing outside ARK_ROLE=worker.
[ "$ARK_ROLE" = "worker" ] || exit 0
[ -n "$ARK_GATE" ] && [ -d "$ARK_WORKTREE" ] && [ -n "$ARK_RUN_DIR" ] || exit 0
input=$(cat)
last=$(printf '%s' "$input" | jq -r '.last_assistant_message | strings' 2>/dev/null)
if [ -z "$last" ]; then   # older input: the last assistant text in the transcript
  transcript=$(printf '%s' "$input" | jq -r '.transcript_path // empty' 2>/dev/null)
  if [ -n "$transcript" ] && [ -f "$transcript" ]; then
    last=$(tail -c 1000000 "$transcript" | jq -Rrs '[split("\n")[] | fromjson? | objects | select(.type == "assistant")
      | .message.content | if type == "array" then [.[] | objects | select(.type == "text") | .text] | join("\n")
                           elif type == "string" then . else "" end
      | select(length > 0)] | last // empty' 2>/dev/null)
  fi
fi
if printf '%s\n' "$last" | awk 'NF { l = $0 } END { print l }' |
   grep -Eq '^STATUS:[[:blank:]]*(FAILED|BLOCKED)([^[:alnum:]_]|$)'; then
  exit 0
fi
count_file="$ARK_RUN_DIR/$ARK_JOB.doneblocks"
n=$(cat "$count_file" 2>/dev/null || echo 0)
[ "$n" -ge 3 ] && exit 0
out=$(cd "$ARK_WORKTREE" && sh -c "$ARK_GATE" 2>&1) && exit 0
echo $((n + 1)) > "$count_file"
tail=$(printf '%s' "$out" | tail -c 3000)
jq -n --arg r "The job gate is red ($((n + 1))/3). Fix it before you finish, or end with STATUS: FAILED — <why>.
Gate: $ARK_GATE
Output (tail):
$tail" '{decision: "block", reason: $r}'
