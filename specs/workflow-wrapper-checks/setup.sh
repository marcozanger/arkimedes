#!/usr/bin/env bash
# Create the throwaway sandbox repo used by checks C1-C6.
set -euo pipefail
home="${ARK_CHECKS_HOME:-$HOME/ark-checks}"
sandbox="$home/sandbox"
if [ -e "$sandbox" ]; then
  echo "already exists: $sandbox" >&2
  exit 1
fi
mkdir -p "$sandbox" "$home/state/protected" "$home/state/inbox"
cd "$sandbox"
git init -q -b main
printf '# Ark checks sandbox\n\nThrowaway repo.\n' > README.md
printf '.claude/\n' > .gitignore
git add README.md .gitignore
git commit -q -m "chore: sandbox"
echo "sandbox ready: $sandbox"
echo "next: cd $sandbox && claude   (accept the trust question, then /exit)"
