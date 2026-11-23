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
# The receipt (<job>.workergate.json) lets the runner skip its own gate on the same head and gate
# command: written only for a green gate on a clean tree whose head did not move, removed otherwise.
receipt="$ARK_RUN_DIR/$ARK_JOB.workergate.json"
rm -f "$receipt"
clean_head() {
  [ -z "$(git -C "$ARK_WORKTREE" status --porcelain 2>/dev/null || echo dirty)" ] &&
    git -C "$ARK_WORKTREE" rev-parse HEAD 2>/dev/null
}
head_before=$(clean_head)
out=$(cd "$ARK_WORKTREE" && sh -c "$ARK_GATE" 2>&1) && {
  if [ -n "$head_before" ] && [ "$(clean_head)" = "$head_before" ]; then
    jq -n --arg head "$head_before" --arg gate "$ARK_GATE" \
      --arg summary "$(printf '%s\n' "$out" | awk 'NF { l = $0 } END { print l }')" \
      --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      '{head: $head, gate: $gate, ok: true, summary: $summary, at: $at}' > "$receipt.tmp" &&
      mv "$receipt.tmp" "$receipt"
  fi
  [ -n "$ARK_DONE_COUNT" ] && [ -n "$ARK_REPORT" ] || exit 0
  have=$(awk '/^## /{s = ($0 ~ /^##[[:blank:]]*Done when/)} s && /^- / && /evidence:/{c++} END{print c+0}' "$ARK_REPORT" 2>/dev/null)
  [ "${have:-0}" -ge "$ARK_DONE_COUNT" ] && exit 0
  echo $((n + 1)) > "$count_file"
  jq -n --arg r "Your report's \`## Done when\` section has evidence for ${have:-0} of $ARK_DONE_COUNT items. Add one bullet per item with \`evidence:\`, or end with STATUS: FAILED — <why>." '{decision: "block", reason: $r}'
  exit 0
}
echo $((n + 1)) > "$count_file"
tail=$(printf '%s' "$out" | tail -c 3000)
jq -n --arg r "The job gate is red ($((n + 1))/3). Fix it before you finish, or end with STATUS: FAILED — <why>.
Gate: $ARK_GATE
Output (tail):
$tail" '{decision: "block", reason: $r}'
