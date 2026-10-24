"""Generate the per-user macOS import schedule with correctly escaped paths."""
from pathlib import Path
import plistlib
import sys


def write_plist(destination, python, importer, vault, sources, path):
    job = {
        "Label": "local.agents-vault.import",
        "ProgramArguments": [python, importer],
        "StartInterval": 3600,
        "EnvironmentVariables": {"PATH": path, "SEC_VAULT_DIR": vault,
                                 "AGENTS_VAULT_SOURCES": sources},
        "StandardOutPath": str(Path(vault) / "import.log"),
        "StandardErrorPath": str(Path(vault) / "import-error.log"),
        "Umask": 0o077,
    }
    target = Path(destination)
    with target.open("wb") as stream:
        plistlib.dump(job, stream)
    target.chmod(0o600)


if __name__ == "__main__":
    write_plist(*sys.argv[1:])
