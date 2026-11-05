#!/usr/bin/env bash
# Arkimedes PreToolUse(Write|Edit|NotebookEdit): the orchestrator only writes its own state.
# Workers (calls carrying agent_id) are allowed; require-worktree.sh still guards them.
input=$(cat)
[ -n "$(printf '%s' "$input" | jq -r '.agent_id // empty')" ] && exit 0
f=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
case "$f" in
  "$HOME/.claude/arkimedes/"*|"$HOME/.claude/coach/queue/"*) exit 0 ;;
esac
jq -nc --arg f "$f" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:("Arkimedes does not edit files outside ~/.claude/arkimedes and the coach queue: " + $f + ". Dispatch a worker.")}}'
