#!/usr/bin/env bash
# Desk statusline (project settings of ~/.claude/arkimedes): Arkimedes badge, then the normal statusline.
INPUT=$(cat)
REST=$(printf '%s' "$INPUT" | bash "$HOME/.claude/hooks/statusline.sh" 2>/dev/null)
printf '\033[1;35m⚙ arkimedes · desk\033[0m %s' "$REST"
