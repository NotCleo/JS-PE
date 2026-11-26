#!/usr/bin/env bash
# PreToolUse (Bash): block self-matching wait loops (CLAUDE.md workflow rule).
cmd=$(jq -r '.tool_input.command // ""')
if printf '%s' "$cmd" | grep -Eq 'pgrep[[:space:]]+-f|until[[:space:]]+grep|while[[:space:]]+pgrep'; then
  echo "Blocked by CLAUDE.md rule: never wait with pgrep -f / until-grep polling loops (they match themselves)." \
       "Run tests in the foreground with timeout, or capture the PID at launch (cmd & pid=\$!) and use wait \$pid / tail --pid=\$pid -f /dev/null." >&2
  exit 2
fi
exit 0
