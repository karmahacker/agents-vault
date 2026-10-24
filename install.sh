#!/usr/bin/env bash
# Install agents-vault on an agent server (Linux or macOS). Idempotent: safe to
# re-run to upgrade; never replaces an existing vault, key file or sources file.
#
#   ./install.sh [--sources FILE] [--no-system-link] [--no-timer] [--no-rules] [--no-import] [--with-mcp]
#
#   --sources FILE    copy FILE to ~/.config/agents-vault/sources.toml (only if none exists yet)
#   --no-system-link  don't symlink /usr/local/bin/sec (needs sudo)
#   --no-timer        don't install the hourly import timer
#   --no-rules        don't add the "Secrets (agents vault)" section to agent instruction files
#   --no-import       don't run the first import
#   --with-mcp        install optional local MCP discovery tools in a dedicated Python venv
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SHARE_DIR="$HOME/.local/share/agents-vault"
BIN_DIR="$HOME/.local/bin"
SOURCES="" SYSLINK=1 TIMER=1 RULES=1 IMPORT=1 MCP=0
PLATFORM="$(uname -s)"
case "$PLATFORM" in
  Darwin)
    SYSLINK=0
    export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH:/Applications/KeePassXC.app/Contents/MacOS"
    ;;
  Linux) ;;
  *) echo "supported platforms: Linux and macOS" >&2; exit 1 ;;
esac

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sources) SOURCES="$2"; shift 2 ;;
    --no-system-link) SYSLINK=0; shift ;;
    --no-timer) TIMER=0; shift ;;
    --no-rules) RULES=0; shift ;;
    --no-import) IMPORT=0; shift ;;
    --with-mcp) MCP=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
done

step() { printf '\n== %s\n' "$*"; }

step "dependencies"
if [[ $PLATFORM == Darwin ]]; then
  if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' >/dev/null 2>&1; then
    command -v brew >/dev/null || { echo "install Homebrew and Python >= 3.11, then retry" >&2; exit 1; }
    brew install python
  fi
fi
if ! command -v keepassxc-cli >/dev/null; then
  if [[ $PLATFORM == Darwin ]]; then
    command -v brew >/dev/null || { echo "install KeePassXC or Homebrew, then retry" >&2; exit 1; }
    brew install --cask keepassxc
  elif command -v apt-get >/dev/null; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends keepassxc
  else
    echo "install keepassxc (keepassxc-cli >= 2.7) with your package manager, then re-run" >&2; exit 1
  fi
fi
python3 -c 'import sys, tomllib; sys.exit(sys.version_info < (3, 11))' \
  || { echo "python3 >= 3.11 required (tomllib)" >&2; exit 1; }
command -v keepassxc-cli >/dev/null || { echo "keepassxc-cli missing from PATH" >&2; exit 1; }
echo "keepassxc-cli $(keepassxc-cli --version), $(python3 --version)"

step "scripts"
install -d -m 755 "$BIN_DIR" "$SHARE_DIR"
install -m 755 "$REPO/bin/sec" "$BIN_DIR/sec"
install -m 755 "$REPO/lib/import.py" "$SHARE_DIR/import.py"
install -m 644 "$REPO/lib/common.py" "$REPO/lib/doctor.py" "$REPO/lib/launchd.py" "$SHARE_DIR/"
install -m 755 "$REPO/lib/setup.sh" "$SHARE_DIR/setup.sh"
for resource in templates examples systemd; do
  install -d -m 755 "$SHARE_DIR/$resource"
  install -m 644 "$REPO/$resource/"* "$SHARE_DIR/$resource/"
done
if [[ $PLATFORM == Darwin ]]; then
  # GUI-launched agents often lack Homebrew and the KeePassXC app in PATH.
  python3 - "$SHARE_DIR/runtime.sh" "$BIN_DIR" "$(dirname "$(command -v python3)")" "$(dirname "$(command -v keepassxc-cli)")" <<'PYTHON'
import pathlib, shlex, sys
prefix = ":".join(dict.fromkeys(sys.argv[2:]))
path = pathlib.Path(sys.argv[1])
path.write_text("export PATH=" + shlex.quote(prefix) + ':"$PATH"\n')
path.chmod(0o600)
PYTHON
fi
echo "sec -> $BIN_DIR/sec, importer -> $SHARE_DIR/import.py"
if [[ $MCP == 1 ]]; then
  step "optional MCP dependencies"
  python3 -m venv "$SHARE_DIR/mcp-venv" || {
    echo "Python venv support required; on apt systems install python3-venv, then retry" >&2
    exit 1
  }
  "$SHARE_DIR/mcp-venv/bin/python" -m pip install -r "$REPO/lib/mcp-requirements.txt"
fi
if [[ $MCP == 1 || -d $SHARE_DIR/mcp-venv ]]; then
  install -m 644 "$REPO/lib/mcp_server.py" "$SHARE_DIR/mcp_server.py"
  install -m 755 "$REPO/bin/sec-mcp" "$BIN_DIR/sec-mcp"
  echo "MCP launcher -> $BIN_DIR/sec-mcp (configure your client separately)"
fi
if [[ $SYSLINK == 1 ]]; then
  # Agents often run non-login shells whose PATH lacks ~/.local/bin.
  sudo ln -sf "$BIN_DIR/sec" /usr/local/bin/sec && echo "linked /usr/local/bin/sec"
fi

init_opts=()
[[ -z $SOURCES ]] || init_opts+=(--sources "$SOURCES")
[[ $TIMER == 1 ]] || init_opts+=(--no-timer)
[[ $RULES == 1 ]] || init_opts+=(--no-rules)
[[ $IMPORT == 1 ]] || init_opts+=(--no-import)
"$BIN_DIR/sec" init ${init_opts[@]+"${init_opts[@]}"}
