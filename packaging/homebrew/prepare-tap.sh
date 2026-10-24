#!/usr/bin/env bash
# Prepare a local, unpublished tap for the branch experiment.
set -euo pipefail
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
source_url="${AGENTS_VAULT_SOURCE_URL:-$(git -C "$repo" remote get-url origin)}"
source_branch="${AGENTS_VAULT_SOURCE_BRANCH:-experiment/homebrew-packaging}"
tap_dir="$(brew --repository)/Library/Taps/agents-vault/homebrew-experimental"
mkdir -p "$tap_dir/Formula"
python3 - "$repo/packaging/homebrew/agents-vault.rb.in" "$tap_dir/Formula/agents-vault.rb" "$source_url" "$source_branch" <<'PY'
from pathlib import Path
import sys

def ruby_string(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

text = Path(sys.argv[1]).read_text()
for token, value in zip(("SOURCE_URL", "SOURCE_BRANCH"), sys.argv[3:]):
    text = text.replace(token, ruby_string(value))
Path(sys.argv[2]).write_text(text)
PY
printf '%s\n' 'Prepared local tap agents-vault/experimental (not published).' \
  'Next: brew install --HEAD agents-vault/experimental/agents-vault'
