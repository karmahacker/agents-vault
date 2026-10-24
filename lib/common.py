"""Shared dotenv parsing, backup retention, and command execution for sec."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

KEEP_BACKUPS = 200


def parse_env(text):
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not m:
            continue
        v = m.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[m.group(1)] = v
    return out


def backup(vault):
    """Called while holding the vault write lock."""
    backups = vault / "backups"
    target = backups / f"agents-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns()}.kdbx"
    shutil.copy2(vault / "agents.kdbx", target)
    # copy2 preserves the database mtime; retention uses backup creation names.
    for old in sorted(backups.glob("agents-*.kdbx"), reverse=True)[KEEP_BACKUPS:]:
        old.unlink()


def run(vault, args):
    env = os.environ.copy()
    key, db = str(vault / "agents.keyx"), str(vault / "agents.kdbx")
    while args and args[0] != "--":
        ref = args.pop(0)
        if ref.startswith("@"):
            command = ["attachment-export", "--stdout", "--no-password", "-k", key, db, ref[1:], ".env"]
        else:
            var, sep, entry = ref.partition("=")
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", var):
                sys.exit("sec: run: expected VAR=entry[:attr] or @entry")
            entry, attr = entry.rsplit(":", 1) if ":" in entry else (entry, "Password")
            command = ["show", "--no-password", "-k", key, "-a", attr, db, entry]
        result = subprocess.run(["keepassxc-cli", *command], capture_output=True)
        if result.returncode:
            sys.exit("sec: run: cannot read requested vault value or .env attachment")
        value = result.stdout.decode(errors="replace")
        if ref.startswith("@"):
            env.update(parse_env(value))
        else:
            env[var] = value.rstrip("\n")
    if args and args[0] == "--":
        args.pop(0)
    if not args:
        sys.exit("sec: run: missing command after --")
    try:
        os.execvpe(args[0], args, env)
    except (OSError, ValueError):
        sys.exit("sec: run: cannot execute command with requested environment")


if __name__ == "__main__":
    action, directory, *args = sys.argv[1:]
    if action == "backup":
        backup(Path(directory))
    elif action == "run":
        run(Path(directory), args)
    else:
        sys.exit("unknown action")
