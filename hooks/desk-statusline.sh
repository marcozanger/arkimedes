#!/usr/bin/env bash
# Planner statusline (project settings of ~/.claude/arkimedes): Arkimedes badge, then the normal statusline.
INPUT=$(cat)
REST=$(printf '%s' "$INPUT" | bash "$HOME/.claude/hooks/statusline.sh" 2>/dev/null)
printf '\033[1;35m📝 planner\033[0m %s' "$REST"
