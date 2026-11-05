#!/usr/bin/env bash
# Arkimedes PreToolUse(Agent): every dispatch must name its model tier.
input=$(cat)
[ -n "$(printf '%s' "$input" | jq -r '.agent_id // empty')" ] && exit 0   # subagents: not ours to police
m=$(printf '%s' "$input" | jq -r '.tool_input.model // empty')
[ -n "$m" ] && exit 0
jq -nc '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:"Arkimedes: pass model explicitly on every Agent call (haiku=T0, sonnet=T1, opus=T2)."}}'
