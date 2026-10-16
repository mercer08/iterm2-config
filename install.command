#!/bin/zsh
# Pass --stage DIR, --apply [--install-deps], or --restore BACKUP_DIR.
set -eu
if ! command -v python3 >/dev/null 2>&1; then
  print -u2 'Python 3 is required. Install Python 3, then rerun this command.'
  exit 1
fi
exec python3 -B "${0:A:h}/scripts/install.py" "$@"
