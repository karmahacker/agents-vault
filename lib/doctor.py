"""Read-only readiness checks. Never print vault contents or subprocess output."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys


def main():
    vault = Path(os.environ.get("SEC_VAULT_DIR", Path.home() / ".agents-vault"))
    problems = []
    if sys.version_info < (3, 11):
        problems.append("Python >= 3.11 is required")
    cli = shutil.which("keepassxc-cli")
    if not cli:
        problems.append("keepassxc-cli is missing from PATH")
    for name, mode in (("", 0o700), ("agents.kdbx", 0o600), ("agents.keyx", 0o600)):
        path = vault / name
        if not path.exists():
            problems.append(f"missing {name or 'vault directory'}; run sec init or restore existing files")
        elif stat.S_IMODE(path.stat().st_mode) != mode:
            problems.append(f"{name or 'vault directory'} must have mode {mode:o}")
    if not problems:
        try:
            subprocess.run([cli, "ls", "-q", "--no-password", "-k", str(vault / "agents.keyx"),
                            str(vault / "agents.kdbx")], capture_output=True, check=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            problems.append("vault cannot be opened with its key file")
    for problem in problems:
        print("FAIL:", problem)
    if not problems:
        print("ready: dependencies, vault permissions, and key-file access checked")
        print("Scheduler, agent instructions, source availability, and MCP clients require separate checks.")
    return bool(problems)


if __name__ == "__main__":
    sys.exit(main())
