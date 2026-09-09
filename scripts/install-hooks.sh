#!/usr/bin/env bash
# Point git at the tracked hooks in .githooks/.
#
# The pre-commit hook runs scripts/check_pii.py over the staged files. Its
# name-and-place layer needs a denylist that cannot live in a public repo;
# put one at ~/.config/schoolsoft-mcp/pii-denylist.txt (one term per line)
# or set PII_DENYLIST_FILE. Without it the hook still runs, and still
# catches record ids, personal numbers, emails and school URLs.
set -euo pipefail
root=$(git rev-parse --show-toplevel)
git -C "$root" config core.hooksPath .githooks
echo "hooks enabled: $(git -C "$root" config core.hooksPath)"
if [ ! -f "${PII_DENYLIST_FILE:-$HOME/.config/schoolsoft-mcp/pii-denylist.txt}" ]; then
  echo "note: no private denylist found — names and places are not being checked." >&2
fi
