#!/usr/bin/env bash
# Desk mode SessionStart (project settings of ~/.claude/arkimedes): inject the desk overview. No model.
[ -n "${ARK_ROLE:-}" ] && exit 0
cat >/dev/null
CODE="$(cd "$(dirname "$0")/.." && pwd)"
if ! ov=$("$CODE/bin/ark" status 2>&1); then ov="(ark status failed: $ov)"; fi
jq -nc --arg c "# Desk mode — overview (injected by desk-start.sh)
$ov

Commands: ark status · ark plans · ark validate <repo>/<slug> · ark approve <repo>/<slug> (Marco confirms)" \
  '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$c}}'
