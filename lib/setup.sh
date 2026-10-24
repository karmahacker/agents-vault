#!/usr/bin/env bash
# Explicit per-user setup; package installation never invokes this automatically.
set -euo pipefail
LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOURCE_DIR="${SEC_RESOURCES:-$LIB_DIR}"
VAULT_DIR="${SEC_VAULT_DIR:-$HOME/.agents-vault}"
CONF_DIR="${SEC_CONFIG_DIR:-$HOME/.config/agents-vault}"
PLATFORM="$(uname -s)"
SOURCES="" TIMER=1 RULES=1 IMPORT=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sources) SOURCES="$2"; shift 2 ;;
    --no-timer) TIMER=0; shift ;;
    --no-rules) RULES=0; shift ;;
    --no-import) IMPORT=0; shift ;;
    -h|--help)
      echo 'sec init [--sources FILE] [--no-timer] [--no-rules] [--no-import]'
      exit 0 ;;
    *) echo "unknown init option: $1" >&2; exit 2 ;;
  esac
done
command -v keepassxc-cli >/dev/null || { echo "keepassxc-cli is required; install KeePassXC first" >&2; exit 1; }
python3 -c 'import tomllib' || { echo "Python >= 3.11 required" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }
# Refuse incomplete vaults before creating or updating any setup files.
if [[ -e $VAULT_DIR/agents.kdbx && ! -e $VAULT_DIR/agents.keyx ]] ||
   [[ -e $VAULT_DIR/agents.keyx && ! -e $VAULT_DIR/agents.kdbx ]]; then
  echo "vault or key file missing its counterpart; restore it before initialization" >&2
  exit 1
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
if [[ -e $VAULT_DIR/README.md ]] && ! cmp -s "$RESOURCE_DIR/templates/vault-README.md" "$VAULT_DIR/README.md"; then
  guide_backup="$(mktemp "$VAULT_DIR/README.md.previous.XXXXXX")"
  install -m 600 "$VAULT_DIR/README.md" "$guide_backup"
  echo "saved previous guide: $guide_backup"
fi
install -m 600 "$RESOURCE_DIR/templates/vault-README.md" "$VAULT_DIR/README.md"
echo "updated $VAULT_DIR/README.md"

step "sources"
install -d -m 700 "$CONF_DIR"
if [[ -e $CONF_DIR/sources.toml ]]; then
  echo "existing sources kept: $CONF_DIR/sources.toml"
elif [[ -n $SOURCES ]]; then
  install -m 600 "$SOURCES" "$CONF_DIR/sources.toml"; echo "installed $SOURCES as sources.toml"
else
  install -m 600 "$RESOURCE_DIR/examples/sources.example.toml" "$CONF_DIR/sources.toml"
  echo "installed the EXAMPLE sources file; edit $CONF_DIR/sources.toml before importing"
  IMPORT=0
fi

if [[ $TIMER == 1 && $PLATFORM == Darwin ]]; then
  step "hourly launchd import"
  launch_dir="$HOME/Library/LaunchAgents"
  install -d -m 700 "$launch_dir"
  launch_plist="$launch_dir/local.agents-vault.import.plist"
  python3 "$LIB_DIR/launchd.py" "$launch_plist" "$(command -v python3)" \
    "$LIB_DIR/import.py" "$VAULT_DIR" "$CONF_DIR/sources.toml" "$PATH"
  launch_domain="gui/$(id -u)"
  if launchctl print "$launch_domain" >/dev/null 2>&1; then
    launchctl bootout "$launch_domain/local.agents-vault.import" 2>/dev/null || true
    launchctl enable "$launch_domain/local.agents-vault.import"
    launchctl bootstrap "$launch_domain" "$launch_plist"
  else
    echo "LaunchAgent installed; it will load at your next graphical login. No GUI session is active."
  fi
elif [[ $TIMER == 1 ]] && systemctl --user show-environment >/dev/null 2>&1; then
  step "hourly import timer"
  install -d "$HOME/.config/systemd/user"
  install -m 644 "$RESOURCE_DIR"/systemd/agents-vault-import.{service,timer} "$HOME/.config/systemd/user/"
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
  python3 - "$RESOURCE_DIR/templates/agent-rule.md" "${targets[@]}" <<'PY'
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
  python3 "$LIB_DIR/import.py" --config "$CONF_DIR/sources.toml"
fi

step "done"
echo "try: sec ls | head; sec help"
