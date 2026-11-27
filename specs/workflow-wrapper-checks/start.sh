#!/usr/bin/env bash
# Start one check session the way Ark would: worktree, per-session settings, detached tmux.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
home="${ARK_CHECKS_HOME:-$HOME/ark-checks}"
sandbox="$home/sandbox"

check="${1:?usage: start.sh <c1|c2|c3|c4|c6> [--allow] [--telemetry]}"
shift
allow=0
telemetry=0
for arg in "$@"; do
  case "$arg" in
    --allow) allow=1 ;;
    --telemetry) telemetry=1 ;;
    *) echo "unknown option: $arg" >&2; exit 1 ;;
  esac
done
prompt_file="$here/prompts/$check.txt"
[ -f "$prompt_file" ] || { echo "no prompt for check: $check" >&2; exit 1; }
[ -d "$sandbox/.git" ] || { echo "run setup.sh first" >&2; exit 1; }
command -v tmux >/dev/null || { echo "tmux is not installed" >&2; exit 1; }

stamp="$(date +%m%d-%H%M%S)"
name="ark-$check-$stamp"
worktree="$sandbox/.claude/worktrees/$name"
git -C "$sandbox" worktree add -q "$worktree" -b "ark/$check-$stamp"

ark="$here/fake_ark.py"
settings="$home/state/$name.settings.json"
rules=""
if [ "$allow" = 1 ]; then
  rules="\"Workflow\", \"Bash($ark *)\", \"Bash(git add *)\", \"Bash(git commit *)\", \"Bash(git push *)\", \"Bash(gh pr create *)\""
fi
# "//path" is an absolute path in permission rules; $home already starts with one slash.
cat > "$settings" <<EOF
{ "permissions": { "allow": [$rules], "deny": ["Edit(/$home/state/protected/**)"] } }
EOF

session_id="$(uuidgen | tr '[:upper:]' '[:lower:]')"
prompt="$(sed -e "s|{{ARK}}|$ark|g" -e "s|{{STATE}}|$home/state|g" -e "s|{{NAME}}|$name|g" "$prompt_file")"

env_args=(-e "ARK_CHECKS_HOME=$home")
if [ "$telemetry" = 1 ]; then
  for pair in CLAUDE_CODE_ENABLE_TELEMETRY=1 OTEL_METRICS_EXPORTER=otlp OTEL_LOGS_EXPORTER=otlp \
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318 \
    OTEL_LOGS_EXPORT_INTERVAL=2000 OTEL_METRIC_EXPORT_INTERVAL=5000; do
    env_args+=(-e "$pair")
  done
fi

tmux new-session -d -s "$name" -c "$worktree" -x 200 -y 50 "${env_args[@]}" \
  claude --session-id "$session_id" --effort ultracode --permission-mode auto \
  --settings "$settings" "$prompt"

echo "$(date '+%F %T') $name $session_id allow=$allow telemetry=$telemetry" >> "$home/sessions.log"
echo "started: $name"
echo "watch:   tmux capture-pane -p -t $name"
echo "attach:  tmux attach -t $name"
