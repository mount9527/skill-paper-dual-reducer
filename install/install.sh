#!/usr/bin/env bash
# Install paper-dual-reduce as a skill for DSH and Claude Code (symlink, or copy).
#
#   bash install/install.sh          # symlink
#   bash install/install.sh --copy   # copy instead
set -euo pipefail

MODE="link"
if [ "${1:-}" = "--copy" ]; then
  MODE="copy"
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME="paper-dual-reduce"

if [ ! -f "$REPO/run.py" ]; then
  echo "Not a paper-dual-reduce checkout: $REPO" >&2
  exit 1
fi

for dest in "$HOME/.dsh/skills/$NAME" "$HOME/.claude/skills/$NAME"; do
  mkdir -p "$(dirname "$dest")"
  if [ -e "$dest" ] || [ -L "$dest" ]; then
    echo "[skip] exists: $dest"
    continue
  fi
  if [ "$MODE" = "link" ]; then
    if ln -s "$REPO" "$dest" 2>/dev/null; then
      echo "[ok]   link: $dest -> $REPO"
      continue
    fi
    echo "[warn] symlink failed; falling back to copy"
  fi
  cp -R "$REPO" "$dest"
  echo "[ok]   copy: $dest"
done

cat <<'EOF'

Next steps by host:
  DSH / Claude Code : restart the session, then ask for de-AI / de-dup on a .docx
  Codex / WorkBuddy : open the repo as the workspace; AGENTS.md is already at the root
  Cursor            : copy .cursor/rules/paper-dual-reduce.mdc into <project>/.cursor/rules/
  Chat-only (Doubao): paste PROMPT.md content, then the paper text

Smoke test: python run.py doctor
EOF
