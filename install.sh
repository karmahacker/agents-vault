#!/usr/bin/env bash
# Install agents-vault on an agent server (Linux, systemd). Idempotent: safe to
# re-run to upgrade; never replaces an existing vault, key file or sources file.
#
#   ./install.sh [--sources FILE] [--no-system-link] [--no-timer] [--no-rules] [--no-import]
#
#   --sources FILE    copy FILE to ~/.config/agents-vault/sources.toml (only if none exists yet)
#   --no-system-link  don't symlink /usr/local/bin/sec (needs sudo)
#   --no-timer        don't install the hourly import timer
#   --no-rules        don't add the "Secrets (agents vault)" section to agent instruction files
#   --no-import       don't run the first import
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT_DIR="${SEC_VAULT_DIR:-$HOME/.agents-vault}"
CONF_DIR="$HOME/.config/agents-vault"
SHARE_DIR="$HOME/.local/share/agents-vault"
BIN_DIR="$HOME/.local/bin"
SOURCES="" SYSLINK=1 TIMER=1 RULES=1 IMPORT=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sources) SOURCES="$2"; shift 2 ;;
    --no-system-link) SYSLINK=0; shift ;;
    --no-timer) TIMER=0; shift ;;
    --no-rules) RULES=0; shift ;;
    --no-import) IMPORT=0; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }

step "dependencies"
if ! command -v keepassxc-cli >/dev/null; then
  if command -v apt-get >/dev/null; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends keepassxc
  else
    echo "install keepassxc (keepassxc-cli >= 2.7) with your package manager, then re-run" >&2; exit 1
  fi
fi
python3 -c 'import sys, tomllib; sys.exit(sys.version_info < (3, 11))' \
  || { echo "python3 >= 3.11 required (tomllib)" >&2; exit 1; }
command -v flock >/dev/null || { echo "flock (util-linux) required" >&2; exit 1; }
echo "keepassxc-cli $(keepassxc-cli --version), $(python3 --version)"

step "scripts"
install -d -m 755 "$BIN_DIR" "$SHARE_DIR"
install -m 755 "$REPO/bin/sec" "$BIN_DIR/sec"
install -m 755 "$REPO/lib/import.py" "$SHARE_DIR/import.py"
echo "sec -> $BIN_DIR/sec, importer -> $SHARE_DIR/import.py"
if [[ $SYSLINK == 1 ]]; then
  # Agents often run non-login shells whose PATH lacks ~/.local/bin.
  sudo ln -sf "$BIN_DIR/sec" /usr/local/bin/sec && echo "linked /usr/local/bin/sec"
fi

step "vault"
install -d -m 700 "$VAULT_DIR" "$VAULT_DIR/backups"
if [[ -e $VAULT_DIR/agents.kdbx ]]; then
  echo "existing vault kept: $VAULT_DIR/agents.kdbx"
else
  [[ -e $VAULT_DIR/agents.keyx ]] && { echo "key file exists without a vault; refusing to guess" >&2; exit 1; }
  keepassxc-cli db-create --set-key-file "$VAULT_DIR/agents.keyx" "$VAULT_DIR/agents.kdbx"
  echo "created $VAULT_DIR/agents.kdbx with new key file agents.keyx"
fi
chmod 600 "$VAULT_DIR"/agents.kdbx "$VAULT_DIR"/agents.keyx
if [[ ! -e $VAULT_DIR/README.md ]]; then
  install -m 600 "$REPO/templates/vault-README.md" "$VAULT_DIR/README.md"
  echo "wrote $VAULT_DIR/README.md"
fi

step "sources"
install -d -m 700 "$CONF_DIR"
if [[ -e $CONF_DIR/sources.toml ]]; then
  echo "existing sources kept: $CONF_DIR/sources.toml"
elif [[ -n $SOURCES ]]; then
  install -m 600 "$SOURCES" "$CONF_DIR/sources.toml"; echo "installed $SOURCES as sources.toml"
else
  install -m 600 "$REPO/examples/sources.example.toml" "$CONF_DIR/sources.toml"
  echo "installed the EXAMPLE sources file; edit $CONF_DIR/sources.toml before importing"
  IMPORT=0
fi

if [[ $TIMER == 1 ]] && systemctl --user show-environment >/dev/null 2>&1; then
  step "hourly import timer"
  install -d "$HOME/.config/systemd/user"
  install -m 644 "$REPO"/systemd/agents-vault-import.{service,timer} "$HOME/.config/systemd/user/"
  systemctl --user daemon-reload
  systemctl --user enable --now agents-vault-import.timer
  [[ $(loginctl show-user "$USER" -p Linger --value 2>/dev/null) == yes ]] \
    || echo "note: run 'sudo loginctl enable-linger $USER' so the timer runs while you are logged out"
fi

if [[ $RULES == 1 ]]; then
  step "agent instructions"
  # Replace or append the "## Secrets (agents vault)" section in each agent's global instructions.
  targets=("$HOME/AGENTS.md")
  [[ -d $HOME/.claude ]] && targets+=("$HOME/.claude/CLAUDE.md")
  [[ -d $HOME/.codex ]] && targets+=("$HOME/.codex/AGENTS.md")
  [[ -d $HOME/.gemini ]] && targets+=("$HOME/.gemini/GEMINI.md")
  python3 - "$REPO/templates/agent-rule.md" "${targets[@]}" <<'PY'
import re, sys, pathlib
rule = pathlib.Path(sys.argv[1]).read_text().strip() + "\n"
for name in sys.argv[2:]:
    f = pathlib.Path(name)
    t = f.read_text() if f.exists() else ""
    if "## Secrets (agents vault)" in t:
        t = re.sub(r"## Secrets \(agents vault\).*?(?=\n## |\Z)", lambda _: rule.rstrip("\n") + "\n", t, flags=re.S)
    else:
        t = (t.rstrip("\n") + "\n\n" if t.strip() else "") + rule
    f.write_text(t)
    print("updated", f)
PY
fi

if [[ $IMPORT == 1 ]]; then
  step "import"
  "$BIN_DIR/sec" import
fi

step "done"
echo "try: sec ls | head; sec help"
